# -*- coding: utf-8 -*-
"""**RunOrchestrator** —— 落盘编排（R4 第二刀；原 `BTFRuntime.run` 的 persist 路径）。

落盘时序（崩溃可审计，08 §13.2 落盘纪律）：

```
① create_run → manifest 骨架（status=RUNNING）
② 数据指纹实算（**仅落盘路径**——BB-1：网格/内存回测零成本）
③ CC-6 产物：数据不变量校验报告**恒定落盘**（含"为何没校验"）
④ Engine 执行（events.jsonl 直写 run 目录）
⑤ 产物落盘（snapshots/fills/trades/rejections）
⑥ 指标计算（单点 `compute_run_metrics`）→ metrics.json + manifest 补全（COMPLETED + digest）
异常 → manifest 落 FAILED（含原因）后抛出
```

拆分目的：把"**一次落盘到底怎么排**"从门面里抽出来——顺序与异常终态是
可复现性的关键，集中一处便于审计与测试；门面只保留 `run()` 一行委托。
"""
from __future__ import annotations

import json
from typing import Any

from btf import registry
from btf.analytics.run_metrics import compute_run_metrics
from btf.analytics.trades import pair_fills_to_trades
from btf.config.paths import RUNS_DIR
from btf.experiment.store import LocalRunStore

#: 数据不变量校验产物文件名（批 8 CC-6；run 目录内）。
#: R4 第二刀：随落盘编排一并落位（`btf.runtime` 再导出，对外名不变）。
DATA_QUALITY = "data_quality_report.json"

__all__ = ["DATA_QUALITY", "RunOrchestrator"]


class RunOrchestrator:
    """一次回测的落盘编排（显式依赖装配根 `rt`）。"""

    def __init__(self, rt: Any) -> None:
        self.rt = rt


    def run(self, *, store: LocalRunStore | None = None,
            persist: bool = True) -> Any:
        """Engine 执行 + RunStore 落盘（04 §8.3.8；08 §13.3）。

        落盘时序（崩溃可审计，08 §13.2 落盘纪律）：
            ① create_run → manifest 骨架（status=RUNNING）
            ② Engine 执行（events.jsonl 直写 run 目录）
            ③ 产物落盘（snapshots/fills/trades/rejections）
            ④ 指标计算 → metrics.json + manifest 补全（COMPLETED + digest）
            异常 → manifest 落 FAILED（含原因）后抛出。

        persist=False 时不落盘（纯内存回测）；store 可注入（测试隔离）。
        返回值=engine.RunResult（附加 ``manifest`` / ``run_id`` / ``metrics``）。
        """
        if self.rt.strategy is None:
            raise RuntimeError("先 build 再 run")

        if not persist:
            result = self.rt._run_engine(None)
            self.rt._warn_if_short_period(result)
            return result

        # 扩展点接线（19 号审查 P1-1/EX-1）：经 registry 名字表取 RunStore
        # ——原实现 `LocalRunStore()` 直 new，「配置声明制」对 RUN_STORE 落空
        store = store or registry.create(
            registry.RUN_STORE, "local_jsonl", {"root": RUNS_DIR})
        # 数据指纹（BB-1「重路」）**只在此处**（落盘路径）实算：披露字段只在有
        # 产物时有意义 → 网格搜索（每组合 build + persist=False）与内存回测
        # **零成本**（BB-1 首版接线在 `build()`，实测令 B4 外推 664s → 3647s，
        # 见 §40.5 D-7）。**fail-closed**：失败即抛，绝不静默回落 `unknown`。
        from time import perf_counter as _pc_run

        _t_run = _pc_run()
        if not self.rt.data_version_info:
            self.rt.data_version_info = self.rt._compute_data_version(
                self.rt.config["run"]["period"])
        self.rt.step_timings["运行·数据指纹"] = _pc_run() - _t_run
        _t_run = _pc_run()
        manifest = store.create_run(
            self.rt.config,
            rules_version=self.rt._rules_version(),
            rules_overrides=self.rt._rules_overrides(),
            assembly_notes=self.rt.assembly_notes,     # P1-NEW-5：口径随产物落盘
            data_version=self.rt.data_version_info,    # BB-1：数据指纹落真值
        )
        self.rt.last_run_id = manifest.run_id
        # CC-6 产物（批 8）：校验报告随 run 落盘（**恒定落盘**：即使 enabled=false
        # 也写"为什么没校验"——铁律新 16：披露字段不得恒空/不得静默）
        if self.rt.data_quality:
            from btf.experiment.store import atomic_write_text

            atomic_write_text(
                store.run_dir(manifest.run_id) / DATA_QUALITY,
                json.dumps(self.rt.data_quality, ensure_ascii=False, indent=2,
                           sort_keys=True) + "\n")
        try:
            result = self.rt._run_engine(store.events_path(manifest.run_id))
            self.rt.step_timings["运行·引擎"] = _pc_run() - _t_run
            _t_run = _pc_run()
            trades = pair_fills_to_trades(result.fills)
            store.save_snapshots(manifest.run_id, result.snapshots)
            store.save_fills(manifest.run_id, result.fills)
            store.save_trades(manifest.run_id, trades)
            store.save_rejections(manifest.run_id, result.rejections)
            # R4 剩余：`compute_all` 三处调用链收敛为单点（`analytics.run_metrics`）
            metrics = compute_run_metrics(
                result.snapshots, result.fills, self.rt._analysis_config(),
                analyzer_names=self.rt._analyzer_names(),
                extras=self.rt._benchmark_metrics,      # R2.5 / P1-10 基准相对项
                resolver=lambda name: registry.resolve(registry.ANALYZER, name))
            manifest = store.save_metrics(manifest.run_id, metrics)
        except Exception as exc:                    # 崩溃审计：留 FAILED 终态
            store.mark_failed(manifest.run_id, f"{type(exc).__name__}: {exc}")
            raise
        result.manifest = manifest
        result.run_id = manifest.run_id
        result.metrics = store.read_metrics(manifest.run_id)
        self.rt.step_timings["运行·落盘与指标"] = _pc_run() - _t_run
        self.rt._warn_if_short_period(result)
        return result
