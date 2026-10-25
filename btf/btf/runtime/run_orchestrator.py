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
import logging
from dataclasses import replace
from pathlib import Path
from typing import Any

from btf import registry
from btf.analytics.run_metrics import compute_run_metrics
from btf.analytics.trades import normalize_consolidations, pair_fills_to_trades
from btf.config.paths import RUNS_DIR
from btf.experiment.store import LocalRunStore

logger = logging.getLogger(__name__)

#: 数据不变量校验产物文件名（批 8 CC-6；run 目录内）。
#: R4 第二刀：随落盘编排一并落位（`btf.runtime` 再导出，对外名不变）。
DATA_QUALITY = "data_quality_report.json"

__all__ = ["DATA_QUALITY", "RunOrchestrator", "consolidations_from_events"]


def consolidations_from_events(path: str | Path) -> tuple[tuple[str, Any, float], ...]:
    """**R61′（N63）**：从 run 的 `events.jsonl` 还原**已应用**的折算序列。

    用途＝**复核路径**（`bt verify` 从产物重算指标）：与落盘路径**同源**——
    引擎把 `portfolio.apply_ex_date` 实际消费的行动写成 `CorporateActionEvent`，
    其中 `consolidation_ratio != 1` 者即已应用（确证缩股恒装载／代理缩股受开关
    `BTFTOOL_CORP_ACTIONS_CONSOLIDATIONS` 控制／ETF 折算走双证据装载 ⇒ 事件集
    ＝应用集；未装载者不落事件 ⇒ 不会被误应用）。

    边界：① 文件缺失（旧产物/无事件日志）⇒ 空；② **采样降级**日志
    （`reader.sampled`）可能缺事件 ⇒ 返回可得子集并 **WARN**（禁静默）。
    """
    p = Path(path)
    if not p.is_file():
        return ()
    from btf.engine.events_log import EventLogReader

    reader = EventLogReader(p)
    rows: list[tuple[Any, Any, Any]] = []
    for ev in reader.events():
        action = getattr(ev, "action", None)
        if action is None:
            continue
        rows.append((getattr(action, "symbol", ""),
                     getattr(action, "ex_date", None),
                     getattr(action, "consolidation_ratio", 1.0)))
    if reader.sampled:
        logger.warning("事件日志为采样降级（%s）⇒ 复核用折算序列可能不完整"
                       "（R61′：禁静默）", p)
    return normalize_consolidations(rows)


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


