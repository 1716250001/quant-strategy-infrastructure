# -*- coding: utf-8 -*-
"""奇点战法信号层（M3 任务 6.3；18 号 H1 第一验收场景）。

真源（**不复制清单**——18 号 6.3 明文纪律）：
    `代码/strategies/qidian.py`（赤潮信号层）：标的池 QIDIAN_POOL、阈值常量
    `代码/indicators.py`：标准 KDJ 参考实现（仅测试对账时引用）

本模块以**纯 Python**（零 numpy/pandas——依赖预算 8/8 已满）复刻：
    - 日线 → ISO 周线 **W-FRI 自聚合**（等价 pandas `to_period("W-FRI")`）
    - 标准 KDJ（N=9 / M1=M2=3；K、D 初值 50；RSV 之 SMA 平滑；J=3K−2D）
    - CCI（N=20；TP=(H+L+C)/3；0.015×平均绝对偏差；零偏差 → 0.0）
    - 信号：买入 J≤−5 或 CCI≤−150；卖出 J≥110 或 CCI≥150（**买入优先**）
    - **双重确认**：ETF 与其配对指数同向触发方成立

对账（B1，`tests/regression/test_qidian_l3.py`）：同输入下与真源实现信号
一致率 **100%**；B2 名单按真源注释分组机检（14 推荐 / 23 双回测 / 4 行业 /
2 可转债）；B3 ETF 撮合 T+0（511180/511380）经 `domain.infer_instrument`。

分层（03 §7.3）：strategy 层只依赖 domain/config + 结构化 ctx。
"""
from __future__ import annotations

import importlib
import importlib.util
import re
import sys
import types
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from btf.config.paths import QIDIAN_REF
from btf.domain.contracts import CONTRACT_VERSION
from btf.domain.types import TradingDate
from btf.strategy.base import StrategyBase
from btf.strategy.rebalance import TargetPortfolio

#: 信号字面量（与真源报告口径一致）
BUY, SELL = "买入", "卖出"
#: 周五（W-FRI 决策日）
FRIDAY = 4
#: 池级别注释分组标记（真源 → 分组名单；**不复制代码清单**）
GROUP_MARKERS: tuple[tuple[str, str], ...] = (
    ("recommended", "推荐操作池"),
    ("dual_backtest", "双回测一致优秀"),
    ("industry_scan", "行业ETF扫描新增"),
    ("convertible_bond", "可转债ETF新增"),
)
_POOL_KEY = re.compile(r'^\s*"(\d{6}\.[A-Z]{2})"\s*:\s*\{')


# ─────────────────────────────────────────────────────────────
# ① 真源加载（常量运行时读取，不落副本）
# ─────────────────────────────────────────────────────────────
def _stub(name: str, **attrs: Any) -> None:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module


def _ensure_reference_deps() -> None:
    """屏蔽真源的取数重依赖（tushare 等）——只消费常量/纯函数。

    真依赖可导入时保持原样（不干扰宿主环境）；不可导入时以桩模块占位。
    """
    for package in ("common", "fetch"):
        try:
            importlib.import_module(package)
        except Exception:
            _stub(package, __path__=[])
    try:
        importlib.import_module("common.paths")
    except Exception:
        _stub("common.paths", FUND_DAILY_DIR="")
    try:
        importlib.import_module("fetch.base")
    except Exception:
        _stub("fetch.base", get_pro=lambda *a, **k: None)


