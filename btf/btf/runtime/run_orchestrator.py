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
from dataclasses import replace
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


def _delisted_holding_notes(result: Any) -> list[str]:
    """RC-11-A（2026-10-05）＋ **CL-3**（2026-10-06）：退市持仓**残值披露**文案。

    文案三要素（[18 §九·C](18) 锚点）：**标的** / **末次交易日** / **残值口径**；
    **CL-3（19 号 §72.4）**：残值口径文字随 `run.delisted_settlement` 取值变化并
    **注明口径出处**（Q-1(a) 整理期末收盘 / Q-2(b) 默认现状 ＋ 显式开关）：

      - `carry`（默认）：按末次已知收盘价**携带**、未清算（RC-11-A 现状，
        数值**零改动**）；
      - `writeoff`：按 **0 核销**（全额损失，压力口径）——核销只作用于区间末
        快照；核销基准 = ≤ 区间末的末次可用收盘价（**防前视，不取区间外价**）。

    无退市持仓 ⇒ 返回空列表（披露**不得恒现**——反向探针依赖）。
    """
    mode = str(getattr(result, "delisted_settlement", "carry") or "carry")
    amount = float(getattr(result, "delisted_writeoff_amount", 0.0) or 0.0)
    notes: list[str] = []
    for item in getattr(result, "delisted_holdings", ()) or ():
        sym = str((item or {}).get("symbol", "?"))
        last = (item or {}).get("last_trade_date") or "（无 bar 记录）"
        if mode == "writeoff":
            notes.append(
                f"退市持仓残值披露：{sym} 于区间末仍持仓且已退市——残值口径 = "
                f"**按 0 核销**（全额损失，压力档 `writeoff`；核销额 "
                f"{amount:.2f} 元，末日快照 NAV 已下调、该标的市值/权重/持仓"
                f"数量快照口径清零）；核销基准 = 区间内**末次可用收盘价**"
                f"（末次交易日 {last}；**未取区间外价**——防前视）；"
                f"口径出处：19 号 §72.4 Q-1(a)/Q-2(b)"
                f"（`--delisted-settlement writeoff`）")
        else:
            notes.append(
                f"退市持仓残值披露：{sym} 于区间末仍持仓且已退市——退市后无 bar ⇒ "
                f"市值沿用**末次已知收盘价**（末次交易日 {last}；残值按末次收盘价"
                f"携带、**未清算**），NAV 不含退市清算损益；"
                f"`portfolio.py` 残值语义**零改动**（RC-11-A，只披露不改数；"
                f"压力口径可显式切 `--delisted-settlement writeoff`，CL-3）")
    return notes


def _corp_action_notes(result: Any) -> list[str]:
    """**CL-2 第二段（R5′；19 号 §79.4）**披露：配股行权 / 缩股 / 清单状态。

    三要素＋审计（逐事件）：**标的 / 除权日 / 获配与缴款**（＋现金缺口、
    获配股份上市日、锁定期卖出警示）。清单**未加载**时**显式**说明
    「配股/缩股未建模」（禁静默——D7 口径保留见 20 号纪律四）。
    无此类事件 ⇒ 不追加逐事件行（披露**不得恒现**），但仍给一行清单状态。
    """
    from btf.data.corp_action_probe import load_actions

    data = load_actions()
    if data is None:
        return ["配股/缩股清单（CL-2 第二段）：**未加载**（缺 "
                "`btf_datasets/corp_actions/corp_actions.json`）⇒ 配股/缩股"
                "**未建模**；命中标的结论须加口径保留（20 号纪律四）"]
    rights = list(getattr(result, "rights_events", ()) or ())
    cons = list(getattr(result, "consolidations", ()) or ())
    from btf.data.corp_action_probe import consolidations_modeled

    st = (data.get("stats") or {})
    cons_note = (f"{st.get('consolidation_corroborated', '?')} 条缩股（佐证；"
                 "**已建模**）" if consolidations_modeled()
                 else f"{st.get('consolidation_corroborated', '?')} 条缩股（佐证；"
                      "**只披露不建模**——开关关闭）")
    conf_n = st.get("consolidations_confirmed", 0)
    conf_note = (f"；确证缩股 {conf_n} 条（股本级三重证据，**已建模**——R18′）"
                 if conf_n else "")
    notes = [
        f"配股/缩股清单（CL-2 第二段；19 号 §79.4）：已加载 "
        f"{st.get('rights_validated', '?')} 条配股（校验通过）＋ {cons_note}"
        f"{conf_note}；"
        f"来源哈希 {str((data.get('source') or {}).get('snapshot_sha256', ''))[:16]}…"
    ]
    for ev in rights:
        line = (
            f"配股行权披露（CL-2 第二段 / Q-4(a) 默认全额行权）："
            f"{ev['symbol']} 除权 {ev['ex_date']}——登记日持仓 "
            f"{ev['qty_at_ex']} 股 × 配股比例 {ev['ratio']:.6g} ⇒ 获配 "
            f"{ev['rights_qty']} 股、缴款 {ev['cost']:.2f} 元"
            f"（配股价 {ev['price']}；可配股数取 floor，§79.3 #1）")
        if ev.get("cash_shortfall"):
            line += (f"；**现金缺口 {ev['cash_shortfall']:.2f} 元**"
                     f"（默认外部增资补足，§79.3 #2）")
        if ev.get("listing_date"):
            line += f"；获配股份上市日 {ev['listing_date']}（默认不建模锁定期）"
        notes.append(line)
    for msg in getattr(result, "lockup_sell_warnings", ()) or ():
        notes.append(str(msg))
    for ev in cons:
        notes.append(
            f"缩股披露（CL-2 第二段 R6′ 代理检测；19 号 §78.5）："
            f"{ev['symbol']} 除权 {ev['ex_date']} 比例 {ev['ratio']:.6g} ⇒ "
            f"持仓 {ev['qty_before']} → {ev['qty_after']} 股（无现金流；"
            f"代理口径，未被交易所公告确证）")
    if not rights and not cons:
        notes.append("本 run 未命中配股/缩股事件（清单已加载；披露不得恒现）")
    return notes


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
            # RC-11-A / CL-2 第二段：不落盘路径同样登记披露（装配说明可被调用方读取）
            self.rt.assembly_notes.extend(_delisted_holding_notes(result))
            self.rt.assembly_notes.extend(_corp_action_notes(result))
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
            # RC-11-A（2026-10-05）：退市持仓残值披露——引擎运行期才可知，
            # 骨架 manifest 已写 ⇒ 追加装配披露并**补写 manifest**
            # （`complete()` 读盘补全 → 终态 manifest 含该披露）。只披露不改数。
            _rc11_notes = (_delisted_holding_notes(result)
                           + _corp_action_notes(result))
            if _rc11_notes:
                self.rt.assembly_notes.extend(_rc11_notes)
                manifest = replace(
                    manifest, assembly_notes=tuple(self.rt.assembly_notes))
                store.write_manifest(manifest)
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