def _etf_action_notes(result: Any, feed: Any) -> list[str]:
    """**CL-2-H（R32′/R35′/R36′；ADR-20）**：ETF/LOF 公司行动披露 ＋ 覆盖段。

    输出（报告「假设与披露」消费）：
      ① **ETF 分红逐事件**（标的 / 除息日 / 到账日 / `qty × div_cash`）；
      ② **覆盖段 7 项**（命中分红笔数／命中折算次数／未建模项清单／指标口径／
         记账口径／告警列表／作废横幅）；`作废横幅` 的渲染归报告层（R36′——
         命中 `回测产物/runs/作废登记-*.md` 即顶部显示）；
      ③ **名义价告警**（宇宙含 ETF/LOF 且区间跨越除息/折算日——§92.12 N33
         改判后的工具侧责任）；
      ④ feed 侧 note（回退链 / 非交易日顺延 / fail-closed 未建模）。
    **披露不得恒现（R38′/N40，2026-10-08）**：宇宙内无 ETF/LOF（`is_fund_code`
    数 = 0）或本次未做 ETF/LOF 扫描（summary 空）⇒ **逐事件行与覆盖段均不出现**
    （纯股票 run 上恒显「命中 0 笔」违反 §93/§95 纪律）；ETF 宇宙 ⇒ 覆盖段
    输出（口径可见，无事件时命中数如实为 0）。
    """
    from btf.domain.types import is_fund_code

    divs = [e for e in (getattr(result, "dividend_events", ()) or ())
            if is_fund_code(str(e.get("symbol", "")))]
    cons = [e for e in (getattr(result, "consolidations", ()) or ())
            if str(e.get("source")) == "fund_conv"]
    notes: list[str] = []
    for ev in divs:
        notes.append(
            f"基金分红披露（ADR-20 第 2/3 条 / R32′）：{ev['symbol']} 除息 "
            f"{ev['ex_date']}——派息日 {ev['pay_date']} 现金到账 "
            f"{ev['qty_at_ex']} 份 × {ev['cash_div']:.6g} = "
            f"{ev['amount']:.2f} 元（`div_cash` 即每份金额、**未除 `base_unit`**；"
            f"`ex_date` 份额不变）")
    feed_notes = list(getattr(feed, "etf_action_notes", ()) or ())
    summary = dict(getattr(feed, "etf_scan_summary", {}) or {})
    # **R38′（N40，2026-10-08）**：**无非空守卫**——宇宙内 `is_fund_code` 数
    # 为 0（`n_symbols=0`：`source=all` 等纯股票宇宙，实测 v77 preRC 恒显
    # 「命中分红 0 笔…」）或本次未做 ETF/LOF 扫描（summary 空）⇒ **不输出
    # 覆盖段**（披露不得恒现——19 号 §93/§95 纪律；N40 负向对照）。
    n_funds = summary.get("n_symbols")
    if n_funds is None or int(n_funds) == 0:
        return list(feed_notes)
    n_div = int(summary.get("n_div", len(divs)) or 0)
    n_conv = int(summary.get("n_conv", len(cons)) or 0)
    n_unmodeled = int(summary.get("n_unmodeled", 0) or 0)
    n_alerts = n_unmodeled
    unmodeled = ([f"{n_unmodeled} 项（fail-closed：双证据不符/因子缺失 ⇒ 不建模）"]
                 if n_unmodeled else ["无"])
    # **R62′（N64；§118.3）**：覆盖段的「指标口径」须随 `run.price_view` 措辞——
    # `adjusted`（默认）下策略信号走复权视图，**不得**再写「信号未复权」；
    # `nominal`（对照档）才保留旧措辞（含跳变失真的名义价告警）。
    pv = str(getattr(result, "price_view", "nominal") or "nominal")
    metric_clause = (
        "指标/信号口径＝**qfq 后复权视图**（R62′/N64；ref＝区间起点，PIT 合规；"
        "ADR-6 记账仍名义价）"
        if pv == "adjusted" else
        "指标口径＝名义价（Q-9=(a′)；**信号未复权**——R62′ 对照档）")
    warning = "无"
    if n_div or n_conv:
        warning = (("1 条：本 run 已消除公司行动处的**信号跳变失真**"
                    "（R62′/N64 复权视图；记账仍名义价——ADR-6 未改）")
                   if pv == "adjusted" else
                   ("1 条：**本 run 策略信号基于名义价，存在跳变失真**"
                    "（ADR-20 第 8 条；Q-9=(a′) 保持名义价——与真源/B1 一致）"))
        n_alerts += 1
    notes.append(
        f"ETF/LOF 公司行动覆盖段（ADR-20/R36′；19 号 §96.4）：命中分红 "
        f"{n_div} 笔（载入；其中**入账 {len(divs)} 笔**——除息日无持仓者不入账）／ "
        f"命中折算 {n_conv} 次（载入；其中**入账 {len(cons)} 次**）／ "
        f"未建模项 {unmodeled[0]} ／ "
        f"{metric_clause}＋ win_rate＝在场日口径"
        f"（R26′/DOC-11；逐笔辅助＝`win_rate_trades`，须显式声明）、Sharpe/波动＝"
        f"全样本含空仓日（显式标注） ／ 记账口径＝现金到账"
        f"（Q-7 默认；再投未启用） ／ 告警列表＝{warning} ／ 作废横幅＝"
        f"由报告层按作废登记表渲染（命中即顶部显示「本 run 已作废：原因＋"
        f"重跑条件」——R36′/§95.3-A4）")
    notes.extend(feed_notes)
    return notes


