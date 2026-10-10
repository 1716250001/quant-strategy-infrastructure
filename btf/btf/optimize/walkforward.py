# -*- coding: utf-8 -*-
"""Walk-Forward 滚动验证（**RC-15**，2026-10-05；派单 §5）。

纪律（照录派单，逐条落地）：
    - **强制时间顺序**：窗口切分只用**交易日历**（`days` 由调用方以
      `trade_cal` / Feed 自带日历解析后传入）——**禁止随机划分**
      （本模块全文件无随机源，可被静态审查）；
    - **支持 purge + embargo**（交易日粒度间隔，防训练/测试边界泄漏）：
      `test_start = train_end + 1 + purge + embargo`；
      purge 语义=训练端贴近边界的样本弃用，embargo 语义=测试端前的缓冲带，
      二者在本实现中**同为交易日间隔**（合并计入 gap；如需差异语义须显式扩展）；
    - **逐折 OOS 指标全量输出**（含最差折）——`folds` 数组不裁剪、不排序，
      `summary` 同时给 mean/median/**min**/**max**（"不得只报最优"）；
    - 每折**只走标准装配与执行**（`BTFRuntime.load_config→build→run`，与单跑
      同一条代码路径），指标经 `analytics.run_metrics.compute_run_metrics`
      **单点**计算（不引入第二套口径）。

产物（`run_walk_forward()` 返回并可落 JSON）::

    {spec, n_folds, folds:[{index, train:{start,end,n_days},
                            test:{start,end,n_days}, run_id,
                            train_metrics, test_metrics, warmup_bars,
                            exec_range, metric_range}],
     summary:{<metric>:{n,mean,median,min,max,stdev}}, warmup_note, note}

**R58′（N60，2026-10-09）——warm-up 语义（19 号 §107.3，原样落实）**：

    1. **热身只喂历史、不计绩效**：OOS 折 `run.period` 起点提前 `warmup` 个
       **交易日**（由 `days` 序列取，**不得**用自然日估算）；`period.end` 恒等
       于 `window.test_end`；
    2. **指标隔离（硬要求）**：`_metrics_of(test_start=…)` 先丢弃日期
       < `test_start` 的 `snapshots`/`fills` 再算——**只放宽 period 不改口径
       ⇒ IS 绩效混入 OOS**（比无 warm-up 更糟，验收不通过）；
    3. **状态延续视为合法**：热身段持仓可带入 test 段（真实交易语义），
       随折披露 `exec_range`（含热身）与 `metric_range`（仅 test）之差集；
    4. **默认＝auto**（按策略声明 `lookback_bars` 推导；未声明 ⇒
       `DEFAULT_WARMUP_BARS`=252 ＋ **WARN**，**禁静默退化 0**）；
       `--warmup 0` 保留为**旧行为对照开关**；
    5. **不留静默**：warm-up 生效但无法推导（未声明）时**显式告警**。

落盘纪律：每折的 `manifest` 经**装配披露**记录「折号 / 训练窗 / 测试窗 /
参数」（`assembly_notes`）——"训练/测试区间入 manifest"由此成立（09 #11）。
"""
from __future__ import annotations

import importlib
import logging
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from btf.domain.types import TradingDate

__all__ = ["DEFAULT_WARMUP_BARS", "Window", "declared_lookback",
           "run_walk_forward", "split_windows"]

logger = logging.getLogger(__name__)

#: **R58′（N60，2026-10-09）默认 warm-up（交易日）**：策略**未声明 lookback** 时的
#: 保守默认（≈1 交易年）。N60 的危害恰发生在**默认用法**下（长 lookback 策略
#: OOS 系统性假阴性：510880 实例 12 笔被压成 1 笔）⇒ **默认档不得再退化为 0**。
DEFAULT_WARMUP_BARS = 252

#: 策略**声明所需历史 bar 数**的约定属性名（duck-typing：类属性或返回 int 的可调用；
#: 只读，不装配、不运行）。
_WARMUP_DECL_ATTRS = ("lookback_bars", "required_lookback_bars",
                      "min_history_bars")


