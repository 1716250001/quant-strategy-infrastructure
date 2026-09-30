# -*- coding: utf-8 -*-
"""参数优化器（v0.5 V5-3；04 §8.5 扩展点 OPTIMIZER）。

`grid_search`：笛卡尔积网格 × 进程池并行回测 → 按 objective 排名。
    - 组合顺序确定（参数名与取值均排序 → itertools.product）；
    - 结果顺序 = 网格顺序（`executor.map` 保序），排名仅重排展示；
    - 每组合独立走 BTFRuntime 标准链路（load_config → build → run
      persist=False）——网格回测与单跑**同一条代码路径**（一致性验收）；
    - objective 缺失/NaN → 排末尾（诚实降级，不剔除记录）。

纪律：优化器**只编排不承载业务**（铁律 5）；worker 为模块级函数
（Windows spawn 可 pickle）。
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from itertools import product
from math import isnan
from typing import Any

from btf.domain.contracts import CONTRACT_VERSION

__all__ = ["GridSearchOptimizer", "deep_set"]


def deep_set(config: dict[str, Any], path: Sequence[str],
             value: Any) -> dict[str, Any]:
    """复制式深层写入（不变异输入）：`path` 逐级下钻建副本。"""
    out = dict(config)
    if len(path) == 1:
        out[path[0]] = value
        return out
    child = out.get(path[0])
    out[path[0]] = deep_set(child if isinstance(child, dict) else {},
                            path[1:], value)
    return out


def _worker_warm(payload: tuple[str, str, str]) -> None:
    """worker initializer（**每进程一次**）：预热跨组合共享的数据源（IO-7）。

    背景（P1-4 / 19 号附录 D.3）：原实现每组合在 worker 内 `BTFRuntime().build()`
    ——"1000 组 = 1000 次数据源实例化"（B4 外推 667s 曾被归因于重复加载）。
    **本轮实测校正**（§41.5 D-10）：单组合 build **0.39s** / run **1.67s**
    ⇒ 加载仅占 ≈19%，"主要由重复加载构成"**偏高**（同批已由 IO-3 的
    `corporate_actions` memo 与 BB-1 的装配期零成本纪律削掉主要部分）。

    本 initializer 只做**语义零风险**的跨组合共享预热：
    ① 触发重模块 import（pyarrow / runtime 链，进程级一次性）；
    ② 预热 `corporate_actions` 进程内 memo（键与原实现逐位一致的十年区间
       由调用方传入）——同 worker 后续组合直接命中（原每组合 ≈0.7s）。
    不做"父进程预载 + 传句柄"：spawn 下须 pickle 全部 Arrow 表（内存/时间
    反而恶化），且会引入跨进程共享可变状态（与"只读纪律"冲突）。
    """
    root, start_ymd, end_ymd = payload
    from btf.data.feed import TushareParquetFeed
    from btf.domain.types import TradingDate

    feed = TushareParquetFeed(root)
    feed.corporate_actions(TradingDate.from_ymd(start_ymd),
                           TradingDate.from_ymd(end_ymd))


def _run_single(payload: tuple[dict[str, Any], dict[str, Any]]
                ) -> dict[str, Any]:
    """worker：单组合回测（模块级——spawn 可 pickle；与单跑同链路）。

    payload = (已注入网格参数的配置 dict, 参数组合)；组合经 tuple 回传
    （不写进配置——root schema `additionalProperties: false`）。
    """
    from btf.analytics.run_metrics import compute_run_metrics
    from btf.runtime import BTFRuntime

    config, params = payload
    rt = BTFRuntime().load_config(config).build()
    result = rt.run(persist=False)
    # R4 剩余：与 run()/verify_run() 共用同一指标计算单点（不传 extras ⇒ 网格
    # 组合**不计算基准相对指标**，与既有行为逐位一致）
    metrics = compute_run_metrics(result.snapshots, result.fills,
                                  rt._analysis_config())
    return {
        "params": params,
        "metrics": metrics,
        "n_days": result.n_days,
        "n_fills": len(result.fills),
    }


def _warm_args(config: Mapping[str, Any]) -> tuple[str, str, str] | None:
    """从配置推导预热参数（root/区间）；推导不出则跳过预热（不阻断）。"""
    data = config.get("data") or {}
    run = config.get("run") or {}
    period = run.get("period") or {}
    if str(data.get("feed", "")) not in ("tushare_parquet", "mixed"):
        return None
    root = (data.get("feed_params") or {}).get("root")
    start, end = period.get("start"), period.get("end")
    if not root or not start or not end:
        return None
    return str(root), str(start).replace("-", ""), str(end).replace("-", "")


class GridSearchOptimizer:
    """网格搜索（进程池；B4 基准：1000 组 × B2 场景 < 30min）。

    workers=0 → **内联顺序执行**（不建进程池）：测试注入型 feed（进程内
    registry.register 的名字在 spawn worker 不可见）与调试路径；正式
    扫描用默认 8 进程。
    """

    contract_version = CONTRACT_VERSION

    def __init__(self, workers: int = 8):
        if workers < 0:
            raise ValueError(f"workers 须 ≥ 0（0=内联），得 {workers!r}")
        self.workers = int(workers)

    def run(
        self,
        config: Mapping[str, Any],
        grid: Mapping[str, Sequence[Any]],
        objective: str = "sharpe_ratio",
        workers: int | None = None,
    ) -> dict[str, Any]:
        """执行网格：config（已合并的配置 dict）+ grid（路径 → 取值列表）。

        grid 键为**点分配置路径**（如 ``run.params.top_n``）；取值为
        原语列表。返回 ``{objective, n_combos, results}``——results 按网格
        顺序（未排序），每项含 ``params / metrics / n_days / n_fills``
        与 ``rank``（objective 降序；NaN 末尾）。
        """
        paths = sorted(grid)
        if not paths:
            raise ValueError("grid 为空（至少一个参数位）")
        value_lists = [list(grid[p]) for p in paths]
        if any(not values for values in value_lists):
            raise ValueError("grid 参数取值列表不得为空")

        combos = [
            dict(zip(paths, values, strict=True))
            for values in product(*value_lists)
        ]
        payloads = []
        for combo in combos:
            cfg = dict(config)
            for path, value in combo.items():
                cfg = deep_set(cfg, path.split("."), value)
            payloads.append((cfg, combo))

        n_workers = workers if workers is not None else self.workers
        if n_workers <= 0:                      # 内联（测试/调试）
            results = [_run_single(p) for p in payloads]
        else:
            warm = _warm_args(config)           # IO-7：跨组合共享预热（可选）
            with ProcessPoolExecutor(
                max_workers=n_workers,
                initializer=_worker_warm if warm else None,
                initargs=(warm,) if warm else (),
            ) as pool:
                results = list(pool.map(_run_single, payloads))

        def objective_of(item: dict[str, Any]) -> float:
            value = item["metrics"].get(objective)
            try:
                value = float(value)
            except (TypeError, ValueError):
                return float("-inf")
            return float("-inf") if isnan(value) else value

        ranked = sorted(results, key=lambda item: -objective_of(item))
        for rank, item in enumerate(ranked, start=1):
            item["rank"] = rank
        return {
            "objective": objective,
            "n_combos": len(combos),
            "workers": n_workers,
            "results": results,               # 网格顺序（含 rank 字段）
            "top": [
                {"rank": item["rank"], "params": item["params"],
                 "value": objective_of(item)} for item in ranked[:10]
            ],
        }
