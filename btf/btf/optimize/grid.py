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

import logging
import os
import weakref
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from itertools import product
from math import isnan
from pathlib import Path
from typing import Any

from btf.domain.contracts import CONTRACT_VERSION

__all__ = ["GridSearchOptimizer", "deep_set"]

logger = logging.getLogger(__name__)

#: P-7：`daily` 的 9 列（与 feed.COLUMNS 逐字同序；键含列序）
_DAILY_COLS = ("ts_code", "trade_date", "open", "high", "low", "close",
               "pre_close", "vol", "amount")
#: P-7b（19 号 §236）：shm 共享表规格（表 → 列集；**列序取主调用方口径**）。
#: `daily` 9 列与 `feed.COLUMNS` 逐字同序；`adj_factor`/`fund_adj` 与质检
#: `_table_slice`／`read_codes` 的请求同序。读层 `_read` 对"列集为规格子集"的
#: 请求做 `select` 投影，故 `symbol_range` 的
#: `("adj_factor","ts_code","trade_date")` 列序也能命中（不白建）。
_SHM_TABLE_SPECS: dict[str, tuple[str, ...]] = {
    "daily": _DAILY_COLS,
    "adj_factor": ("ts_code", "trade_date", "adj_factor"),
    "fund_adj": ("ts_code", "trade_date", "adj_factor"),
}
#: 估算字节/行（`num_rows × per_row`；parquet `total_byte_size` 是**压缩后**，
#: 不可用于内存估算，§225-A）。`daily` 实测 ≈71 B/行；`adj_factor` 420 MB ÷
#: 8.75 M 行 ≈ 48 B/行（`fund_adj` 同构）。
_SHM_BYTES_PER_ROW: dict[str, int] = {"daily": 72, "adj_factor": 48, "fund_adj": 48}
_SHM_MAX_TOTAL_YEARS = 15
_SHM_MAX_TABLE_BYTES = 200 * 1024 * 1024
_SHM_MIN_YEARS = 8
_SHM_MIN_BYTES = 700 * 1024 * 1024


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


def _worker_setup(warm: tuple[str, str, str] | None,
                  shm_spec: Mapping[str, Any] | None = None) -> None:
    """worker initializer（**无条件执行**）：预算 + 可选预热 +（P-7）shm attach。

    P1″-7（19 号 §219-C/§220-B）：`READ_CACHE_MB=256` 必须在这里无条件设置。
    原实现写在 `_worker_warm` 内，而挂载是 `initializer=_worker_warm if warm
    else None`；实测 `_warm_args(a1 配置) = None`（无 `data.feed_params.root`）
    ⇒ initializer 从不挂载 ⇒ 256 从不设置 ⇒ N=6 每 worker 各持 1024 MB
    （额外 ≈6.4 GB，超可用内存）。
    """
    os.environ["READ_CACHE_MB"] = "256"
    if warm:
        _worker_warm(warm)                        # 原预热逻辑一字不动
    if shm_spec:
        _worker_attach_shm(shm_spec)               # P-7：失败即降级（不抛出）


class _ShmSentinel:
    """weakref-able 哨兵（`object()` 不支持弱引用，`weakref.finalize` 会 TypeError）。"""


_SHM_KEPT: list[tuple[Any, Any, Any]] = []
_SHM_SENTINEL = _ShmSentinel()
_SHM_FINALIZER: Any = None


def _release_shm_tables() -> None:
    """worker 退出：先清登记表 → `del` 表/缓冲 → `shm.close()`（§214 BufferError）。"""
    from btf.data import parquet_reader as pr

    pr.clear_shm_tables()
    while _SHM_KEPT:
        shm, buf, table = _SHM_KEPT.pop()
        del table, buf
        try:
            shm.close()
        except Exception:
            pass