def declared_lookback(config: Mapping[str, Any]) -> int | None:
    """读 `run.strategy` **声明的**所需历史 bar 数（R58′；未声明 ⇒ `None`）。

    约定：策略类声明 `lookback_bars`（int；或 `lookback_bars()` 返回 int）；
    本函数只读配置 + 类属性——**不装配、不运行引擎**（零副作用）。
    未声明 ⇒ 调用方按 `DEFAULT_WARMUP_BARS` 保守默认 **并 WARN**（禁静默）。
    """
    run_cfg = config.get("run") or {}
    module_name, _, class_name = str(run_cfg.get("strategy") or "").partition(":")
    if not module_name or not class_name:
        return None
    try:
        cls = getattr(importlib.import_module(module_name), class_name)
    except (ImportError, AttributeError):
        return None
    for attr in _WARMUP_DECL_ATTRS:
        val = getattr(cls, attr, None)
        if val is None:
            continue
        if callable(val):
            try:
                val = val()
            except Exception:                       # 声明不可用 ⇒ 视为未声明
                continue
        if isinstance(val, int) and not isinstance(val, bool) and val > 0:
            return int(val)
    return None


def _resolve_warmup(config: Mapping[str, Any],
                    explicit: int | None) -> tuple[int, str]:
    """→ `(warmup_bars, source)`；source ∈ `explicit`／`auto:strategy`／
    `auto:default_252`／`legacy_zero`（§107.3.2-4/5）。"""
    if explicit is not None:
        n = int(explicit)
        if n < 0:
            raise ValueError(f"warmup 须 ≥ 0，得 {explicit!r}")
        return (n, "legacy_zero" if n == 0 else "explicit")
    look = declared_lookback(config)
    if look is not None:
        return (look, "auto:strategy")
    return (DEFAULT_WARMUP_BARS, "auto:default_252")

#: 汇总统计的候选指标键（逐折交集存在者才进 summary；含基准项——配基准时）
_SUMMARY_KEYS = (
    "sharpe_ratio", "annualized_return", "total_return", "max_drawdown",
    "calmar_ratio", "information_ratio", "annualized_excess",
    "excess_return", "n_fills", "n_trading_days",
)


@dataclass(frozen=True)
class Window:
    """一折的窗口（闭区间交易日；`train` 为 IS 段，`test` 为 OOS 段）。"""

    index: int
    train_start: TradingDate
    train_end: TradingDate
    test_start: TradingDate
    test_end: TradingDate

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "train": {"start": self.train_start.to_ymd(),
                      "end": self.train_end.to_ymd()},
            "test": {"start": self.test_start.to_ymd(),
                     "end": self.test_end.to_ymd()},
        }


def split_windows(
    days: Sequence[TradingDate],
    *,
    train: int,
    test: int,
    step: int | None = None,
    mode: str = "rolling",
    purge: int = 0,
    embargo: int = 0,
) -> list[Window]:
    """交易日历 → 窗口列表（**时间顺序**；rolling / anchored）。

    参数：
        days   升序交易日序列（唯一真源；**不得**用自然月/随机划分）；
        train  训练窗长（交易日）；`test` 测试窗长；`step` 滚动步长（缺省 = test）；
        mode   `rolling`（训练窗随折滑动）/ `anchored`（训练窗起点锚定首日、逐折加长）；
        purge / embargo  训练末与测试首之间的间隔交易日（合并为 gap）。

    语义：第 i 折（0 基）——`train_end_idx = i*step + train - 1`；
    `test_start_idx = train_end_idx + 1 + purge + embargo`；
    窗口须完整落在 `days` 内，否则该折（及其后）不生成。
    """
    if mode not in ("rolling", "anchored"):
        raise ValueError(f"mode 须为 rolling|anchored，得 {mode!r}")
    if train < 1 or test < 1:
        raise ValueError(f"train/test 须 ≥ 1，得 train={train!r}, test={test!r}")
    if step is not None and step < 1:
        raise ValueError(f"step 须 ≥ 1，得 {step!r}")
    if purge < 0 or embargo < 0:
        raise ValueError(f"purge/embargo 须 ≥ 0，得 {purge!r}/{embargo!r}")
    step = test if step is None else step
    gap = purge + embargo
    n = len(days)
    out: list[Window] = []
    i = 0
    while True:
        train_end_idx = i * step + train - 1
        test_start_idx = train_end_idx + 1 + gap
        test_end_idx = test_start_idx + test - 1
        if test_end_idx >= n:
            break
        train_start_idx = 0 if mode == "anchored" else i * step
        out.append(Window(
            index=len(out) + 1,
            train_start=days[train_start_idx],
            train_end=days[train_end_idx],
            test_start=days[test_start_idx],
            test_end=days[test_end_idx],
        ))
        i += 1
    if not out:
        raise ValueError(
            f"窗口不足：交易日 {n} 天装不下 train={train}+gap={gap}+test={test}"
            f"（mode={mode}）——请缩短窗口或扩大回测区间")
    return out


