# -*- coding: utf-8 -*-
"""L2 选股**消融策略**（老大 2026-10-01 令：旧脚本定论用 btf 重算）。

## 由来（为什么要有这个文件）

2026-08-21 的 v7.4 审计（`_archive/v7.4审计-20260821/`）用 6 个**一次性脚本**
得出"L2 基本面选股是 alpha"的结论，该结论沿用至今
（`rules/single-source.md:78` 标为「唯一 alpha」）。审计发现两个致命问题：

1. **参数漂移**：同批脚本止损档不一致（-3%/-3.5% vs -5%），且报告 §5.4 把
   "止损放宽"（0.77%→1.07%，+0.30pp）与"选股"（1.07%→2.26%，+1.19pp）
   **混在一起**宣称为"2.9 倍提升"；
2. **验证对象与落地对象不一致**：报告推荐并验证的是**方案 E（多因子 Top30%）**，
   而落地的却是四条硬阈值，后者从未被单独验证。

本模块在 **btf 稳定引擎**上把这两件事重新做一遍：同一 engine、同一数据 IO、
同一评价指标、同一期间，只让**选股方式**成为变量。

## 正交设计（关键）

旧脚本把「过滤」和「排序」耦合在一起，导致无法归因。本策略显式拆成两个变量：

- `selection`：用哪些条件**筛出候选池**
  - `none`        不过滤（数据可得全集）——基准，代表"不选股"
  - `current`     现行四条硬筛（PE<30 + PB<3 + ROE>5% + 股息率>1%）
  - `multifactor` 方案 E：PE/PB/ROE/股息率四因子横截面打分 Top 30%
  - `pr`          方案 C：PR = pe_ttm / roe_waa < 1.5

- `ranking`：从候选池里**怎么挑** top_k
  - `random`  确定性随机（seed + 日期派生）——真·无观点基准
  - `code`    股票代码字母序 —— **btf v77 现状的实际行为**（见下方 FF-3）
  - `pe_asc`  PE 升序（低估优先）—— v77 docstring **声称**但从未实现的行为
  - `score`   因子综合分升序（配合 multifactor / pr 使用）

由此可做干净归因：
  · `none+random` vs `none+pe_asc`      → **纯排序效应**（"挑选方式"值多少）
  · `current+pe_asc` vs `none+pe_asc`   → **纯过滤效应**（"筛子"值多少）
  · `current+code` vs `current+pe_asc`  → FF-3 缺陷的实际代价

## FF-3：v77「低估优先」从未实现

`btf/data/fundamentals.py::screen_l2` 返回 `sorted(picks)` —— **代码字母序**；
`btf/strategy/v77.py` 用 `[:top_k]` 截取，docstring 却写"低估优先（pe_ttm 升序）
取前 top_k"。全仓库不存在任何 pe_ttm 排序实现。

⇒ **所有历史 v77 结论（含 §23.2 的 IR 0.0548 / §54.3 的 Z-6 后 IR 0.0504）
都是在「取通过硬筛的代码字母序前 10 只」这一行为下产生的**，
"低估优先"这一 alpha 主张**从未被执行过**。

本文件的 `ranking="code"` 忠实复刻该行为以保可比；`ranking="pe_asc"` 才是
文档所描述的行为。两者同跑即可量化 FF-3 的实际影响。

## 口径纪律

- **候选宇宙统一**：所有臂一律限制在「当日 pe_ttm/pb/dv_ttm + PIT roe_waa
  均可得」的集合内。理由：旧脚本 BASE 用行情全集、选股臂用基本面子集，
  两者 universe 不同 ⇒ 混入**数据可得性偏差**。此处认齐，alpha 才干净。
- **真 PIT**：ROE 经 `ann_date ≤ date` 过滤（比 08-21 脚本严格）。
- **确定性**：随机臂用 `Random(f"{seed}:{ymd}")` 派生，不依赖全局 rng 状态，
  同参数必同结果（可 `bt verify` 复现）。
- **规则参数**只经 `rules`（镜像 JSON），本模块不写死任何阈值字面量。

## 装配

    strategy: "btf.strategy.l2_ablation:L2AblationStrategy"
    params: {selection: "current", ranking: "pe_asc", top_k: 10, seed: 42}
    rules: {source: "mirror_json", path: "..."}
"""
from __future__ import annotations

import math
import random
from collections.abc import Callable, Mapping, Sequence

from btf.data.fundamentals import daily_basic_of, roe_pit_of
from btf.data.liq import POSITION_CAPS, LiqState, state_of
from btf.domain.contracts import CONTRACT_VERSION
from btf.domain.types import TradingDate
from btf.strategy.base import StrategyBase
from btf.strategy.context import StrategyContext
from btf.strategy.rebalance import TargetPortfolio

