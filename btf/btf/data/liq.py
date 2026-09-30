# -*- coding: utf-8 -*-
"""L0-LIQ 流动性状态机（M3 任务 6.2；赤潮 v7.7 §L0-LIQ）。

判定（与 md_core/market_state.py::get_l0_liq_state 同口径）：
    CRISIS = 跌停家数 ≥ down_crisis
             OR 中证2000 ≤ small_crisis
             OR (上证 ≤ sh_crisis AND 跌停占比 ≥ ratio_crisis)
    WATCH（仅当非 CRISIS）= 上证 ≤ sh_watch
             OR 跌停家数 ≥ down_watch
             OR 跌停占比 ≥ ratio_watch
             OR 中证2000 ≤ small_watch
    否则 NORMAL

RECOVERY / 确认期（规则源 Z-6 澄清=唯一口径；仓位上限见 `POSITION_CAPS`）：
    单日函数无法判定（md_core docstring 明示"由调用方结合连续状态序列判断"）
    ——本模块以**原始判定序列**实现两段窗口（`prev_raw` 由调用方回灌）：

    ① **入闸确认期**（`confirm_days`，默认 3）：原始 CRISIS 结束后，需
       **连续 3 个交易日原始判定均为 NORMAL**（任何 CRISIS/WATCH 使计数归零，
       含 WATCH 当日）方确认恢复；**确认期内最终状态 = CRISIS**（仓位上限 0、
       禁止新增风险）——治"救市脉冲假恢复"（规则源"救市脉冲不自动恢复"）。
    ② **恢复期**（`recovery_days`，默认 3）：确认完成后 3 个交易日为
       RECOVERY（≤1 成），期满回 NORMAL；期间出现 WATCH 会打断平静计数，
       其后需重新满足 3 日确认（保守方向，与①同族）。

    最终状态单调恢复：`CRISIS →（确认期 CRISIS×3）→ RECOVERY×3 → NORMAL`
    ——不会在危机后直接回到 NORMAL 的 5 成上限（19 号 §21.3 的核心裁决点）。

数据（主库只读）：
    指数涨跌幅：index_daily（000001.SH 上证 / 932000.CSI 中证2000）
    跌停统计 ：stk_limit ∩ daily（分母=有涨跌停价且当日有行情的样本数）

留痕（口径差异）：
    - 中证2000（932000.CSI）基期 2023-08，早段缺失 → 小盘分支 skip（fail-closed）；
    - 跌停判定容差 1e-6（与 md_core 一致；btf/data/state.py 用 1e-4，二者
      用途不同——本模块对账赤潮口径，state.py 供引擎撮合）。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from btf.config.paths import MARKET_DATA_DIR

if TYPE_CHECKING:                     # 仅类型注解（运行期惰性 import，无环）
    from btf.data.core import YearTableStore


def _store(root: Path | None) -> YearTableStore:
    """本模块取数口（EX-4 完全体）：**只经 core**，不再直呼 `parquet_reader`。"""
    from btf.data import core as _core

    return _core.YearTableStore(Path(root or MARKET_DATA_DIR))

#: 状态常量（`status` 取值 = 仓位上限/策略消费依据）
NORMAL, WATCH, CRISIS, RECOVERY = "NORMAL", "WATCH", "CRISIS", "RECOVERY"
#: 机器相位（`phase`；Z-6：确认期状态归属 = CRISIS 的实现记忆）
IDLE, CONFIRM = "IDLE", "CONFIRM"
#: 规则源仓位上限（成 → 百分比；RECOVERY 按规则"≤1 成逐步恢复"）
POSITION_CAPS = {NORMAL: 50.0, WATCH: 20.0, CRISIS: 0.0, RECOVERY: 10.0}

IDX_SH = "000001.SH"
IDX_SMALL = "932000.CSI"
_TOL = 1e-6


@dataclass(frozen=True)
class LiqState:
    """单日 L0-LIQ 状态（triggers 记录触发项，供报告/断言）。

    `raw`：当日**原始判定**（NORMAL/WATCH/CRISIS，仅由阈值决定）；
    `status`：**叠加确认期与恢复期后**的最终状态（仓位上限与策略消费的依据）。
    二者在确认期内**不同**（raw=NORMAL/WATCH 而 status=CRISIS）——同时披露
    两个字段，避免"用最终态反推原始态"的歧义（Z-6 澄清的落地要求）。
    `phase` / `phase_days`：状态机**记忆**（IDLE / CONFIRM / RECOVERY +
    在当前相位已过天数）——单步递推用（`state_of(prev=…)`），亦为审计证据。
    """

    date: str
    status: str
    sh_pct: float | None
    small_pct: float | None
    down_cnt: int
    down_ratio: float
    triggers: tuple[str, ...]
    raw: str = NORMAL
    phase: str = IDLE
    phase_days: int = 0


def index_pct(symbol: str, date_ymd: str, root: Path | None = None
              ) -> float | None:
    """指数当日涨跌幅（%）；缺失/无行情 → None（fail-closed）。"""
    table = _store(root).read_range(
        "index_daily", date_ymd, date_ymd,
        columns=["ts_code", "trade_date", "pct_chg"])
    for row in table.to_pylist():
        if row.get("ts_code") == symbol and str(row.get("trade_date")) == date_ymd:
            value = row.get("pct_chg")
            return None if value is None else float(value)
    return None


def limit_stats(date_ymd: str, root: Path | None = None) -> tuple[int, int, float]:
    """跌停统计 → (跌停家数, 样本总数, 跌停占比%)。"""
    root = Path(root or MARKET_DATA_DIR)
    limits = _store(root).read_range(
        "stk_limit", date_ymd, date_ymd,
        columns=["ts_code", "trade_date", "down_limit", "up_limit"])
    limits_by_code = {r["ts_code"]: r for r in limits.to_pylist()
                      if str(r.get("trade_date")) == date_ymd}
    if not limits_by_code:
        return 0, 0, 0.0
    daily = _store(root).read_range(
        "daily", date_ymd, date_ymd, columns=["ts_code", "trade_date", "close"])
    total = down = 0
    for row in daily.to_pylist():
        if str(row.get("trade_date")) != date_ymd:
            continue
        lim = limits_by_code.get(row["ts_code"])
        if not lim:
            continue
        down_limit, close = lim.get("down_limit"), row.get("close")
        if down_limit is None or close is None or not down_limit > 0:
            continue
        total += 1
        if close <= down_limit + _TOL:
            down += 1
    return down, total, (down / total * 100.0 if total else 0.0)


def classify(*, sh_pct: float | None, small_pct: float | None, down_cnt: int,
             down_ratio: float, thresholds: dict) -> tuple[str, tuple[str, ...]]:
    """纯判定（无 I/O，可单测）：(状态, 触发项)。"""
    sh_crisis = float(thresholds["sh_crisis"])
    sh_watch = float(thresholds["sh_watch"])
    small_crisis = float(thresholds["small_crisis"])
    small_watch = float(thresholds["small_watch"])
    down_crisis = int(thresholds["down_crisis"])
    down_watch = int(thresholds["down_watch"])
    ratio_crisis = float(thresholds["ratio_crisis"])
    ratio_watch = float(thresholds["ratio_watch"])

    triggers: list[str] = []
    if down_cnt >= down_crisis:
        triggers.append(f"down_cnt≥{down_crisis}")
    if small_pct is not None and small_pct <= small_crisis:
        triggers.append(f"small≤{small_crisis}")
    if (sh_pct is not None and sh_pct <= sh_crisis
            and down_ratio >= ratio_crisis):
        triggers.append(f"sh≤{sh_crisis}且ratio≥{ratio_crisis}")
    if triggers:
        return CRISIS, tuple(triggers)

    if sh_pct is not None and sh_pct <= sh_watch:
        triggers.append(f"sh≤{sh_watch}")
    if down_cnt >= down_watch:
        triggers.append(f"down_cnt≥{down_watch}")
    if down_ratio >= ratio_watch:
        triggers.append(f"ratio≥{ratio_watch}")
    if small_pct is not None and small_pct <= small_watch:
        triggers.append(f"small≤{small_watch}")
    if triggers:
        return WATCH, tuple(triggers)
    return NORMAL, ()


def state_of(date_ymd: str, thresholds: dict, *, root: Path | None = None,
             prev: LiqState | None = None,
             recovery_days: int = 3, confirm_days: int = 3) -> LiqState:
    """单日状态（含确认期 + RECOVERY 两段序列判定）——**单日入口**（对账/抽查用）。

    `prev`：**前一交易日的 `LiqState`**（单步递推；`None` = 序列起点/IDLE）。
    机器记忆（`phase` / `phase_days`）随状态回传，故调用方只需保留**上一日**
    状态——无需拼历史序列，也不会因"久远的危机"误触发再确认（见 `_finalize`）。

    回测主链路请用 `precompute_liq_series`（19 号报告 P0-1：本函数为取
    4 个标量读整年表；每日调用 = 十年 760s 白烧）。二者须语义等值——
    回归断言 `test_liq_precompute.py::test_matches_state_of`。
    """
    root = Path(root or MARKET_DATA_DIR)
    sh_pct = index_pct(IDX_SH, date_ymd, root)
    small_pct = index_pct(IDX_SMALL, date_ymd, root)
    down_cnt, _total, down_ratio = limit_stats(date_ymd, root)
    return _finalize(date_ymd, thresholds, sh_pct, small_pct, down_cnt,
                     down_ratio, prev, recovery_days, confirm_days)


def _finalize(date_ymd: str, thresholds: dict, sh_pct: float | None,
              small_pct: float | None, down_cnt: int, down_ratio: float,
              prev: LiqState | None, recovery_days: int,
              confirm_days: int) -> LiqState:
    """判定 + 确认期/恢复期叠加（`state_of` 与预计算共用的**唯一**口径）。

    状态机（规则源 Z-6 澄清 = 唯一口径；**单步递推**，记忆 = `phase` +
    `phase_days`）：

        原始判定 raw = classify(...)（NORMAL / WATCH / CRISIS）
        raw == CRISIS        → status=CRISIS，phase=CONFIRM，days=0
        raw == WATCH  ∧ phase==IDLE     → status=WATCH（普通 WATCH，≤2 成）
        raw == WATCH  ∧ phase∈{CONFIRM,RECOVERY}
                             → status=**CRISIS**（确认/恢复被打断 ⇒ 重新计时）
        raw == NORMAL ∧ phase==IDLE     → status=NORMAL
        raw == NORMAL ∧ phase==CONFIRM  → days+1 < C ? CRISIS : RECOVERY
        raw == NORMAL ∧ phase==RECOVERY → days+1 < R ? RECOVERY : NORMAL
        其中 C = `confirm_days`(3)、R = `recovery_days`(3)。

    序列恒为 `CRISIS → CRISIS×（C−1）（确认期）→ RECOVERY×R → NORMAL`：
    **危机后不会直接跳回 NORMAL 的 5 成上限**（19 号 §21.3 的裁决点），
    仓位上限单调恢复（0 → 1 成 → 5 成）。

    与"按历史计数"的等价性说明：确认期 = 「原始判定连续 C 日非 CRISIS 且非
    WATCH」，第 C 个平静日**当日入闸**（status 即为 RECOVERY）；WATCH 打断即
    归零重计（当日按 CRISIS，0 成）。递推写法避免了"拿久远危机当上下文"的
    误判：一旦走完恢复期（phase=IDLE），其后任何 WATCH/NORMAL 都按普通状态处理。

    修复/演进留痕：
        · 2026-09-28（X-1）删除自我维持子句 `or RECOVERY in prev_statuses[:1]`
          （原致 RECOVERY 十年占 27.1%、最长 182 日）；
        · 2026-09-29（**Z-6 / X-1 入闸侧对齐**）确认期归属定为 **CRISIS**
          （规则源写明"确认期内状态归属 = CRISIS（0 成）"），
          原"CRISIS 次日即入 RECOVERY"的最简窗口**取消**。
    """
    raw, triggers = classify(sh_pct=sh_pct, small_pct=small_pct,
                             down_cnt=down_cnt, down_ratio=down_ratio,
                             thresholds=thresholds)
    phase = prev.phase if prev is not None else IDLE
    days = prev.phase_days if prev is not None else 0

    if raw == CRISIS:
        status, phase, days = CRISIS, CONFIRM, 0
    elif raw == WATCH:
        if phase == IDLE:
            status, phase, days = WATCH, IDLE, 0
        else:
            # 确认期/恢复期被打断（"连续 C 日无危机**且无 WATCH**"被破坏）
            status, phase, days = CRISIS, CONFIRM, 0
    elif phase == CONFIRM:
        if days + 1 < confirm_days:
            status, days = CRISIS, days + 1              # 入闸确认期（0 成）
        else:
            status, phase, days = RECOVERY, RECOVERY, 0  # 第 C 个平静日入闸
    elif phase == RECOVERY:
        if days + 1 < recovery_days:
            status, days = RECOVERY, days + 1            # 恢复期（≤1 成）
        else:
            status, phase, days = NORMAL, IDLE, 0
    else:                                                # IDLE + NORMAL
        status, phase, days = NORMAL, IDLE, 0

    return LiqState(date=date_ymd, status=status, sh_pct=sh_pct,
                    small_pct=small_pct, down_cnt=down_cnt,
                    down_ratio=down_ratio, triggers=triggers,
                    raw=raw, phase=phase, phase_days=days)


# ─────────────────────────────────────────────────────────────
# 区间预计算（19 号报告 P0-1 / §9.2）：十年 760s → 目标 < 5s
# ─────────────────────────────────────────────────────────────
def _limit_counts_by_day(t: object, tol: float) -> dict[str, tuple[int, int]]:
    """Arrow 向量化逐日跌停统计（stk_limit ⋈ daily on (ts_code, trade_date)）。

    口径与 `limit_stats` **逐字对齐**：仅统计同时有 down_limit 与 close 且
    down_limit > 0 的样本；`close <= down_limit + tol` 记跌停。
    实现差异仅在手段（`index_in` 对齐 + `take` 取值 + group_by 计数，
    替代逐行 Python 循环）——结果等值由回归测试钉住。

    实现选型（本机单年实测 2020）：复合键 hash join 0.50s / `index_in`+take
    ≈0.41s——取后者（少一次整表物化）。
    """
    import pyarrow as pa
    import pyarrow.compute as pc

    limits, daily = t

    def _key(tbl):
        return pc.binary_join_element_wise(
            tbl.column("ts_code").cast(pa.large_string()),
            tbl.column("trade_date").cast(pa.large_string()),
            pa.scalar("|", pa.large_string()))

    down_col = limits.column("down_limit")
    close_col = daily.column("close")
    # 有效性先行（口径：down_limit 有效且 > 0；close 有效）
    lim = limits.filter(pc.and_(pc.is_valid(down_col),
                                pc.greater(down_col, 0)))
    dl = daily.filter(pc.is_valid(close_col))
    if not lim.num_rows or not dl.num_rows:
        return {}
    idx = pc.index_in(_key(dl), value_set=_key(lim))
    # 未命中语义：pyarrow `index_in` 对未命中返回 **null**；部分版本/路径可能
    # 返回 -1——两者都排除（`take(-1)` 会静默取末行 → 计数错乱，实测踩过）
    mask = pc.and_(pc.is_valid(idx),
                   pc.greater_equal(pc.fill_null(idx, -1), 0))
    if not pc.any(mask).as_py():
        return {}
    sel = pc.filter(idx, mask)
    aligned = pa.table({
        "trade_date": pc.filter(dl.column("trade_date"), mask),
        "close": pc.filter(close_col.cast(pa.float64()), mask),
        # 索引基准 = **过滤后**的 lim（sel 由 _key(lim) 的 value_set 生成）
        "down_limit": pc.take(lim.column("down_limit").cast(pa.float64()), sel),
    })

    def _counts(tbl) -> dict[str, int]:
        grouped = tbl.group_by("trade_date").aggregate([("close", "count")])
        return dict(zip(grouped.column("trade_date").to_pylist(),
                        grouped.column("close_count").to_pylist(), strict=True))

    total = _counts(aligned)
    hit = aligned.filter(pc.less_equal(
        aligned.column("close"),
        pc.add(aligned.column("down_limit"), pa.scalar(tol))))
    down = _counts(hit)
    return {day: (int(down.get(day, 0)), int(cnt)) for day, cnt in total.items()}


def precompute_liq_series(
    start_ymd: str, end_ymd: str, thresholds: dict, *,
    root: Path | None = None, recovery_days: int = 3, confirm_days: int = 3,
    store: YearTableStore | None = None,
) -> dict[str, LiqState]:
    """一次性算整个回测区间的 LIQ 序列（19 号报告 §9.2；P0-1 直接解）。

    成本结构（对比 `state_of` 逐日）：每表**每年装载一次**（`YearTableStore`
    缓存 + Arrow 向量化），逐日只剩 O(1) 查值与纯函数判定。

    语义等值锚点：对任意 ymd ∈ [start, end]，
    `precompute[ymd] == state_of(ymd, thresholds, prev_raw=历史原始序列)`
    逐字段相等（`tests/perf/test_liq_precompute.py` 回归钉住）——历史序列用
    `LiqState.raw` 回灌（不是最终 `status`，见 `state_of` docstring）。
    """
    from btf.data.core import YearTableStore

    root = Path(root or MARKET_DATA_DIR)
    store = store or YearTableStore(root, max_entries=8)
    y0, y1 = int(start_ymd[:4]), int(end_ymd[:4])

    # ① 交易日域（A 股交易日 = daily 表日索引），逐年滚动装载
    days: list[str] = []
    for year in range(y0, y1 + 1):
        days.extend(d for d in store.days("daily", year)
                    if start_ymd <= d <= end_ymd)
    days.sort()

    # ② 指数涨跌幅：小标的集整年取（Arrow is_in；对比 index_pct 整表遍历）
    pct: dict[tuple[str, str], float] = {}
    for year in range(y0, y1 + 1):
        pct.update(store.values_by_symbol_day(
            "index_daily", year, [IDX_SH, IDX_SMALL], "pct_chg"))

    # ③ 逐日跌停统计：逐年 Arrow join + group_by（业务口径留在本模块）
    #    免排序装载（join/group_by 不要求有序）+ **逐年并行**（pyarrow 释放
    #    GIL；年表数据各自独立只读）
    #    共享状态口径订正（X-9，19 号 §17.4.4）：各年**共享同一个 store**
    #    （缓存/计数器），原注释"无共享状态"与事实不符；store 现已加锁
    #    （`YearTableStore._lock`，IO 在锁外保留并行收益）+ 装载双检，
    #    故并发下 `load_count` 断言可靠。
    years = list(range(y0, y1 + 1))

    def _year_counts(year: int) -> dict[str, tuple[int, int]]:
        limits = store.load_year(
            "stk_limit", year, ("ts_code", "trade_date", "down_limit"),
            sorted_rows=False)[0]
        daily = store.load_year(
            "daily", year, ("ts_code", "trade_date", "close"),
            sorted_rows=False)[0]
        if limits is None or daily is None:
            return {}
        return _limit_counts_by_day((limits, daily), _TOL)

    counts: dict[str, tuple[int, int]] = {}
    if len(years) > 1:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=min(6, len(years))) as pool:
            for part in pool.map(_year_counts, years):
                counts.update(part)
    else:
        for year in years:
            counts.update(_year_counts(year))

    # ④ 逐日判定（纯函数；确认期 + RECOVERY 两段与 state_of 同口径）
    #    单步递推：只需上一日状态（`phase`/`phase_days` 即机器记忆）——O(1)/日
    out: dict[str, LiqState] = {}
    prev: LiqState | None = None
    for day in days:
        down_cnt, total = counts.get(day, (0, 0))
        ratio = down_cnt / total * 100.0 if total else 0.0
        state = _finalize(day, thresholds, pct.get((IDX_SH, day)),
                          pct.get((IDX_SMALL, day)), down_cnt, ratio,
                          prev, recovery_days, confirm_days)
        out[day] = state
        prev = state
    return out


__all__ = ["CONFIRM", "CRISIS", "IDLE", "IDX_SH", "IDX_SMALL", "NORMAL",
           "POSITION_CAPS", "RECOVERY", "WATCH", "LiqState", "classify",
           "index_pct", "limit_stats", "precompute_liq_series", "state_of"]