def _iso(day: TradingDate) -> str:
    """TradingDate → `YYYY-MM-DD`（配置 schema 的日期形制）。"""
    ymd = day.to_ymd()
    return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"


def _fold_period(config: Mapping[str, Any], window: Window,
                 which: str, *, warm_start: TradingDate | None = None
                 ) -> dict[str, Any]:
    """配置 → 指定段的 period 覆盖（浅复制；`run.period` 单点替换）。

    **R58′（N60）**：`which="test"` 且给 `warm_start` 时——**只提前起点**给策略
    热身，`period.end` 恒等于 `window.test_end`（IS 段 period 不变；禁止 train
    吸收 warm-up）。
    """
    if which == "train":
        start, end = window.train_start, window.train_end
    else:
        start = window.test_start if warm_start is None else warm_start
        end = window.test_end
    cfg = dict(config)
    run_cfg = dict(cfg.get("run") or {})
    run_cfg["period"] = {"start": _iso(start), "end": _iso(end)}
    cfg["run"] = run_cfg
    return cfg


def _metrics_of(rt: Any, result: Any, *,
                test_start: TradingDate | None = None) -> dict[str, float]:
    """折内指标——与 `bt run` 同一单点（含基准相对项与名字表）。

    **R58′（N60）硬要求——指标隔离**：给 `test_start` 时，**先丢弃日期
    < `test_start` 的 `snapshots`/`fills`** 再交 `compute_run_metrics`——
    热身段只喂历史、**不计绩效**；只放宽 period 而不改指标口径会把 IS 段
    绩效混入 OOS（判为验收不通过）。
    """
    from btf.analytics.run_metrics import compute_run_metrics
    from btf.analytics.trades import normalize_consolidations  # R61′

    snaps: Sequence[Any] = result.snapshots
    fills: Sequence[Any] = result.fills
    if test_start is not None:
        lo = test_start.to_ymd()
        snaps = [s for s in snaps if s.date.to_ymd() >= lo]
        fills = [f for f in fills if f.fill_date.to_ymd() >= lo]
    return compute_run_metrics(
        snaps, fills, rt._analysis_config(),
        analyzer_names=rt._analyzer_names(),
        extras=rt._benchmark_metrics,
        # R61′（N63）：逐笔口径与落盘路径同源（折算早于窗口起点的
        # 事件对空队列是 no-op，无需额外过滤）
        consolidations=normalize_consolidations(
            getattr(result, "consolidations", ())),
    )