#: 可用 selection（白名单；拼错即参数错误，不静默退化）
SELECTIONS = ("none", "current", "multifactor", "pr")
#: 可用 ranking
RANKINGS = ("random", "code", "pe_asc", "score")
#: 方案 E 的 Top 百分比口径（08-21 报告 §5.2 用 Top 30% 定义方案；
#: 本实现用 score 升序取 top_k，故该常量仅作**文档留痕**，不参与取数）
MULTIFACTOR_TOP_PCT = 30.0
#: 方案 C 的 PR 上限（08-21 报告口径；注意 single-source 的 PR 用 ROE_DT，
#: 此处用 roe_waa 以与 current 臂同口径 —— 差异在报告中披露）
PR_MAX = 1.5

_SELECTION_HELP = " | ".join(SELECTIONS)
_RANKING_HELP = " | ".join(RANKINGS)


def _finite(value: object) -> float | None:
    """None / NaN / inf → None（估值字段缺值常见，必须显式净化）。"""
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


class L2AblationStrategy(StrategyBase):
    """L2 选股消融策略（selection × ranking 正交，其余与 V77Strategy 同构）。

    与 `V77Strategy` 的唯一结构性差异：本类的候选池筛选方式由 `selection`
    参数化，可在同一进程、同一份数据上切换对照组；控仓逻辑（L0-LIQ）与 v77
    完全一致，保证 baseline 可比。
    """

    contract_version = CONTRACT_VERSION

    def __init__(
        self,
        rules: object,
        *,
        selection: str = "current",
        ranking: str = "code",
        top_k: int = 10,
        seed: int = 42,
        root: str | None = None,
        rebalance: str = "monthly",
        caps: Mapping[str, float] | None = None,
        liq_series: Mapping[str, LiqState] | None = None,
        liq_fn: Callable[..., LiqState] | None = None,
    ):
        """
        rules        RulesProvider（镜像 JSON；装配期注入）
        selection    候选池筛选方式（见模块 docstring；白名单 SELECTIONS）
        ranking      池内挑选方式（见模块 docstring；白名单 RANKINGS）
        top_k        持有数量
        seed         随机臂派生种子（同参必同结果）
        root         主库根（None = 默认 MARKET_DATA_DIR）
        rebalance    "monthly"（首个交易日）| "daily"
        caps         状态→仓位上限 %（None = 规则源口径 POSITION_CAPS）
        liq_series   区间预计算 LIQ 序列（性能：O(1) 查表）
        liq_fn       注入的状态函数（测试用）
        """
        if selection not in SELECTIONS:
            raise ValueError(
                f"未知 selection={selection!r}；可用：{_SELECTION_HELP}")
        if ranking not in RANKINGS:
            raise ValueError(
                f"未知 ranking={ranking!r}；可用：{_RANKING_HELP}")
        if selection in ("multifactor", "pr") and ranking != "score":
            # 打分型筛选的成果体现在分数上；用别的 ranking 会丢掉打分语义
            raise ValueError(
                f"selection={selection!r} 必须配 ranking='score'")
        if selection == "none" and ranking == "score":
            raise ValueError("selection='none' 无分数可用，不能用 ranking='score'")

        self.rules = rules
        self.selection = selection
        self.ranking = ranking
        self.top_k = int(top_k)
        self.seed = int(seed)
        self.root = root
        self.rebalance = rebalance
        self.caps = dict(caps) if caps else dict(POSITION_CAPS)
        self._liq = liq_fn or state_of
        self._liq_series = dict(liq_series) if liq_series else None
        self._last_key: str | None = None
        self._last_state: LiqState | None = None
        self.last_state: LiqState | None = None
        #: 每次调仓的候选池规模（诊断用：universe 太小则该格结论不可靠）
        self.last_pool_size: int = 0

    # ── 候选宇宙 ──
    def _snapshot(self, ymd: str,
                  exclude_bj: bool = True) -> dict[str, dict[str, float]]:
        """当日「数据可得全集」→ {code: {pe, pb, roe, dv}}（**不施加任何阈值**）。

        所有臂共用同一个宇宙，避免旧脚本"BASE 用行情全集 / 选股臂用基本面子集"
        的**数据可得性偏差**。
        """
        basics = daily_basic_of(
            ymd, self.root,
            columns=("ts_code", "trade_date", "close", "pe_ttm", "pb",
                     "dv_ttm", "total_mv"))
        if not basics:
            return {}
        roes = roe_pit_of(ymd, self.root)
        out: dict[str, dict[str, float]] = {}
        for code, row in basics.items():
            if exclude_bj and code.endswith(".BJ"):
                continue
            pe, pb, dv = (_finite(row.get("pe_ttm")), _finite(row.get("pb")),
                          _finite(row.get("dv_ttm")))
            entry = roes.get(code)
            if pe is None or pb is None or dv is None or entry is None:
                continue
            roe = _finite(entry[0])
            if roe is None:
                continue
            out[code] = {"pe": pe, "pb": pb, "roe": roe, "dv": dv}
        return out

    def _apply_selection(self, snap: dict[str, dict[str, float]],
                         params: Mapping
                         ) -> tuple[list[str], dict[str, float]]:
        """施加筛选 → (代码列表, {code: score})。score 仅 multifactor/pr 有值。"""
        if self.selection == "none":
            return sorted(snap), {}

        if self.selection == "current":
            pe_max = float(params["pe_max"])
            pb_max = float(params["pb_max"])
            roe_min = float(params["roe_min"])
            dv_min = float(params["dv_min"])
            picked = [c for c, m in snap.items()
                      if m["pe"] > 0 and m["pe"] < pe_max and m["pb"] < pb_max
                      and m["roe"] > roe_min and m["dv"] > dv_min]
            return sorted(picked), {}

        if self.selection == "pr":
            picked, scores = [], {}
            for code, m in snap.items():
                if m["pe"] <= 0 or m["roe"] <= 0:      # 亏损股 PR 无意义
                    continue
                pr = m["pe"] / m["roe"]
                if pr < PR_MAX:
                    picked.append(code)
                    scores[code] = pr                   # PR 越小越好
            return sorted(picked), scores

        # multifactor（方案 E）：四因子横截面百分位加总，**越小越好**
        picked = [c for c, m in snap.items()
                  if m["pe"] > 0 and m["roe"] > 0]      # PE/ROE 非正者不可打分
        if len(picked) < 3:
            return sorted(picked), {}
        scores = _multifactor_scores(snap, picked)
        return sorted(picked), scores

    def _apply_ranking(self, pool: Sequence[str],
                       snap: Mapping[str, dict[str, float]],
                       scores: Mapping[str, float], ymd: str) -> list[str]:
        """池内排序 → 有序代码列表（确定性）。"""
        pool = list(pool)
        if not pool:
            return []
        if self.ranking == "code":
            return sorted(pool)                     # FF-3：btf 现状实际行为
        if self.ranking == "pe_asc":
            return sorted(pool, key=lambda c: (snap[c]["pe"], c))
        if self.ranking == "score":
            return sorted(pool, key=lambda c: (scores[c], c))  # 升序：越小越好
        # random：**按日期派生**，不用全局 rng（保证可复现 + 与遍历顺序无关）
        rng = random.Random(f"{self.seed}:{ymd}")
        idx = list(pool)
        rng.shuffle(idx)
        return idx

    # ── 策略钩子 ──
    def _state_on(self, ymd: str) -> LiqState:
        if self._liq_series is not None:
            cached = self._liq_series.get(ymd)
            if cached is not None:
                return cached
        state = self._liq(ymd, self.rules.get("L0_LIQ"), root=self.root,
                          prev=self._last_state)
        self._last_state = state
        return state

    def on_close(self, ctx: StrategyContext, date: TradingDate) -> None:
        ymd = date.to_ymd()
        key = ymd[:6] if self.rebalance == "monthly" else ymd
        state = self._state_on(ymd)
        self.last_state = state
        if key == self._last_key:
            return
        self._last_key = key

        cap = float(self.caps.get(state.status, 0.0))
        if cap <= 0 or not self.top_k:
            ctx.submit_target(TargetPortfolio(date, {}))
            return

        snap = self._snapshot(ymd)
        self.last_pool_size = len(snap)
        if not snap:
            ctx.submit_target(TargetPortfolio(date, {}))
            return
        pool, scores = self._apply_selection(snap, self.rules.get("L2_filter"))
        if not pool:
            ctx.submit_target(TargetPortfolio(date, {}))
            return
        picks = self._apply_ranking(pool, snap, scores, ymd)[:self.top_k]
        if not picks:
            ctx.submit_target(TargetPortfolio(date, {}))
            return
        weight = min(cap / 100.0 / len(picks), 1.0)
        ctx.submit_target(TargetPortfolio(date, {s: weight for s in picks}))