def load_reference(path: str | Path | None = None) -> types.ModuleType:
    """动态加载真源信号层（只读；返回模块，不复制其常量）。"""
    src = Path(path or QIDIAN_REF)
    if not src.is_file():
        raise FileNotFoundError(f"奇点信号源缺失：{src}")
    _ensure_reference_deps()
    spec = importlib.util.spec_from_file_location("btf_qidian_reference", src)
    if spec is None or spec.loader is None:                # pragma: no cover
        raise ImportError(f"无法加载奇点信号源：{src}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pool_of(reference: types.ModuleType) -> dict[str, dict[str, Any]]:
    """标的池（真源 QIDIAN_POOL；保持声明顺序）。"""
    return dict(reference.QIDIAN_POOL)


@dataclass(frozen=True, slots=True)
class SignalRule:
    """信号阈值（真源常量；**不硬编码**到 btf）。"""

    buy_j: float
    buy_cci: float
    sell_j: float
    sell_cci: float


@dataclass(frozen=True, slots=True)
class IndicatorParams:
    """指标参数（真源常量）。"""

    kdj_n: int
    kdj_m1: int
    kdj_m2: int
    cci_n: int
    weeks_lookback: int


def rules_of(reference: types.ModuleType) -> SignalRule:
    return SignalRule(
        buy_j=float(reference.BUY_J_THRESHOLD),
        buy_cci=float(reference.BUY_CCI_THRESHOLD),
        sell_j=float(reference.SELL_J_THRESHOLD),
        sell_cci=float(reference.SELL_CCI_THRESHOLD),
    )


def params_of(reference: types.ModuleType) -> IndicatorParams:
    return IndicatorParams(
        kdj_n=int(reference.KDJ_N), kdj_m1=int(reference.KDJ_M1),
        kdj_m2=int(reference.KDJ_M2), cci_n=int(reference.CCI_N),
        weeks_lookback=int(reference.WEEKS_LOOKBACK),
    )


def pool_groups(path: str | Path | None = None) -> dict[str, list[str]]:
    """按真源**注释分组**解析名单（B2 机检）：分组不得在 btf 复制。

    真源以 `# ── 推荐操作池…` / `# ── 双回测一致优秀…` / 新增注释划分四组，
    本函数以行级来源归属（最近的前置分组注释）还原，避免清单双源。
    """
    src = Path(path or QIDIAN_REF)
    if not src.is_file():
        raise FileNotFoundError(f"奇点信号源缺失：{src}")
    groups: dict[str, list[str]] = {name: [] for name, _ in GROUP_MARKERS}
    current: str | None = None
    for line in src.read_text(encoding="utf-8").splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            for name, marker in GROUP_MARKERS:
                if marker in stripped:
                    current = name
            continue
        match = _POOL_KEY.match(line)
        if match and current is not None:
            groups[current].append(match.group(1))
    return groups


# ─────────────────────────────────────────────────────────────
# ② 周线自聚合（W-FRI）与指标（纯 Python 复刻）
# ─────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class WeeklyBar:
    """周线（W-FRI）。"""

    week_end: str          # YYYYMMDD（周五）
    open: float
    high: float
    low: float
    close: float
    volume: float


def week_end_of(ymd: str) -> str:
    """W-FRI 周末（周五）日期——等价 pandas `Period(..., "W-FRI").end_time`。

    规则：周五及之后（周六/周日）归下一周五；周一至周四归本周五。
    """
    day = date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8]))
    return (day + timedelta(days=(FRIDAY - day.weekday()) % 7)).strftime("%Y%m%d")


def weekly_bars(bars: Sequence[Any]) -> list[WeeklyBar]:
    """日线序列（Bar 或同字段对象）→ 周线（open=首 / high=max / low=min /
    close=末 / volume=和），按周末日升序。"""
    buckets: dict[str, list[tuple[str, Any]]] = {}
    for bar in bars:
        raw = bar.date
        ymd = raw.to_ymd() if hasattr(raw, "to_ymd") else str(raw).replace("-", "")
        buckets.setdefault(week_end_of(ymd), []).append((ymd, bar))
    out: list[WeeklyBar] = []
    for week_end in sorted(buckets):
        group = sorted(buckets[week_end], key=lambda item: item[0])
        rows = [bar for _, bar in group]
        out.append(WeeklyBar(
            week_end=week_end,
            open=float(rows[0].open),
            high=max(float(b.high) for b in rows),
            low=min(float(b.low) for b in rows),
            close=float(rows[-1].close),
            volume=sum(float(b.volume) for b in rows),
        ))
    return out