def _worker_attach_shm(spec: Mapping[str, Any]) -> None:
    """worker：attach 父进程共享的年表（**零拷贝只读**；P-7b 多表）。

    释放顺序（§214 已复现 `BufferError`）：先清登记表 → `del` 表/缓冲 →
    `shm.close()`；本进程**不 unlink**（由父进程 `try/finally` 统一回收）。
    """
    from multiprocessing import shared_memory

    import pyarrow as pa
    import pyarrow.ipc as ipc

    from btf.data import parquet_reader as pr

    global _SHM_FINALIZER
    root = Path(spec["root"])
    try:
        for item in spec.get("tables") or ():
            table_name = str(item["table"])
            year = int(item["year"])
            cols = tuple(item["cols"])
            shm = shared_memory.SharedMemory(name=str(item["name"]))
            buf = pa.py_buffer(shm.buf)
            table = ipc.open_stream(buf).read_all()
            pr.register_shm_table(root / table_name / f"{year}.parquet",
                                  cols, table)
            _SHM_KEPT.append((shm, buf, table))
        if _SHM_FINALIZER is None:
            _SHM_FINALIZER = weakref.finalize(_SHM_SENTINEL, _release_shm_tables)
    except Exception as exc:                      # 整体降级：清掉半成品
        _release_shm_tables()
        logger.warning("shm attach 失败 → 降级本地缓存：%s", exc)


def _run_single(payload: tuple[dict[str, Any], dict[str, Any]]
                ) -> dict[str, Any]:
    """worker：单组合回测（模块级——spawn 可 pickle；与单跑同链路）。

    payload = (已注入网格参数的配置 dict, 参数组合)；组合经 tuple 回传
    （不写进配置——root schema `additionalProperties: false`）。
    """
    from btf.analytics.run_metrics import compute_run_metrics
    from btf.analytics.trades import normalize_consolidations  # R61′
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
                                  extras=rt._benchmark_metrics,
                                  # R61′（N63）：逐笔口径与落盘路径同源
                                  consolidations=normalize_consolidations(
                                      getattr(result, "consolidations", ())))
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