def _price_view_notes(result: Any) -> list[str]:
    """**R62′（N64；19 号 §118）**：消费侧价格视图披露（禁静默）。

    恒披露一行「视图＋基准日＋ADR-6 记账不变」；`adjusted` 下降级（因子缺失
    回退 1.0 等）由 `TushareAdjustService.degraded_notes()` 透传。
    """
    pv = str(getattr(result, "price_view", "nominal") or "nominal")
    ref = getattr(result, "price_view_ref", None)
    ref_s = ref.to_ymd() if ref is not None else "—"
    notes = [
        f"价格视图（R62′/N64；19 号 §118）：run.price_view={pv}"
        + (f"——喂给策略的 bar 走 **qfq 后复权**（ref={ref_s}＝区间起点，PIT 合规）；"
           f"**ADR-6 记账口径不变**：撮合价/持仓成本/费用/NAV/快照恒为**名义价**"
           if pv == "adjusted" else
           "——喂给策略的 bar 为**名义价**（旧行为/对照档）")
    ]
    notes.extend(str(n) for n in (getattr(result, "adjust_notes", ()) or ()))
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
        if ev.get("p0_source") == "avg_cost_fallback":
            # **B-02**（三轮架构审查）：`p0` 兜底取 avg_cost ＝ **成本冒充市价**
            # （当日无 bar）。属降级口径，必须进披露通道（与 RC-04 同机制），
            # 否则只存在于代码注释里 —— 引用该事件当日 NAV 的结论会无据可查。
            line += (f"；⚠ **降级披露**：除权日无 `last_close`（当日无 bar）⇒ "
                     f"理论除权价基准 `p0` **兜底取 avg_cost={ev.get('p0')}**"
                     f"（成本冒充市价——B-02；当日 NAV 重估基准为降级值，"
                     f"引用须加口径保留）")
        notes.append(line)
    for msg in getattr(result, "lockup_sell_warnings", ()) or ():
        notes.append(str(msg))
    for ev in cons:
        # **R22′（19 号 §87.8.2 修订版；N24）**：逐事件按**来源**分措辞——
        # `confirmed`（股本级三重证据，R18′）**不得**写成「代理／未确证」；
        # `proxy`（价格代理检测）维持原口径。
        src = str(ev.get("source") or "proxy")
        if src == "fund_conv":
            # **CL-2-H（R35′）**：ETF/LOF 份额折算（双证据，已建模）——与股票
            # 缩股同构（qty ×= ratio、成本总额守恒），措辞按来源三分。
            notes.append(
                f"基金份额折算披露（ADR-20 第 5 条 / R35′；19 号 §96.4）："
                f"{ev['symbol']} 折算 {ev['ex_date']} 比例 {ev.get('ratio', 0):.6g} ⇒ "
                f"持仓 {ev['qty_before']} → {ev['qty_after']} 份（无现金流；"
                f"`shares÷=R`、`avg_cost×=R`、成本总额守恒）；**已建模**"
                f"（双证据：`pre_close÷前收` ⇄ `fund_adj` 跳变倒数，容差 0.5%）")
            if ev.get("forced_min_qty"):
                notes[-1] += (
                    "；⚠ **WARN** 保底生效：可缩股数 `int(qty×ratio)==0` ⇒ "
                    "保留 1 股且 `avg_cost` 不放大（`forced_min_qty`，R21′）")
            frac = ev.get("qty_fraction_dropped")
            if frac:
                # **R45′（N48）**：碎份口径显式披露（ADR-20 第 ⑪ 条）
                notes[-1] += (
                    f"；碎份口径：向下取整**丢弃 {frac:g} 份**（不补偿——"
                    f"ADR-20 第 ⑪ 条/R45′）")
            continue
        body = (f"{ev['symbol']} 除权 {ev['ex_date']} 比例 {ev['ratio']:.6g} ⇒ "
                f"持仓 {ev['qty_before']} → {ev['qty_after']} 股（无现金流；")
        if src == "confirmed":
            line = (f"缩股披露（**确证缩股**：股本级三重证据——"
                    f"`total_share`↓ ∩ `adj_factor`↓ 同窗比例一致，R18′；"
                    f"19 号 §85.1）：{body}"
                    f"**已建模**）")
        else:
            line = (f"缩股披露（CL-2 第二段 R6′ 代理检测；19 号 §78.5）：{body}"
                    f"代理口径，未被交易所公告确证；**仅披露不建模**）")
        if ev.get("forced_min_qty"):
            line += ("；⚠ **WARN** 保底生效：可缩股数 `int(qty×ratio)==0` ⇒ "
                     "保留 1 股且 `avg_cost` 不放大（`forced_min_qty`，R21′）")
        notes.append(line)
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
        返回值=engine.RunResult——``manifest`` / ``run_id`` / ``metrics`` 三字段
        由 `RunResult` dataclass **声明**（D-01）：落盘路径填真值，
        `persist=False` 路径显式置 **None**（＝未落盘/未计算，禁按空字典兜底）。
        """
        if self.rt.strategy is None:
            raise RuntimeError("先 build 再 run")

        if not persist:
            result = self.rt._run_engine(None)
            # **D-01**（三轮架构审查）：不落盘路径**显式置 None** —— 三字段已在
            # `RunResult` dataclass 声明（契约单一形态），此处显式赋 None 把
            # "本次未落盘/未计算"写进代码而非只靠默认值（禁用 `{}` 兜底解读）。
            result.manifest = None
            result.run_id = None
            result.metrics = None
            # RC-11-A / CL-2 第二段：不落盘路径同样登记披露（装配说明可被调用方读取）
            self.rt.assembly_notes.extend(_delisted_holding_notes(result))
            self.rt.assembly_notes.extend(_corp_action_notes(result))
            self.rt.assembly_notes.extend(
                _etf_action_notes(result, self.rt.feed))
            self.rt.assembly_notes.extend(_price_view_notes(result))   # R62′
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
            # T-18（19 号 §149 D-1）：WF 折内跳过哈希门禁时**必须落盘披露**
            version_gate=(self.rt.version_gate_skip_reason
                          if self.rt.version_gate_skipped_hash else None),
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
                           + _corp_action_notes(result)
                           + _etf_action_notes(result, self.rt.feed)
                           + _price_view_notes(result))      # R62′（N64）
            if _rc11_notes:
                self.rt.assembly_notes.extend(_rc11_notes)
                manifest = replace(
                    manifest, assembly_notes=tuple(self.rt.assembly_notes))
                store.write_manifest(manifest)
            self.rt.step_timings["运行·引擎"] = _pc_run() - _t_run
            _t_run = _pc_run()
            # **R61′（N63，§114.2 第 1 条）**：配对与指标均传入**已应用的折算序列**
            # （源＝`result.consolidations`——portfolio 实际入账的逐事件审计）
            consolidations = normalize_consolidations(
                getattr(result, "consolidations", ()) or ())
            trades = pair_fills_to_trades(result.fills,
                                          consolidations=consolidations)
            store.save_snapshots(manifest.run_id, result.snapshots)
            store.save_fills(manifest.run_id, result.fills)
            store.save_trades(manifest.run_id, trades)
            store.save_rejections(manifest.run_id, result.rejections)
            # R4 剩余：`compute_all` 三处调用链收敛为单点（`analytics.run_metrics`）
            metrics = compute_run_metrics(
                result.snapshots, result.fills, self.rt._analysis_config(),
                analyzer_names=self.rt._analyzer_names(),
                extras=self.rt._benchmark_metrics,      # R2.5 / P1-10 基准相对项
                resolver=lambda name: registry.resolve(registry.ANALYZER, name),
                consolidations=consolidations)          # R61′：逐笔口径配对同源
            manifest = store.save_metrics(manifest.run_id, metrics)
            # **MM-6（19 号 §137 B）**：多基准对照明细随 manifest 落盘
            # （`report.benchmarks[]`）——主基准数值已在 metrics（上一步），
            # 其余基准**只在此披露**（P0-NEW-7：metrics 只许数值）。单基准
            # 亦落一条（同一口径，报告据此渲染对照/披露，不改 digest）。
            if self.rt.benchmark_details:
                manifest = replace(
                    manifest, benchmarks=tuple(self.rt.benchmark_details))
                store.write_manifest(manifest)

        except Exception as exc:                    # 崩溃审计：留 FAILED 终态
            store.mark_failed(manifest.run_id, f"{type(exc).__name__}: {exc}")
            raise
        result.manifest = manifest
        result.run_id = manifest.run_id
        result.metrics = store.read_metrics(manifest.run_id)
        self.rt.step_timings["运行·落盘与指标"] = _pc_run() - _t_run
        self.rt._warn_if_short_period(result)
        return result