def _multifactor_scores(snap: Mapping[str, dict[str, float]],
                        pool: Sequence[str]) -> dict[str, float]:
    """四因子横截面百分位排名 → 综合分（**越小越好**）。

    对应 08-21 报告方案 E 口径"PE+PB+ROE+股息率综合打分"：
      · PE / PB：**升序**百分位（越低越便宜 → 分越小越好）
      · ROE / 股息率：**降序**百分位（越高越好 → 取 1 - 升序百分位）

    用**百分位**而非原始值，避免量纲差异让单一因子主导。这是原报告没写、
    但打分法成立的必要前提，故在此显式留痕。

    取 Top 30% 的语义由调用方控制：`ranking="score"` 取前 top_k 只；
    当 `top_k ≈ 30% × 池宽` 时即等价于报告的 Top30% 行为。
    """
    def pct(field: str, ascending: bool) -> dict[str, float]:
        pairs = sorted(((snap[c][field], c) for c in pool),
                       key=lambda p: (p[0], p[1]))
        n = len(pairs)
        out: dict[str, float] = {}
        for i, (_v, code) in enumerate(pairs):
            pr = (i + 1) / n                     # 1..n → (0,1]
            out[code] = pr if ascending else 1.0 - pr
        return out

    r_pe = pct("pe", ascending=True)
    r_pb = pct("pb", ascending=True)
    r_roe = pct("roe", ascending=False)
    r_dv = pct("dv", ascending=False)
    return {c: (r_pe[c] + r_pb[c] + r_roe[c] + r_dv[c]) / 4.0 for c in pool}


__all__ = ["RANKINGS", "SELECTIONS", "L2AblationStrategy"]