def calc_kdj(high: Sequence[float], low: Sequence[float],
             close: Sequence[float], params: IndicatorParams
             ) -> tuple[float, float, float]:
    """标准 KDJ（对齐真源 `calc_kdj(smooth=True)`）→ (K, D, J)。

    样本不足 → (50, 50, 50)；窗口内 high==low → RSV=50（真源同口径）。
    """
    n, m1, m2 = params.kdj_n, params.kdj_m1, params.kdj_m2
    length = len(close)
    if length < n:
        return 50.0, 50.0, 50.0
    rsv = [50.0] * length
    for i in range(n - 1, length):
        window_low = min(low[i - n + 1:i + 1])
        window_high = max(high[i - n + 1:i + 1])
        rsv[i] = ((close[i] - window_low) / (window_high - window_low) * 100.0
                  if window_high != window_low else 50.0)
    k_arr = [50.0] * length
    d_arr = [50.0] * length
    for i in range(1, length):
        k_arr[i] = (k_arr[i - 1] * (m1 - 1) + rsv[i]) / m1
        d_arr[i] = (d_arr[i - 1] * (m2 - 1) + k_arr[i]) / m2
    return k_arr[-1], d_arr[-1], 3.0 * k_arr[-1] - 2.0 * d_arr[-1]


def calc_cci(high: Sequence[float], low: Sequence[float],
             close: Sequence[float], params: IndicatorParams) -> float:
    """CCI（对齐真源 `calc_cci`）：最新一根；样本不足或零偏差 → 0.0。"""
    n = params.cci_n
    if len(close) < n:
        return 0.0
    tp = [(high[i] + low[i] + close[i]) / 3.0 for i in range(len(close))]
    window = tp[-n:]
    mean = sum(window) / n
    deviation = sum(abs(value - mean) for value in window) / n
    if deviation == 0:
        return 0.0
    return (tp[-1] - mean) / (0.015 * deviation)


def signal_of(j: float, cci: float, rules: SignalRule) -> str | None:
    """单标的信号（**买入优先**，对齐真源 `check_signal_detail`）。"""
    if j <= rules.buy_j or cci <= rules.buy_cci:
        return BUY
    if j >= rules.sell_j or cci >= rules.sell_cci:
        return SELL
    return None


def dual_signal(etf_signal: str | None, index_signal: str | None) -> str | None:
    """双重确认：ETF 与配对指数**同向**触发方成立（否则 None）。"""
    if etf_signal is None or etf_signal != index_signal:
        return None
    return etf_signal


def signal_at(etf_weekly: Sequence[WeeklyBar],
              index_weekly: Sequence[WeeklyBar],
              rules: SignalRule, params: IndicatorParams,
              week_end: str) -> str | None:
    """指定周末日的双确认信号（截断至该周，无前视）。"""
    etf = [w for w in etf_weekly if w.week_end <= week_end]
    index = [w for w in index_weekly if w.week_end <= week_end]
    min_weeks = max(params.kdj_n, params.cci_n)
    if len(etf) < min_weeks or len(index) < min_weeks:
        return None
    etf_high = [w.high for w in etf]
    etf_low = [w.low for w in etf]
    etf_close = [w.close for w in etf]
    idx_high = [w.high for w in index]
    idx_low = [w.low for w in index]
    idx_close = [w.close for w in index]
    return dual_signal(
        signal_of(calc_kdj(etf_high, etf_low, etf_close, params)[2],
                  calc_cci(etf_high, etf_low, etf_close, params), rules),
        signal_of(calc_kdj(idx_high, idx_low, idx_close, params)[2],
                  calc_cci(idx_high, idx_low, idx_close, params), rules),
    )


def kdj_j_series(high: Sequence[float], low: Sequence[float],
                 close: Sequence[float], params: IndicatorParams) -> list[float]:
    """J 值**序列**（与 `calc_kdj` 同口径：K/D 初值 50、SMA 平滑）。

    与逐前缀调用 `calc_kdj(…[:k+1])` 逐项相等——平滑递推锚定在序列首元素
    （初值 50），故一次 O(n·窗口) 扫描即得全序列（免 O(n²) 重算；周五决策
    每次都要全序列，逐周重算不可行）。
    """
    n, m1, m2 = params.kdj_n, params.kdj_m1, params.kdj_m2
    length = len(close)
    rsv = [50.0] * length
    for i in range(n - 1, length):
        window_low = min(low[i - n + 1:i + 1])
        window_high = max(high[i - n + 1:i + 1])
        rsv[i] = ((close[i] - window_low) / (window_high - window_low) * 100.0
                  if window_high != window_low else 50.0)
    k_arr = [50.0] * length
    d_arr = [50.0] * length
    for i in range(1, length):
        k_arr[i] = (k_arr[i - 1] * (m1 - 1) + rsv[i]) / m1
        d_arr[i] = (d_arr[i - 1] * (m2 - 1) + k_arr[i]) / m2
    return [3.0 * k_arr[i] - 2.0 * d_arr[i] for i in range(length)]


