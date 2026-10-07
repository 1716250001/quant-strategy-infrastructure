# -*- coding: utf-8 -*-
"""参数优化器（v0.5 V5-3；04 §8.5 扩展点 OPTIMIZER）。

`grid_search`：笛卡尔积网格 × 进程池并行回测 → 按 objective 排名。
    - 组合顺序确定（参数名与取值均排序 → itertools.product）；
    - 结果顺序 = 网格顺序（`executor.map` 保序），排名仅重排展示；
    - 每组合独立走 BTFRuntime 标准链路（load_config → build → run
      persist=False）——网格回测与单跑**同一条代码路径**（一致性验收）；
    - objective 缺失/NaN → 排末尾（诚实降级，不剔除记录）。
    - **RC-08（2026-10-05）**：每组合指标与 `bt run` 同口径——含基准相对
      项（`extras=rt._benchmark_metrics`）。未配 `report.benchmark` ⇒ extras
      返回空（不报错、不新增字段，与既有行为逐位一致）。
    - **RC-07（2026-10-05）**：`perturb=δ 列表` → 对 top1 的**数值型**参数
      生成 `p×(1±δ)` 邻域点（复用 deep_set + 同一 worker 链路），产物给
      极差 / 标准差 / **相对退化率**；退化 > 50% ⇒ `sensitive=True`。
      只把"参数是否敏感"变成**产物里可见的数字**，不替用户下结论。

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
    # R4 剩余：与 run()/verify_run() 共用同一指标计算单点；**RC-08
    # （2026-10-05）**：`extras` 与 `bt run` 同口径（`_benchmark_metrics`）
    # ——配了 `report.benchmark` ⇒ 每组合含 IR/超额；未配 ⇒ extras 返回空
    # （不报错、不新增字段，与既有行为逐位一致）。
    metrics = compute_run_metrics(result.snapshots, result.fills,
                                  rt._analysis_config(),
                                  extras=rt._benchmark_metrics)
    # RC-06（2026-10-05）：推断层输入——日收益的 `(T, 单期夏普, 偏度, 峰度)`
    # （纯统计，供 PSR/DSR；样本 < 3 或方差为 0 ⇒ None，**不阻断**网格）。
    returns_stats: dict[str, Any] | None = None
    try:
        from btf.analytics.inference import sharpe_moments
        from btf.analytics.metrics import build_nav_series

        series = build_nav_series(result.snapshots, result.fills)
        n, sr_period, skew, kurt = sharpe_moments(series.effective_returns())
        returns_stats = {"n_periods": n, "sr_period": sr_period,
                         "skew": skew, "kurt": kurt}
    except (ValueError, ZeroDivisionError, KeyError):
        returns_stats = None
    return {
        "params": params,
        "metrics": metrics,
        "n_days": result.n_days,
        "n_fills": len(result.fills),
        "returns_stats": returns_stats,
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


def _execute_payloads(
    payloads: Sequence[tuple[dict[str, Any], dict[str, Any]]],
    n_workers: int,
    config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """组合执行器（网格与邻域扫描**共用**；RC-07 复用同一进程池链路）。

    `n_workers <= 0` → 内联顺序执行（测试注入型 feed / 调试）；否则进程池
    + 跨组合共享预热（IO-7）。
    """
    if n_workers <= 0:
        return [_run_single(p) for p in payloads]
    warm = _warm_args(config)
    with ProcessPoolExecutor(
        max_workers=n_workers,
        initializer=_worker_warm if warm else None,
        initargs=(warm,) if warm else (),
    ) as pool:
        return list(pool.map(_run_single, payloads))


def _perturb_points(
    params: Mapping[str, Any], deltas: Sequence[float],
) -> list[tuple[str, int | float]]:
    """top1 参数 → 邻域扰动点（RC-07）：每参数每 δ 生成 `p×(1−δ)`/`p×(1+δ)`。

    - 非数值参数（str/bool/None）跳过——无可扰动的"数值邻域"；
    - 整型参数取**就近整数**（schema 拒小数；取整后与原值相同则跳过，
      如 `top_n=1` 的 ±10% 取整仍为 1）；
    - 同 (路径, 值) 去重（保序）——不同 δ 取整后可能撞值。
    """
    points: list[tuple[str, int | float]] = []
    seen: set[tuple[str, Any]] = set()
    for path, value in params.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        for delta in deltas:
            for raw in (value * (1.0 - delta), value * (1.0 + delta)):
                cand: int | float = (round(raw) if isinstance(value, int)
                                     else float(raw))
                if cand == value or (path, cand) in seen:
                    continue
                seen.add((path, cand))
                points.append((path, cand))
    return points


class GridSearchOptimizer:
    """网格搜索（进程池；B4 基准：1000 组 × B2 场景 < 30min）。

    workers=0 → **内联顺序执行**（不建进程池）：测试注入型 feed（进程内
    registry.register 的名字在 spawn worker 不可见）与调试路径；正式
    扫描用默认 8 进程。

    **短样本门禁边界（N14，19 号 §73.5 / §75.4——已知边界，非缺陷）**：
    CL-1 的短区间门禁（`MIN_OPTIMIZE_TRADING_DAYS` ＋ `--allow-short` ＋
    产物 `short_period_override` 留痕）**只在 CLI 层**（`bt optimize`）落地；
    **直呼本类 API 的调用方不受门禁** ⇒ 须**自担短样本风险**（短区间下部分
    指标"数学有定义、业务无意义"），且**不产 override 留痕**。如需同口径：
    经 `bt optimize`，或自行判 `report["n_trading_days"] >= 250`。
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
        n_trials_declared: int = 0,
        perturb: Sequence[float] | None = None,
    ) -> dict[str, Any]:
        """执行网格：config（已合并的配置 dict）+ grid（路径 → 取值列表）。

        grid 键为**点分配置路径**（如 ``run.params.top_n``）；取值为
        原语列表。返回 ``{objective, n_combos, results}``——results 按网格
        顺序（未排序），每项含 ``params / metrics / n_days / n_fills``
        与 ``rank``（objective 降序；NaN 末尾）。

        **RC-06（2026-10-05）**：另返回多重检验可见化字段——
        ``param_dim``（参数维度 k）/ ``n_trading_days`` / ``n_trials_declared``
        （用户声明的历史累计试验数）/ ``n_trials`` = 本次组合数 + 声明数；
        每项含 ``psr`` / ``dsr``（样本不足时为 ``None``），以及
        ``adjustment_note``（"N=… 未校正 —— 阈值应 ≤ 0.05/N"）。
        **排序与 rank 语义完全不变**（只加字段，不改口径）。

        **RC-08（2026-10-05）**：每组合 `metrics` 与 `bt run` 同口径——配
        `report.benchmark` 时含 `information_ratio` / `annualized_excess`
        等基准相对项；未配 ⇒ extras 返回空，不报错。

        **RC-07（2026-10-05）**：`perturb=[δ…]` ⇒ 返回 ``perturbation``
        块（top1 数值型参数的 p×(1±δ) 邻域扫描：`range` / `std` /
        `relative_degradation` / `sensitive`；未传 ⇒ ``None``）。
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
        results = _execute_payloads(payloads, n_workers, config)

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

        # RC-06（2026-10-05）：多重检验校正**可见化**——"试得越多越不可信"
        # 变成产物字段。N = 本次组合数 + 用户声明的历史累计试验数；
        # PSR 只看单个策略，**DSR 才是"挑出来"的夏普的正确显著性**。
        from btf.analytics.inference import deflated_sharpe, probabilistic_sharpe

        n_trials = len(combos) + max(0, int(n_trials_declared))
        for item in results:
            stats = item.get("returns_stats")
            if not stats:
                item["psr"] = None
                item["dsr"] = None
                continue
            n_periods = int(stats["n_periods"])
            item["psr"] = probabilistic_sharpe(
                stats["sr_period"], sr0=0.0, n_periods=n_periods,
                skew=stats["skew"], kurt=stats["kurt"])
            item["dsr"] = deflated_sharpe(
                stats["sr_period"], n_periods=n_periods, skew=stats["skew"],
                kurt=stats["kurt"], n_trials=n_trials)

        # RC-07（2026-10-05）：参数邻域扫描——只对 **top1 的数值型参数**做
        # `p×(1±δ)`（复用同一 deep_set + worker 链路）。产物只报**数字**
        # （极差/标准差/相对退化率）；"疑似过拟合"是终端提示，不替用户判定。
        perturbation: dict[str, Any] | None = None
        if perturb:
            deltas: list[float] = []
            for raw_delta in perturb:
                delta = float(raw_delta)
                if not 0.0 < delta < 1.0:
                    raise ValueError(
                        f"perturb δ 须在 (0, 1)，得 {raw_delta!r}")
                deltas.append(delta)
            top_item = ranked[0]
            base = objective_of(top_item)
            points = _perturb_points(top_item["params"], deltas)
            block_points: list[dict[str, Any]] = []
            if points:
                perturb_payloads = []
                for path, cand in points:
                    cfg = dict(config)
                    cfg = deep_set(cfg, path.split("."), cand)
                    perturb_payloads.append((cfg, {path: cand}))
                block_points = [
                    {"params": dict(r["params"]), "value": objective_of(r)}
                    for r in _execute_payloads(perturb_payloads, n_workers,
                                               config)
                ]
            values = [p["value"] for p in block_points
                      if p["value"] != float("-inf")]
            if points and values and base != float("-inf"):
                vmin, vmax = min(values), max(values)
                mean = sum(values) / len(values)
                std = (sum((v - mean) ** 2 for v in values)
                       / len(values)) ** 0.5
                # 相对退化率 = 邻域最差值相对 top1 的跌幅（≥0；top1 本身
                # 可能被邻域点反超 ⇒ 截断为 0）。base ≈ 0 时不可定义 ⇒ None。
                degradation = (max(0.0, base - vmin) / abs(base)
                               if abs(base) > 1e-12 else None)
                note = None
                sensitive = degradation is not None and degradation > 0.5
            else:
                vmin = vmax = mean = std = degradation = None
                sensitive = None
                note = ("top1 无数值型参数（或扰动量与原值相同）→ 未做邻域扫描"
                        if not points else
                        "邻域点无可比 objective（NaN/缺失）→ 未判定敏感")
            perturbation = {
                "deltas": deltas,
                "base_params": dict(top_item["params"]),
                "base_value": None if base == float("-inf") else base,
                "n_points": len(points),
                "threshold": 0.5,
                "objective_min": vmin,
                "objective_max": vmax,
                "objective_mean": mean,
                "range": (vmax - vmin) if vmin is not None else None,
                "std": std,
                "relative_degradation": degradation,
                "sensitive": sensitive,
                "note": note,
                "points": block_points,
            }

        return {
            "objective": objective,
            "n_combos": len(combos),
            "param_dim": len(paths),
            "n_trading_days": int(results[0]["n_days"]) if results else 0,
            "n_trials_declared": max(0, int(n_trials_declared)),
            "n_trials": n_trials,
            "adjustment_note": (
                f"N={n_trials} 未校正 —— 阈值应 ≤ 0.05/N = "
                f"{0.05 / n_trials:.4g}"),
            "workers": n_workers,
            "perturbation": perturbation,     # RC-07（未传 ⇒ None）
            "results": results,               # 网格顺序（含 rank / psr / dsr）
            "top": [
                {"rank": item["rank"], "params": item["params"],
                 "value": objective_of(item),
                 "psr": item.get("psr"), "dsr": item.get("dsr")}
                for item in ranked[:10]
            ],
        }
