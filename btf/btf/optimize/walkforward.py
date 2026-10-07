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
                            train_metrics, test_metrics}],
     summary:{<metric>:{n,mean,median,min,max,stdev}}, note}

落盘纪律：每折的 `manifest` 经**装配披露**记录「折号 / 训练窗 / 测试窗 /
参数」（`assembly_notes`）——"训练/测试区间入 manifest"由此成立（09 #11）。
"""
from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from btf.domain.types import TradingDate

__all__ = ["Window", "run_walk_forward", "split_windows"]

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
                 which: str) -> dict[str, Any]:
    """配置 → 指定段的 period 覆盖（浅复制；`run.period` 单点替换）。"""
    if which == "train":
        start, end = window.train_start, window.train_end
    else:
        start, end = window.test_start, window.test_end
    cfg = dict(config)
    run_cfg = dict(cfg.get("run") or {})
    run_cfg["period"] = {"start": _iso(start), "end": _iso(end)}
    cfg["run"] = run_cfg
    return cfg


def _metrics_of(rt: Any, result: Any) -> dict[str, float]:
    """折内指标——与 `bt run` 同一单点（含基准相对项与名字表）。"""
    from btf.analytics.run_metrics import compute_run_metrics

    return compute_run_metrics(
        result.snapshots, result.fills, rt._analysis_config(),
        analyzer_names=rt._analyzer_names(),
        extras=rt._benchmark_metrics,
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
) -> dict[str, Any]:
    """逐折执行 Walk-Forward（**每折走标准装配与执行**）。

    `persist=True` 时每折经 RunStore 落盘（`store` 可注入），且**窗口信息
    进该折 manifest 的装配披露**（09 #11）；`persist=False` 为纯内存模式
    （快，但无 manifest/报告——披露落在返回结构里）。
    `include_is=False` 跳过训练段回测（省一半算力；IS 指标列缺省为 null）。
    """
    from btf.runtime import BTFRuntime

    windows = split_windows(days, train=train, test=test, step=step, mode=mode,
                            purge=purge, embargo=embargo)
    step_eff = test if step is None else step
    folds: list[dict[str, Any]] = []
    for w in windows:
        fold: dict[str, Any] = {"index": w.index, **{k: v for k, v in
                                                     w.as_dict().items()
                                                     if k != "index"}}
        note = (
            f"Walk-Forward 折 {w.index}/{len(windows)}（RC-15）："
            f"训练窗 {w.train_start.to_ymd()}~{w.train_end.to_ymd()} / "
            f"测试窗 {w.test_start.to_ymd()}~{w.test_end.to_ymd()}"
            f"（mode={mode}, window={train}, test={test}, step={step_eff}, "
            f"purge={purge}, embargo={embargo}）")
        if include_is:
            rt_tr = BTFRuntime().load_config(
                _fold_period(config, w, "train")).build()
            rt_tr.assembly_notes.append(note + "｜段=训练（IS）")
            res_tr = rt_tr.run(persist=False)
            fold["train_metrics"] = _metrics_of(rt_tr, res_tr)
        else:
            fold["train_metrics"] = None

        rt_te = BTFRuntime().load_config(_fold_period(config, w, "test")).build()
        rt_te.assembly_notes.append(note + "｜段=测试（OOS，判定只看本段）")
        res_te = rt_te.run(store=store, persist=persist)
        fold["test_metrics"] = _metrics_of(rt_te, res_te)
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
                 "persist": persist, "include_is": include_is},
        "n_folds": len(windows),
        "folds": folds,
        "summary": summary,
        "note": ("逐折 OOS 指标全量输出（含最差折）；summary 含 min/max——"
                 "不得只报最优。**只报数字、不判定策略有效**（ADR-13）"),
    }