def run_walk_forward(
    config: Mapping[str, Any],
    days: Sequence[TradingDate],
    *,
    train: int,
    test: int,
    step: int | None = None,
    mode: str = "rolling",
    purge: int = 0,
    embargo: int = 0,
    persist: bool = True,
    store: Any = None,
    include_is: bool = True,
    warmup: int | None = None,
) -> dict[str, Any]:
    """逐折执行 Walk-Forward（**每折走标准装配与执行**）。

    `persist=True` 时每折经 RunStore 落盘（`store` 可注入），且**窗口信息
    进该折 manifest 的装配披露**（09 #11）；`persist=False` 为纯内存模式
    （快，但无 manifest/报告——披露落在返回结构里）。
    `include_is=False` 跳过训练段回测（省一半算力；IS 指标列缺省为 null）。

    **R58′（N60）warm-up**：`warmup=None`（默认）＝**auto**——按策略声明的
    `lookback_bars` 推导；未声明 ⇒ `DEFAULT_WARMUP_BARS`（252）＋ **WARN**
    （禁退化为 0 静默）。`warmup=0` ＝**旧行为对照档**（同 WARN 提示）。
    OOS 折的 `run.period` 起点提前 `warmup` 个**交易日**（由 `days` 序列取），
    **终点不变**；**指标只在 test 段计算**（`_metrics_of(test_start=…)` 硬隔离）；
    热身段持仓可带入（真实语义），`exec_range`／`metric_range` 差集随折披露。
    """
    from btf.runtime import BTFRuntime

    warm_bars, warm_src = _resolve_warmup(config, warmup)
    notes: list[str] = []
    if warm_src == "auto:default_252":
        notes.append(
            "策略未声明 lookback（`lookback_bars`）⇒ OOS 折 warm-up 采用**保守默认** "
            f"{DEFAULT_WARMUP_BARS} 交易日（warmup_source=auto:default_252）；"
            "可用 `--warmup N` 显式指定（`--warmup 0` 复现旧行为）")
        logger.warning("walk-forward：%s", notes[-1])
    elif warm_src == "legacy_zero":
        notes.append(
            "warm-up 显式关闭（`--warmup 0`）＝**旧行为对照档**——长 lookback 策略"
            "可能样本外假阴性（N60），仅用于回归比对")
        logger.warning("walk-forward：%s", notes[-1])

    windows = split_windows(days, train=train, test=test, step=step, mode=mode,
                            purge=purge, embargo=embargo)
    step_eff = test if step is None else step
    idx_of = {d.to_ymd(): i for i, d in enumerate(days)}     # 交易日粒度反查
    folds: list[dict[str, Any]] = []
    for w in windows:
        fold: dict[str, Any] = {"index": w.index, **{k: v for k, v in
                                                     w.as_dict().items()
                                                     if k != "index"}}
        ts_ymd, te_ymd = w.test_start.to_ymd(), w.test_end.to_ymd()
        ts_idx = idx_of.get(ts_ymd, 0)
        te_idx = idx_of.get(te_ymd, ts_idx)
        eff_warm = min(warm_bars, max(0, ts_idx))            # 历史不足 ⇒ 取可用
        if eff_warm < warm_bars:
            notes.append(
                f"折 {w.index}：可用历史 {ts_idx} 日 < 请求 warm-up {warm_bars}"
                f" ⇒ 实际取 {eff_warm} 日（已披露）")
        warm_start = days[ts_idx - eff_warm] if eff_warm else w.test_start
        fold["warmup_bars"] = eff_warm
        fold["exec_range"] = {
            "start": warm_start.to_ymd(), "end": te_ymd,
            "n_days": te_idx - (ts_idx - eff_warm) + 1}
        fold["metric_range"] = {
            "start": ts_ymd, "end": te_ymd, "n_days": te_idx - ts_idx + 1}
        note = (
            f"Walk-Forward 折 {w.index}/{len(windows)}（RC-15）："
            f"训练窗 {w.train_start.to_ymd()}~{w.train_end.to_ymd()} / "
            f"测试窗 {ts_ymd}~{te_ymd}"
            f"（mode={mode}, window={train}, test={test}, step={step_eff}, "
            f"purge={purge}, embargo={embargo}, warmup={eff_warm}"
            f"[{warm_src}]）")
        if include_is:
            rt_tr = BTFRuntime().load_config(
                _fold_period(config, w, "train")).build()
            rt_tr.assembly_notes.append(note + "｜段=训练（IS）")
            res_tr = rt_tr.run(persist=False)
            fold["train_metrics"] = _metrics_of(rt_tr, res_tr)
        else:
            fold["train_metrics"] = None

        rt_te = BTFRuntime().load_config(
            _fold_period(config, w, "test", warm_start=warm_start)).build()
        rt_te.assembly_notes.append(
            note + f"｜段=测试（OOS，判定只看本段；exec 含热身 {eff_warm} 日，"
                   f"指标口径仅 {ts_ymd}~{te_ymd}）")
        res_te = rt_te.run(store=store, persist=persist)
        fold["test_metrics"] = _metrics_of(rt_te, res_te,
                                           test_start=w.test_start)
        fold["run_id"] = getattr(res_te, "run_id", None)
        folds.append(fold)

    summary: dict[str, dict[str, float]] = {}
    for key in _SUMMARY_KEYS:
        vals = [f["test_metrics"].get(key) for f in folds]
        vals = [float(v) for v in vals
                if isinstance(v, (int, float)) and v == v]
        if not vals:
            continue
        summary[key] = {
            "n": len(vals),
            "mean": statistics.fmean(vals),
            "median": statistics.median(vals),
            "min": min(vals),          # ← "不得只报最优"：最差折在册
            "max": max(vals),
            "stdev": statistics.stdev(vals) if len(vals) > 1 else 0.0,
        }
    return {
        "spec": {"mode": mode, "window": train, "test": test,
                 "step": step_eff, "purge": purge, "embargo": embargo,
                 "persist": persist, "include_is": include_is,
                 "warmup": warm_bars, "warmup_source": warm_src,
                 "warmup_requested": warmup},
        "n_folds": len(windows),
        "folds": folds,
        "summary": summary,
        "warmup_note": "；".join(notes) or None,
        "note": ("逐折 OOS 指标全量输出（含最差折）；summary 含 min/max——"
                 "不得只报最优。**只报数字、不判定策略有效**（ADR-13）。"
                 "**OOS 折含 warm-up**（exec_range 含热身；metric_range 仅 test "
                 "段——指标口径已隔离，R58′/N60）"),
    }