def _shm_plan(config: Mapping[str, Any]) -> tuple[Path, list[int]] | None:
    """推导待共享的年文件集合（`run.period` 年范围 −1 缓冲年；P-7b 多表）。"""
    if os.environ.get("SHM_DISABLE", "").strip() not in ("", "0"):
        logger.warning("shm：SHM_DISABLE 已设置 → 降级为本地缓存")
        return None
    data = config.get("data") or {}
    if str(data.get("feed", "")) not in ("tushare_parquet", "mixed"):
        return None
    root = (data.get("feed_params") or {}).get("root")
    if root:
        root = Path(root)
    else:
        from btf.config.paths import MARKET_DATA_DIR

        root = Path(MARKET_DATA_DIR)
    period = (config.get("run") or {}).get("period") or {}
    start, end = period.get("start"), period.get("end")
    if not start or not end:
        return None
    y0, y1 = int(str(start)[:4]) - 1, int(str(end)[:4])
    years = list(range(y0, y1 + 1))
    if len(years) > _SHM_MAX_TOTAL_YEARS:
        logger.warning("shm：年份数 %d > %d → 降级", len(years),
                       _SHM_MAX_TOTAL_YEARS)
        return None
    # P-7″（19 号 §224-B）：小配置启用 shm 是**净损失**（父进程预读+序列化
    # 0.6 s > 省下的读盘；ETF 实测 +19%/+3.4 GB）。两道门槛同时满足才启用：
    # 年份 ≥8，且**各表合计**估算字节 >700 MB（只读元数据，不真读表）。
    if len(years) < _SHM_MIN_YEARS:
        logger.warning("shm：跳过（年份 %d < %d）", len(years), _SHM_MIN_YEARS)
        return None
    import pyarrow.parquet as pq

    # 估算口径：parquet 元数据的 `total_byte_size` 是**压缩后**（a1 12 年 daily
    # 只 368 MB，实际内存 1,008.7 MB）；改用「行数 × 每行字节」（§225-A）。
    est = 0
    for table in _SHM_TABLE_SPECS:
        per_row = _SHM_BYTES_PER_ROW[table]
        for y in years:
            p = root / table / f"{y}.parquet"
            if p.is_file():
                est += int(pq.read_metadata(p).num_rows) * per_row
    if est <= _SHM_MIN_BYTES:
        logger.warning("shm：跳过（年份 %d、预计 %.0f MB < %d MB）",
                       len(years), est / 1024**2, _SHM_MIN_BYTES // 1024**2)
        return None
    return root, years


def _shm_create(config: Mapping[str, Any]) -> tuple[dict[str, Any] | None, list[Any]]:
    """父进程：按 `_SHM_TABLE_SPECS` 序列化各表年文件并直写 shm。

    P-7b（19 号 §236）：单年预估 >200 MB ⇒ **该表整体跳过**（不拖累其它表，
    红线 3）；该表创建异常 ⇒ 仅该表降级；全部失败 ⇒ 整体回退本地缓存。
    """
    plan = _shm_plan(config)
    if plan is None:
        return None, []
    root, years = plan
    from multiprocessing import shared_memory

    import pyarrow as pa
    import pyarrow.ipc as ipc
    import pyarrow.parquet as pq

    spec: dict[str, Any] = {"root": str(root.resolve()), "tables": []}
    kept: list[Any] = []

    def _drop(segs: list[Any]) -> None:
        for shm in segs:
            try:
                kept.remove(shm)
            except ValueError:
                pass
            _shm_destroy([shm])

    for table, cols in _SHM_TABLE_SPECS.items():
        per_row = _SHM_BYTES_PER_ROW[table]
        rows: list[int] = []
        too_big: int | None = None
        for y in years:
            p = root / table / f"{y}.parquet"
            if not p.is_file():
                continue
            if int(pq.read_metadata(p).num_rows) * per_row > _SHM_MAX_TABLE_BYTES:
                too_big = y
                break
            rows.append(y)
        if too_big is not None:
            logger.warning("shm：%s/%d 预估 >200 MB → 该表跳过（其它表照常）",
                           table, too_big)
            continue
        if not rows:
            continue
        segs: list[Any] = []
        try:
            for year in rows:
                path = root / table / f"{year}.parquet"
                table_obj = pq.read_table(path, columns=list(cols))
                # 先预演一次得到精确字节数（shm 段尺寸必须给定）；再原地直写
                #（预演与直写逐字节一致：同一 schema 与同一表）
                pre = pa.BufferOutputStream()
                with ipc.new_stream(pre, table_obj.schema) as w:
                    w.write_table(table_obj)
                size = int(pre.getvalue().size)
                name = f"btf_{table}_{year}_{os.getpid()}_{len(kept)}"
                shm = shared_memory.SharedMemory(name=name, create=True, size=size)
                kept.append(shm)
                writer = pa.FixedSizeBufferWriter(pa.py_buffer(shm.buf))
                with ipc.new_stream(writer, table_obj.schema) as w:
                    w.write_table(table_obj)
                segs.append(shm)
                spec["tables"].append({"table": table, "year": year,
                                       "name": name, "size": size,
                                       "cols": list(cols)})
        except Exception as exc:
            logger.warning("shm：%s 创建失败 → 该表降级：%s", table, exc)
            _drop(segs)
            spec["tables"] = [it for it in spec["tables"] if it["table"] != table]
            continue
    if not spec["tables"]:
        logger.warning("shm 创建失败 → 降级为本地缓存（无可用年文件）")
        _shm_destroy(kept)
        return None, []
    by_table: dict[str, int] = {}
    for it in spec["tables"]:
        by_table[it["table"]] = by_table.get(it["table"], 0) + 1
    logger.warning(
        "shm：启用 —— 共享 %d 表 / %d 段 / %.1f MB（%s；只读零拷贝）",
        len(by_table), len(spec["tables"]),
        sum(it["size"] for it in spec["tables"]) / 1024**2,
        "、".join(f"{k} {v} 年" for k, v in by_table.items()))
    return spec, kept


def _shm_destroy(handles: Sequence[Any]) -> None:
    """父进程：`close + unlink`（`try/finally` 保证；异常退出才可能残留）。"""
    for shm in handles:
        try:
            shm.close()
        except Exception:
            pass
        try:
            shm.unlink()
        except Exception:
            pass


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
    shm_spec, shm_handles = _shm_create(config)
    try:
        with ProcessPoolExecutor(
            max_workers=n_workers,
            initializer=_worker_setup,
            initargs=(warm, shm_spec),
        ) as pool:
            return list(pool.map(_run_single, payloads))
    finally:
        _shm_destroy(shm_handles)


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