def cci_series(high: Sequence[float], low: Sequence[float],
               close: Sequence[float], params: IndicatorParams) -> list[float]:
    """CCI 序列（滚动 n 期；样本不足 → 0.0，与 `calc_cci` 同口径）。"""
    n = params.cci_n
    tp = [(high[i] + low[i] + close[i]) / 3.0 for i in range(len(close))]
    out = [0.0] * len(tp)
    for i in range(n - 1, len(tp)):
        window = tp[i - n + 1:i + 1]
        mean = sum(window) / n
        deviation = sum(abs(value - mean) for value in window) / n
        out[i] = 0.0 if deviation == 0 else (tp[i] - mean) / (0.015 * deviation)
    return out


def dual_signal_series(etf_weekly: Sequence[WeeklyBar],
                       index_weekly: Sequence[WeeklyBar],
                       rules: SignalRule, params: IndicatorParams
                       ) -> list[tuple[str, str | None]]:
    """逐周双确认信号 → [(week_end, signal|None)]（按 ETF 周次对齐）。

    无前视：第 k 周只用 ≤ week_end 的数据；指数按「≤ 该周」的最后一个周次
    取值（两序列周次通常一致，缺周时以最近可得周代替）。
    """
    etf_j = kdj_j_series([w.high for w in etf_weekly],
                         [w.low for w in etf_weekly],
                         [w.close for w in etf_weekly], params)
    etf_c = cci_series([w.high for w in etf_weekly],
                       [w.low for w in etf_weekly],
                       [w.close for w in etf_weekly], params)
    idx_j = kdj_j_series([w.high for w in index_weekly],
                         [w.low for w in index_weekly],
                         [w.close for w in index_weekly], params)
    idx_c = cci_series([w.high for w in index_weekly],
                       [w.low for w in index_weekly],
                       [w.close for w in index_weekly], params)
    min_weeks = max(params.kdj_n, params.cci_n)
    out: list[tuple[str, str | None]] = []
    pos = -1
    for i, week in enumerate(etf_weekly):
        while (pos + 1 < len(index_weekly)
               and index_weekly[pos + 1].week_end <= week.week_end):
            pos += 1
        if i + 1 < min_weeks or pos + 1 < min_weeks:
            out.append((week.week_end, None))
            continue
        out.append((week.week_end, dual_signal(
            signal_of(etf_j[i], etf_c[i], rules),
            signal_of(idx_j[pos], idx_c[pos], rules))))
    return out


# ─────────────────────────────────────────────────────────────
# ③ 策略类（双重确认 → 目标组合）
# ─────────────────────────────────────────────────────────────
class QidianStrategy(StrategyBase):
    """奇点战法（周线 KDJ/CCI + ETF/指数双确认）。

    决策时点：每周五收盘（W-FRI）；`bars_provider` 缺省走 `ctx.history`
    （ETF/指数日线须由数据层提供——fund_daily/index_daily 接入见留痕）。

    持仓语义：某 ETF 最近一次**双确认信号**为买入 → 持有（等权 weight）；
    为卖出 → 空仓；无信号（历史不足）→ 不参与。
    """

    #: S1 契约声明（v0.5.1 起 runtime 对策略做版本协商——19 号 P1-2/EX-2）
    contract_version = CONTRACT_VERSION

    def __init__(self, *, reference_path: str | Path | None = None,
                 weight: float = 0.1, decision_weekday: int = FRIDAY,
                 bars_provider: Callable[[Any, str, TradingDate], Sequence[Any]]
                 | None = None,
                 pool: Mapping[str, Any] | None = None,
                 **_ignored: Any) -> None:
        self.reference = load_reference(reference_path)
        self.pool = dict(pool or pool_of(self.reference))
        self.rules = rules_of(self.reference)
        self.params = params_of(self.reference)
        self.weight = float(weight)
        self.decision_weekday = int(decision_weekday)
        self._bars_provider = bars_provider
        #: 降级登记：配对指数无可用周线（如 H30315.CSI 源端仅 close 无 OHLC）
        #: → 该 ETF 不参与信号（不静默，经 `degraded_notes()` 披露）
        self.skipped_index: dict[str, str] = {}

    # ── 数据 ──
    def _daily(self, ctx: Any, symbol: str, date: TradingDate) -> Sequence[Any]:
        lookback = self.params.weeks_lookback * 5 + 20
        if self._bars_provider is not None:
            return self._bars_provider(ctx, symbol, date)
        return ctx.history(symbol, date, n_bars=lookback)

    def _weekly(self, ctx: Any, symbol: str, date: TradingDate) -> list[WeeklyBar]:
        return weekly_bars(self._daily(ctx, symbol, date))

    # ── 决策 ──
    def desired(self, ctx: Any, date: TradingDate) -> set[str]:
        """持有意向集合（无前视：仅用 date 及之前数据）。

        信号序列**一次扫描**（O(n·窗口)）取「最近一次非空信号」，避免逐周
        重算（O(n²)）——本方法每周五决策都调用，逐周重算在 200 周 × 43 池
        规模下不可行（实测量级差）。
        """
        min_weeks = max(self.params.kdj_n, self.params.cci_n)
        held: set[str] = set()
        for symbol, info in self.pool.items():
            indexes = list(info.get("indices") or [])
            if not indexes:
                continue
            etf_weekly = self._weekly(ctx, symbol, date)
            index_weekly: list[WeeklyBar] = []
            for entry in indexes:
                candidate = self._weekly(ctx, entry["code"], date)
                if len(candidate) >= min_weeks:
                    index_weekly = candidate
                    break
            if len(index_weekly) < min_weeks:
                # 逐次覆写（非 setdefault）：回测早段周线不足属暂时态，
                # 后续可用时须注销——否则降级披露虚报为"全部不可用"
                self.skipped_index[symbol] = "配对指数无可用 OHLC 或周线不足"
                continue
            self.skipped_index.pop(symbol, None)
            series = dual_signal_series(etf_weekly, index_weekly,
                                        self.rules, self.params)
            latest = next((signal for _week, signal in reversed(series)
                           if signal is not None), None)
            if latest == BUY:
                held.add(symbol)
        return held

    def degraded_notes(self) -> tuple[str, ...]:
        """降级披露（报告消费）：配对指数不可用 → 该 ETF 不参与信号。"""
        if not self.skipped_index:
            return ()
        codes = sorted(self.skipped_index)
        shown = ", ".join(codes[:8]) + (" 等" if len(codes) > 8 else "")
        return (f"配对指数不可用（无 OHLC / 周线不足）：{len(codes)} 只 ETF "
                f"不参与信号——{shown}",)

    def on_close(self, ctx: Any, date: TradingDate) -> None:
        if date.iso.weekday() != self.decision_weekday:
            return
        desired = self.desired(ctx, date)
        targets = ({sym: self.weight for sym in sorted(desired)}
                   if desired else {})
        ctx.submit_target(TargetPortfolio(date, targets))


__all__ = [
    "BUY",
    "FRIDAY",
    "GROUP_MARKERS",
    "SELL",
    "IndicatorParams",
    "QidianStrategy",
    "SignalRule",
    "WeeklyBar",
    "calc_cci",
    "calc_kdj",
    "cci_series",
    "dual_signal",
    "dual_signal_series",
    "kdj_j_series",
    "load_reference",
    "params_of",
    "pool_groups",
    "pool_of",
    "rules_of",
    "signal_at",
    "signal_of",
    "week_end_of",
    "weekly_bars",
]
