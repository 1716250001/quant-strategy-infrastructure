# -*- coding: utf-8 -*-
"""公司行动探针与离线清单（**CL-2 第二段 R1′/R2′/R6′**；19 号 §77-bis / §78.5 / §79.4）。

两条线（构建 → 消费）：

**① 构建**（`python -m btf.data.corp_action_probe --out DIR`）
    扫本地 `adj_factor` 跳变（`|chg−1| > 0.1%`）∩ `dividend` **无对应 `ex_date`**
    （dividend 年文件按**方案年度**归档、`ex_date` 最长滞后 5 年 ⇒ 须 5 年回看，
    见 `feed.py` / `parquet_reader.py`）⇒ 「疑配股 / 缩股事件清单」：
      - **向上跳变**（除权类：分红/送转/配股）⇒ 未解释者与 akshare
        `stock_pg_em` 快照（离线 CSV；**已落盘**）按 `ts_code ＋ 登记日窗口` 关联，
        补 **配股比例 / 配股价 / 上市日**；再做**双向校验**——由 `chg` 反推前收盘
        价 `X = chg·r·Pr / (1 + r − chg)` vs 实际收盘（**容差 0.5%**）；
        不一致 ⇒ **挂起披露**（`suspended`，**禁造数**）。
      - **向下跳变**（**缩股候选**）⇒ 非分红/送转/配股解释者（代理检测，§78.5），
        `consolidation = chg`（< 1）。

**② 消费**（feed adapter）：`load_actions()` 读清单 → `CorporateAction`
    （`rights_ratio` / `rights_price` / `rights_ex_date` / `rights_listing_date` /
    `consolidation_ratio`），供引擎除权日原子分发（R4′；§79.2 公式块）。

**方向铁律（§78.5，不得反）**：后复权价 = `close × adj_factor`；除权日股价
下跌 ⇒ `adj_factor` **上升**（分红/送转/配股）；**缩股**（股数减少、股价上涨）
⇒ `adj_factor` **向下**跳变。

**边界（INV-12 / §79.5 D）**：本模块只**读取** `adj_factor` 做**检测**，绝不修改
`adj_factor` 消费逻辑、绝不把复权价引入引擎记账；产物只落离线目录（禁写主库）。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import pathlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from btf.config.paths import CORP_ACTION_DIR, MARKET_DATA_DIR
from btf.domain.action import CorporateAction
from btf.domain.types import TradingDate

__all__ = [
    "PROBE_SCHEMA",
    "Jump",
    "build_artifact",
    "load_actions",
    "scan_jumps",
    "to_corporate_actions",
]

PROBE_SCHEMA = "corp_actions.v1"

#: 显著跳变阈值（§77-bis.5 / §78.5：naive 扫描 4,286 条噪声 → 加阈值后复核）
JUMP_THRESHOLD = 0.001
#: dividend 归档滞后回看年数（沿用 `feed.py::_DIV_MAX_LAG_YEARS` 实测 = 5）
DIV_LOOKBACK_YEARS = 5
#: 登记日 → 除权日窗口（§77-bis.2 实测 15/16 为 +9 日历日；早期年份时间轴更长，
#: 取 60 日窗口并在回执披露匹配口径）
RECORD_TO_EX_MAX_DAYS = 60
#: 双向校验容差（§79.4 R2′：反推前收盘 vs 实际收盘）
VALIDATION_TOL = 0.005
#: **R8′（19 号 §80.3 / §81.2 G2）**：反向校验的**放大因子守卫**——
#: `amp = chg(1+r)/|1+r−chg|`；配股价折价小（chg → 1+r）时分母趋零、误差被放大
#: `amp` 倍（实测挂起组中位 10.4x / 最大 39.5x）。`amp > AMP_GUARD` ⇒ **禁用反推**
#:（判定一律走正推；反向字段置空并标注）。
AMP_GUARD = 5.0
#: **R9′（§80.4 N17 / G3）**：缩股佐证容差 5% → **1%**——信号本身 ~0.2%，5% 容差
#: ＝ 信号的 25 倍 ⇒ 判据**恒真**（24 条「佐证」中 71% 为同标的重复触发）。
CONS_CORROB_TOL = 0.01
#: **R9′（G3）**：缩股比例门槛——真实缩股方案比例远离 1（A 股极低频、整数比居多）；
#: `ratio > CONS_MAX_RATIO` 的 0.99x「缩股」判为**非缩股噪声**（进 uncorroborated 披露）。
CONS_MAX_RATIO = 0.95

_RIGHTS_RATIO_RE = re.compile(r"^10配([\d.]+)$")


@dataclass(frozen=True)
class Jump:
    """`adj_factor` 相邻交易日跳变（`chg = af_t / af_{t−1}`）。"""

    symbol: str
    ex_date: str            # YYYYMMDD（跳变日 = 除权日）
    pre_date: str           # 跳变前一交易日（YYYYMMDD）
    chg: float


# ─────────────────────────────────────────────────────────────
# ① 本地扫描（R1′）
# ─────────────────────────────────────────────────────────────
def _ymd_to_date(ymd: str):
    from datetime import date

    return date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8]))


def scan_jumps(root: str | pathlib.Path, *,
               years: Sequence[int] | None = None,
               threshold: float = JUMP_THRESHOLD) -> list[Jump]:
    """扫 `adj_factor` **相邻交易日**跳变（跨年连续；|chg−1| > threshold）。

    方向：`chg > 1` ＝ 除权类（分红/送转/配股）；`chg < 1` ＝ **缩股候选**。
    只读 `adj_factor`（INV-12：检测用，消费逻辑不动）。
    """
    from btf.data.core import YearTableStore

    store = YearTableStore(pathlib.Path(root))
    y0 = years[0] if years else 1991
    y1 = years[-1] if years else datetime.now().year
    prev: dict[str, tuple[str, float]] = {}
    out: list[Jump] = []
    for year in range(y0, y1 + 1):
        t = store.read_file("adj_factor", year=year,
                            columns=["ts_code", "trade_date", "adj_factor"])
        if t is None or t.num_rows == 0:
            continue
        rows = t.to_pylist()
        rows.sort(key=lambda r: (r["ts_code"], r["trade_date"]))
        for r in rows:
            sym, ymd, af = r["ts_code"], r["trade_date"], r["adj_factor"]
            if not sym or not ymd or af is None:
                continue
            p = prev.get(sym)
            if p is not None and p[1]:
                chg = float(af) / float(p[1])
                if abs(chg - 1.0) > threshold:
                    out.append(Jump(symbol=sym, ex_date=ymd, pre_date=p[0],
                                    chg=chg))
            prev[sym] = (ymd, float(af))
    return out


def load_dividend_ex_dates(root: str | pathlib.Path, years: Sequence[int]
                           ) -> dict[str, set[str]]:
    """`(ts_code → {ex_date})`（全量 dividend，含回看年；ex_date 缺失行不入）。"""
    from btf.data.core import YearTableStore

    store = YearTableStore(pathlib.Path(root))
    out: dict[str, set[str]] = {}
    rows: list[dict[str, Any]] = []
    for year in range(years[0] - DIV_LOOKBACK_YEARS, years[-1] + 1):
        t = store.read_file("dividend", year=year,
                            columns=["ts_code", "ex_date"])
        if t is not None and t.num_rows:
            rows.extend(t.to_pylist())
    for r in rows:
        if r.get("ts_code") and r.get("ex_date"):
            out.setdefault(str(r["ts_code"]), set()).add(str(r["ex_date"]))
    return out


def _daily_index(root: str | pathlib.Path, year: int, symbols: Iterable[str]
                 ) -> dict[str, list[tuple[str, float, float]]]:
    """(symbol → 升序 [(trade_date, close, pre_close)])——**只含真实 bar**。

    关键：`adj_factor` 在**停牌日仍有行**（实测 600079.SH 2006-07-28..08-04
    有 adj_factor 无 daily）⇒ 「除权前收盘价」必须取 **`daily` 中 ≤ 除权日的
    最后真实 bar**，不能取 adj_factor 的前一行。
    **R19′（§85.3 Q-6）**：行元组扩为三元，附 `pre_close`（交易所公布的除权
    参考价——L1 取值的零推断来源）。
    """
    from btf.data.core import YearTableStore

    store = YearTableStore(pathlib.Path(root))
    want = set(symbols)
    out: dict[str, list[tuple[str, float, float]]] = {}
    t = store.read_file("daily", year=year,
                        columns=["ts_code", "trade_date", "close", "pre_close"])
    if t is None or t.num_rows == 0:
        return out
    for r in t.to_pylist():
        if r["ts_code"] in want and r["close"] is not None:
            pc = r.get("pre_close")
            out.setdefault(str(r["ts_code"]), []).append(
                (str(r["trade_date"]), float(r["close"]),
                 float(pc) if pc is not None else float("nan")))
    for rows in out.values():
        rows.sort()
    return out


def _last_close_lt(rows: Sequence[tuple[str, float]], ymd: str
                   ) -> tuple[str, float] | None:
    """**严格早于** ymd 的最后一条（＝除权前收盘价；防前视）。

    注意必须 *严格* `<`：除权日当天常有真实 bar（且已是**除权后**价，
    如中信 600030 除权日 close=24.15），把它当"前收盘"会让校验全盘错位。
    """
    best = None
    for d, c, *_ in rows:
        if d < ymd:
            best = (d, c)
        else:
            break
    return best


def _first_close_ge(rows: Sequence[tuple], ymd: str
                    ) -> tuple | None:
    for row in rows:
        if row[0] >= ymd:
            return row                      # 整行（含 pre_close——R19′ L1 来源）
    return None


# ─────────────────────────────────────────────────────────────
# ② akshare 快照关联（R2′）
# ─────────────────────────────────────────────────────────────
def _ts_code_of(code6: str, suffix_map: dict[str, str]) -> str:
    code6 = str(code6).zfill(6)
    if code6 in suffix_map:
        return suffix_map[code6]
    if code6.startswith("6"):
        return f"{code6}.SH"
    if code6.startswith(("0", "3")):
        return f"{code6}.SZ"
    if code6.startswith(("4", "8")):
        return f"{code6}.BJ"
    return f"{code6}.SZ"


def _stock_basic_suffix_map(root: str | pathlib.Path) -> dict[str, str]:
    """6 位代码 → ts_code（stock_basic 权威映射；缺失再走位数规则）。"""
    from btf.data.core import YearTableStore

    store = YearTableStore(pathlib.Path(root))
    out: dict[str, str] = {}
    t = store.read_file("stock_basic", columns=["ts_code"])
    if t is None:
        return out
    for r in t.to_pylist():
        ts = str(r.get("ts_code") or "")
        if len(ts) >= 9:
            out[ts.split(".")[0]] = ts
    return out


def load_akshare_snapshot(csv_path: str | pathlib.Path,
                          suffix_map: dict[str, str]) -> list[dict[str, Any]]:
    """东财 `stock_pg_em()` 快照（离线 CSV）→ 结构化记录（含 `ratio`）。"""
    raw = pathlib.Path(csv_path).read_bytes()
    text = None
    for enc in ("utf-8-sig", "gbk", "utf-8"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError(f"快照编码无法解码：{csv_path}")
    rows = list(csv.reader(text.splitlines()))
    head, body = rows[0], rows[1:]
    idx = {name: i for i, name in enumerate(head)}

    def _int_of(row: list[str], field: str) -> int | None:
        raw = (str(row[idx[field]]).strip().replace(",", "")
               if field in idx else "")
        try:
            return int(float(raw)) if raw else None
        except ValueError:
            return None

    out: list[dict[str, Any]] = []
    for r in body:
        if not r or not r[idx["股票代码"]]:
            continue
        m = _RIGHTS_RATIO_RE.match(str(r[idx["配股比例"]]).strip())
        if not m:
            continue
        out.append({
            "ts_code": _ts_code_of(r[idx["股票代码"]], suffix_map),
            "name": r[idx["股票简称"]],
            "ratio": float(m.group(1)) / 10.0,
            "price": float(r[idx["配股价"]]),
            "record_date": str(r[idx["股权登记日"]]).replace("-", "").strip(),
            "pay_start": str(r[idx["缴款起始日期"]]).replace("-", "").strip(),
            "pay_end": str(r[idx["缴款截止日期"]]).replace("-", "").strip(),
            "listing_date": str(r[idx["上市日"]]).replace("-", "").strip(),
            # **R13′（19 号 §82.2 N19/§82.4②）**：股本三要素——交易所按**实际发行
            # 比例**除权（认购率 <100%），名单是**名义比例**；二者之差是可观测
            # 物理量（非噪声、非造数）。仅随清单披露，**不改判定**（R15′/Q-6）。
            "shares_pre": _int_of(r, "配股前总股本"),
            "shares_issued": _int_of(r, "配股数量"),
            "shares_post": _int_of(r, "配股后总股本"),
        })
    return out


def _match_record(records: Sequence[dict[str, Any]], ex_ymd: str
                  ) -> dict[str, Any] | None:
    """`ts_code + 登记日窗口`：登记日 ∈ [ex−20d, ex]，取**最接近**者。"""
    ex = _ymd_to_date(ex_ymd)
    best: dict[str, Any] | None = None
    best_gap = None
    for rec in records:
        rd = rec.get("record_date") or ""
        if len(rd) != 8 or not rd.isdigit():
            continue
        gap = (ex - _ymd_to_date(rd)).days
        if not (0 <= gap <= RECORD_TO_EX_MAX_DAYS):
            continue
        if best is None or gap < best_gap:
            best, best_gap = rec, gap
    return best


def _validate_right_math(r: float, pr: float, chg: float,
                         x_act: float | None) -> dict[str, float | None]:
    """R8′ 配股校验的**纯数学核心**（19 号 §80.3；可单测——G2）。

    - **正推（判定依据）**：`chg_pred = X(1+r)/(X + r·Pr)`（X＝除权前实际收盘；
      放大因子 ≈1、数值稳定）；`forward_err = |chg_pred − chg| / chg`，
      `≤ VALIDATION_TOL` ⇒ validated。
    - **反向（仅辅助）**：`x_pred = chg·r·Pr/(1+r−chg)`；放大因子
      `amp = chg(1+r)/|1+r−chg|`，`amp > AMP_GUARD` ⇒ **禁反推**
      （`x_pred/rel_err` 置 None——旧判据正是被病态反推误杀 79 条真配股）。
    """
    chg_pred = (x_act * (1.0 + r) / (x_act + r * pr)
                if x_act and (x_act + r * pr) > 0 else None)
    fwd_rel = (abs(chg_pred - chg) / chg
               if chg_pred is not None and chg else None)
    denom = 1.0 + r - chg
    amp = abs(chg * (1.0 + r) / denom) if abs(denom) > 1e-9 else None
    reverse_ok = amp is not None and amp <= AMP_GUARD
    x_pred = (chg * r * pr / denom) if reverse_ok else None
    rel = (abs(x_pred - x_act) / x_act
           if x_pred is not None and x_act else None)
    return {"chg_pred": chg_pred, "forward_err": fwd_rel,
            "amp_factor": amp, "x_pred": x_pred, "rel_err": rel}


#: **R19′（19 号 §85.3）**：Q-6 单独登记的异常条目——`r_imp > r_nom` 反向偏离
#:（疑配股与送转同日或快照匹配错位）；**保持现状、不改口径**。
_PEX_ANOMALIES: dict[tuple[str, str], str] = {
    ("600480.SH", "20190620"):
        "r_imp=0.375 > r_nom=0.30（比值 1.25，唯一反向偏离）——疑配股与送转同日"
        "或快照匹配错位；Q-6 §85.3 单独登记，不改口径",
}


def _choose_pex(x_act: float | None, r_nom: float, pr: float,
                r_eff: float | None, ex_bar: tuple | None,
                guard_min: float = 0.85) -> tuple:
    """R19′（19 号 §85.3 Q-6）：`pex` **三级取值**——份额与除权价口径**分离**。

    - **L1 `exchange_preclose`**：除权日当日真实 bar 的 `pre_close`（交易所公布
      除权参考价，零推断；覆盖 175/176）；
    - **L2 `effective_ratio`**：无 bar 且股本可得 ⇒ `(X + r_eff·Pr)/(1+r_eff)`；
    - **L3 `nominal_fallback`**：现状口径 `(X + r_nom·Pr)/(1+r_nom)`。
    **守卫**：`r_eff / r_nom < guard_min` ⇒ 分母口径错（A+H/B：H 股不参与 A 股
    配股，如 002948.SZ 总股本含 H 股）⇒ **强制降级 L3** 并标注（禁静默采用）。
    份额 `M = floor(N × r_nom)` **不受本函数影响**（Q-4(a) 股东权利口径）。
    """
    guard = None
    if ex_bar is not None:
        pc = ex_bar[2]
        if pc == pc and pc > 0:                          # NaN/非正 ⇒ 不可用
            return pc, "exchange_preclose", None
    if (r_eff is not None and r_nom and x_act
            and r_eff / r_nom >= guard_min):
        return ((x_act + r_eff * pr) / (1.0 + r_eff), "effective_ratio", None)
    if r_eff is not None and r_nom and r_eff / r_nom < guard_min:
        guard = "denominator_mismatch_AH"
    pex = ((x_act + r_nom * pr) / (1.0 + r_nom)) if x_act else None
    return pex, "nominal_fallback", guard


def confirm_consolidations(root: str | pathlib.Path, *,
                           years: tuple[int, int] = (1990, 2026),
                           min_drop: float = 0.03,
                           max_ratio_gap: float = 0.01,
                           window: int = 10) -> dict[str, Any]:
    """**股本级缩股确证扫描**（R18′-a；19 号 §84.1/§85.1 判据，全区间 1990–2026）。

    真缩股的定义性后果：**总股本与复权因子同窗口同比例减少**（股东持股数同减）。
    判据（三者同时成立）：① `daily_basic.total_share` 单日减少 ≥ `min_drop`；
    ② `adj_factor` 同窗口（±`window` 日）向下跳变 ≥ `min_drop`（按 (标的,前值,新值)
    去重——同跳变多日重复是数据噪声，N17 同源）；③ 两比例差 ≤ `max_ratio_gap`。
    仅股本减少 ⇒ 回购注销/股改等（股东持股数不变，`adj_factor` 不动，不影响记账）；
    仅因子跳变 ⇒ 数据噪声。
    """
    from itertools import pairwise

    from btf.data.core import YearTableStore

    store = YearTableStore(pathlib.Path(root))
    y0, y1 = years

    def _scan(table: str, value_col: str) -> list[dict[str, Any]]:
        drops: list[dict[str, Any]] = []
        prev_tail: dict[str, tuple[str, float]] = {}
        for y in range(y0, y1 + 1):
            t = store.read_file(table, year=y,
                                columns=["ts_code", "trade_date", value_col])
            if t is None or t.num_rows == 0:
                continue
            series: dict[str, list[tuple[str, float]]] = {}
            for r in t.to_pylist():
                sym, td, v = (r.get("ts_code"), r.get("trade_date"),
                              r.get(value_col))
                if sym and td and v is not None:
                    series.setdefault(str(sym), []).append(
                        (str(td), float(v)))
            cur_tail: dict[str, tuple[str, float]] = {}
            for sym, rows in series.items():
                rows.sort()
                pairs = []
                if sym in prev_tail:
                    pairs.append((prev_tail[sym], rows[0]))
                pairs.extend(pairwise(rows))
                for (_d0, v0), (d1, v1) in pairs:
                    if v0 > 0 and v1 / v0 - 1.0 <= -min_drop:
                        drops.append({"symbol": sym, "date": d1,
                                      "value_pre": v0, "value_post": v1,
                                      "ratio": round(v1 / v0, 6)})
                cur_tail[sym] = rows[-1]
            prev_tail = cur_tail
        return drops

    share_drops = _scan("daily_basic", "total_share")
    # 复权因子向下跳变：按 (标的, 前值, 新值) 去重（同跳变多日重复＝噪声）
    adj_drops = _scan("adj_factor", "adj_factor")
    adj_jumps: dict[tuple, dict[str, Any]] = {}
    for d in adj_drops:
        key = (d["symbol"], round(d["value_pre"], 6), round(d["value_post"], 6))
        if key in adj_jumps:
            adj_jumps[key]["dates"].append(d["date"])
        else:
            adj_jumps[key] = {"symbol": d["symbol"], "ratio": d["ratio"],
                              "adj_pre": d["value_pre"],
                              "adj_post": d["value_post"],
                              "dates": [d["date"]]}

    from datetime import date as _date

    def _days(s: str) -> _date:
        return _date(int(s[:4]), int(s[4:6]), int(s[6:8]))

    confirmed: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for d in share_drops:
        rs = d["ratio"]
        best = None
        for j in adj_jumps.values():
            if j["symbol"] != d["symbol"]:
                continue
            gap = min(abs((_days(x) - _days(d["date"])).days)
                      for x in j["dates"])
            if (gap <= window and rs
                    and abs(j["ratio"] - rs) / rs <= max_ratio_gap
                    and (best is None or gap < best[0])):
                best = (gap, j)
        if best:
            j = best[1]
            confirmed.append({
                "symbol": d["symbol"], "ex_date": d["date"],
                "consolidation_ratio": rs,
                "share_pre": d["value_pre"], "share_post": d["value_post"],
                "adj_pre": j["adj_pre"], "adj_post": j["adj_post"],
                "adj_ratio": j["ratio"], "adj_dates": j["dates"],
                "ratio_gap": round(abs(j["ratio"] - rs) / rs, 6),
            })
        else:
            rejected.append({
                "symbol": d["symbol"], "date": d["date"], "ratio": rs,
                "share_pre": d["value_pre"], "share_post": d["value_post"],
                "reason": "share_reform_or_buyback",
                "note": ("股权分置改革集中期（2005-2007）：总股本减而股东持股数"
                         "不变，adj_factor 不动 ⇒ 非股东层面缩股"
                         if d["date"][:4] in ("2005", "2006", "2007") else
                         "无同窗口 adj_factor 向下跳变（股东持股数不变的股本"
                         "减少——回购注销/股改/吸收合并等）"),
            })
    return {"confirmed": confirmed, "rejected": rejected,
            "stats": {"share_drops": len(share_drops),
                      "adj_jumps_dedup": len(adj_jumps),
                      "confirmed": len(confirmed),
                      "rejected": len(rejected),
                      "years": [y0, y1], "min_drop": min_drop,
                      "max_ratio_gap": max_ratio_gap, "window": window}}


def build_artifact(root: str | pathlib.Path, snapshot_csv: str | pathlib.Path,
                   *, years: Sequence[int] | None = None,
                   threshold: float = JUMP_THRESHOLD) -> dict[str, Any]:
    """构建离线清单（R1′ 扫描 ＋ R2′ 关联/校验 ＋ R6′ 缩股代理）。"""
    y0 = years[0] if years else 1991
    y1 = years[-1] if years else datetime.now().year
    jumps = scan_jumps(root, years=[y0, y1], threshold=threshold)
    div_ex = load_dividend_ex_dates(root, [y0, y1])
    suffix_map = _stock_basic_suffix_map(root)
    snapshot = load_akshare_snapshot(snapshot_csv, suffix_map)
    by_sym: dict[str, list[dict[str, Any]]] = {}
    for rec in snapshot:
        by_sym.setdefault(rec["ts_code"], []).append(rec)

    up = [j for j in jumps if j.chg > 1.0]
    down = [j for j in jumps if j.chg < 1.0]
    up_explained = [j for j in up if j.ex_date in div_ex.get(j.symbol, ())]
    down_explained = [j for j in down if j.ex_date in div_ex.get(j.symbol, ())]
    up_cand = [j for j in up if j.ex_date not in div_ex.get(j.symbol, ())]
    down_cand = [j for j in down if j.ex_date not in div_ex.get(j.symbol, ())]

    # 真实 bar 索引（按年批处理；前收盘 = **≤ 除权日的最后真实 bar**）
    price_rows: dict[tuple[str, str], tuple[str, float]] = {}   # (sym, ex) → (date, close)
    resume_rows: dict[tuple[str, str], tuple[str, float]] = {}
    years_needed = sorted({int(j.ex_date[:4]) for j in (*up_cand, *down_cand)})
    for year in years_needed:
        items = [j for j in (*up_cand, *down_cand) if int(j.ex_date[:4]) == year]
        syms = {j.symbol for j in items}
        idx = _daily_index(root, year, syms)
        # 年初候选：并入上一年同标的（跨年取前收盘）
        if any(j.ex_date[4:] < "0301" for j in items):
            prev = _daily_index(root, year - 1, syms)
            for sym, rows in prev.items():
                idx.setdefault(sym, []).extend(rows)
                idx[sym].sort()
        for j in items:
            rows = idx.get(j.symbol, ())
            last = _last_close_lt(rows, j.ex_date)
            nxt = _first_close_ge(rows, j.ex_date)
            if last is not None:
                price_rows[(j.symbol, j.ex_date)] = last
            if nxt is not None:
                resume_rows[(j.symbol, j.ex_date)] = nxt

    rights, suspended, unmatched = [], [], []
    for j in up_cand:
        rec = _match_record(by_sym.get(j.symbol, ()), j.ex_date)
        if rec is None:
            unmatched.append({"symbol": j.symbol, "ex_date": j.ex_date,
                              "chg": round(j.chg, 6)})
            continue
        r, pr = rec["ratio"], rec["price"]
        last = price_rows.get((j.symbol, j.ex_date))
        x_act = last[1] if last else None
        # **R13′（N19）**：股本级披露——`r_effective` ＝ 配股数量/配股前总股本
        #（**实际发行比例**）；`subscription_ratio` ＝ r_eff/名义 r（认购率）。
        # **R19′（Q-6 §85.3）**：r_eff 亦作 `pex` L2 取值来源（份额仍用名义 r——
        # 两口径分离）；判定仍用名义 r（挂起披露口径不变）。
        r_eff = (rec["shares_issued"] / rec["shares_pre"]
                 if rec.get("shares_issued") and rec.get("shares_pre") else None)
        m_eff = (_validate_right_math(r_eff, pr, j.chg, x_act)
                 if r_eff is not None else None)
        # **R8′（19 号 §80.3 / §81.2 G1+G2）**：正推判定 ＋ 放大因子守卫
        #（数学核心抽到 `_validate_right_math`，可单测）。
        m = _validate_right_math(r, pr, j.chg, x_act)
        chg_pred = m["chg_pred"]
        fwd_rel = m["forward_err"]
        amp = m["amp_factor"]
        x_pred = m["x_pred"]
        rel = m["rel_err"]
        # **R19′（Q-6）**：`pex` 三级取值（L1 pre_close → L2 r_eff → L3 r_nom）
        # ＋ A+H/B 分母守卫（`r_eff/r_nom < 0.85` ⇒ 降级 L3 并标注）。
        nxt = resume_rows.get((j.symbol, j.ex_date))
        ex_bar = nxt if (nxt is not None and nxt[0] == j.ex_date) else None
        pex_val, pex_src, pex_guard = _choose_pex(x_act, r, pr, r_eff, ex_bar)
        item = {
            "symbol": j.symbol, "name": rec["name"], "ex_date": j.ex_date,
            "ratio": r, "price": pr, "record_date": rec["record_date"],
            "listing_date": rec["listing_date"] or None,
            "chg": round(j.chg, 6),
            # 正推（**判定依据**，G1）
            "chg_pred": round(chg_pred, 6) if chg_pred is not None else None,
            "forward_err": round(fwd_rel, 6) if fwd_rel is not None else None,
            # 放大因子（**两列标注**，G1/G2；>5 ⇒ 反推字段为空）
            "amp_factor": round(amp, 3) if amp is not None else None,
            # 反推（仅辅助；amp ≤ 5 才计算）
            "x_pred": round(x_pred, 4) if x_pred is not None else None,
            "x_actual": x_act,
            "x_actual_date": last[0] if last else None,
            "rel_err": round(rel, 6) if rel is not None else None,
            "status": ("validated" if fwd_rel is not None
                       and fwd_rel <= VALIDATION_TOL else "suspended"),
            # 股本级披露（R13′/N19）
            "r_effective": round(r_eff, 6) if r_eff is not None else None,
            "subscription_ratio": (round(r_eff / r, 6)
                                   if r_eff is not None and r else None),
            "forward_err_eff": (round(m_eff["forward_err"], 6)
                                if m_eff and m_eff["forward_err"] is not None
                                else None),
            # pex 三级取值（R19′/Q-6）
            "pex": round(pex_val, 6) if pex_val is not None else None,
            "pex_source": pex_src,
        }
        if pex_guard:
            item["pex_guard"] = pex_guard
        if (j.symbol, j.ex_date) in _PEX_ANOMALIES:
            item["anomaly"] = _PEX_ANOMALIES[(j.symbol, j.ex_date)]
        (rights if item["status"] == "validated" else suspended).append(item)

    # 缩股候选（R6′ 代理）：向下跳变 ∩ 非分红解释；**hfq 连续性佐证**
    # （close_resume ≈ close_pre / chg ⇒ 该跳变在价格侧自洽，方可建模）。
    # **R9′（19 号 §80.4 N17 / §81.2 G3）**：① 佐证容差 5% → **1%**（信号 ~0.2%，
    # 5% 恒真）；② **比例门槛 ratio ≤ 0.95**（0.99x 不对应任何真实缩股方案）；
    # ③ **同标的仅保留最早 1 次**（920375.BJ ×11 / 000011.SZ ×3 均为重复触发噪声）；
    # 未过门槛者**仍进 uncorroborated 披露**（带 reason，**不得删除**）。
    consolidations, cons_uncorroborated = [], []
    seen_syms: set[str] = set()
    for j in sorted(down_cand, key=lambda x: (x.symbol, x.ex_date)):
        last = price_rows.get((j.symbol, j.ex_date))
        nxt = resume_rows.get((j.symbol, j.ex_date))
        ok = False
        if last and nxt and last[1] > 0 and j.chg > 0:
            ok = abs(nxt[1] * j.chg / last[1] - 1.0) <= CONS_CORROB_TOL
        item = {"symbol": j.symbol, "ex_date": j.ex_date,
                "consolidation_ratio": round(j.chg, 6),
                "close_pre": last[1] if last else None,
                "close_resume": nxt[1] if nxt else None,
                "corroborated": ok}
        if j.chg > CONS_MAX_RATIO:
            reason = (f"ratio {j.chg:.6f} > {CONS_MAX_RATIO}"
                      f"（0.99x 非真实缩股方案——N17）")
        elif not ok:
            reason = f"佐证超容差 {CONS_CORROB_TOL} 或无复牌 bar"
        elif j.symbol in seen_syms:
            reason = "同标的重复触发（仅保留最早 1 次——N17）"
        else:
            reason = None
        if reason is None:
            seen_syms.add(j.symbol)
            consolidations.append(item)
        else:
            item["reason"] = reason
            cons_uncorroborated.append(item)

    # **R18′（19 号 §85.5）**：股本级缩股确证扫描（全区间 1990–2026）——
    # 价格代理只配做**候选生成**，判定走「股本 × 复权因子交叉」（§85.2 方法论定论）；
    # 确证者入 `consolidations_confirmed`（**默认建模**——三重证据，非造数）；
    # 未交叉的股本减少显式标注 `share_reform_or_buyback`（**禁**当缩股——R18′-e）。
    confirmation = confirm_consolidations(root)
    confirmed_keys = {(c["symbol"], c["ex_date"])
                      for c in confirmation["confirmed"]}
    for item in cons_uncorroborated:
        if (item["symbol"], item["ex_date"]) in confirmed_keys:
            item["proxy_captured"] = True
            item["note"] = ("价格代理已捕获本事件但佐证判据无法佐证；经股本级"
                            "确证入 consolidations_confirmed（R18′/§85.2）")

    snap_bytes = pathlib.Path(snapshot_csv).read_bytes()
    return {
        "schema": PROBE_SCHEMA,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source": {
            "adj_factor_root": str(root),
            "snapshot_csv": str(snapshot_csv),
            "snapshot_sha256": hashlib.sha256(snap_bytes).hexdigest(),
            "threshold": threshold,
            "years": [y0, y1],
            "div_lookback_years": DIV_LOOKBACK_YEARS,
        },
        "stats": {
            "jumps_total": len(jumps),
            "up_total": len(up), "down_total": len(down),
            "up_explained_by_dividend": len(up_explained),
            "down_explained_by_dividend": len(down_explained),
            "up_candidates": len(up_cand), "down_candidates": len(down_cand),
            "rights_validated": len(rights),
            "rights_suspended": len(suspended),
            "rights_unmatched": len(unmatched),
            "consolidation_candidates": len(down_cand),
            "consolidation_corroborated": len(consolidations),
            "consolidation_uncorroborated": len(cons_uncorroborated),
            "consolidations_confirmed": len(confirmation["confirmed"]),
        },
        "rights": rights,
        "rights_suspended": suspended,
        "rights_unmatched": unmatched,
        "consolidations": consolidations,
        "consolidations_confirmed": confirmation["confirmed"],
        "consolidations_uncorroborated": cons_uncorroborated,
        "consolidation_confirmation_report": confirmation,
    }


# ─────────────────────────────────────────────────────────────
# ③ 消费（feed adapter）
# ─────────────────────────────────────────────────────────────
_ACTIONS_CACHE: dict[str, dict[str, Any]] = {}


def load_actions(directory: str | pathlib.Path | None = None
                 ) -> dict[str, Any] | None:
    """读离线清单（进程内 memo）；缺文件 ⇒ `None`（调用方决定是否披露）。"""
    directory = pathlib.Path(directory or CORP_ACTION_DIR)
    path = directory / "corp_actions.json"
    key = str(path)
    if key in _ACTIONS_CACHE:
        return _ACTIONS_CACHE[key]
    if not path.exists():
        _ACTIONS_CACHE[key] = None
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    _ACTIONS_CACHE[key] = data
    return data


def consolidations_modeled() -> bool:
    """缩股代理事件是否**建模**（**默认不建**——19 号 §80.6 裁决：只披露、不建模）。

    裁决理由（§80.6）：① 事件未被交易所公告确证（「禁造数」同族）；② **N17**：
    佐证判据恒真、24 条中 71% 同标的重复触发、0.99x 非真实缩股 ⇒ 建模输入不可信；
    ③ 默认档不得让冻结口径（v77 三档）无谓漂移；④ 缩股极低频，只披露成本极低。

    `BTFTOOL_CORP_ACTIONS_CONSOLIDATIONS=on` ⇒ **显式开启**建模（对照实验用；
    R10′ 实测开启会让 v77 三档漂移 超额 −0.010~−0.048pp）。
    清单（`consolidations` ＋ `consolidations_uncorroborated`）**保留并披露**，
    不因默认 off 而删除；探针**不得关闭**（§80.6 配套要求）。
    """
    import os

    val = str(os.environ.get("BTFTOOL_CORP_ACTIONS_CONSOLIDATIONS", "off"))
    return val.strip().lower() in ("1", "on", "true", "yes")


def to_corporate_actions(data: dict[str, Any] | None, start: TradingDate,
                         end: TradingDate) -> list[CorporateAction]:
    """清单 → `CorporateAction`（区间过滤；配股 ＋ 缩股两类）。

    - 配股：`ex_date = 除权日`（本地 `adj_factor` 跳变真值）、`rights_ratio`
      （`10配X ⇒ X/10`）、`rights_price`、`rights_listing_date`；
      现金/送转字段为 0（**旧五字段语义不变**）。
    - 缩股：`consolidation_ratio = chg`（< 1 ⇒ `qty` 按比例减少，无现金流）。
    """
    if not data:
        return []
    lo, hi = start.to_ymd(), end.to_ymd()
    out: list[CorporateAction] = []
    for item in data.get("rights") or ():
        ex = str(item.get("ex_date") or "")
        if not (lo <= ex <= hi):
            continue
        # **R19′（Q-6）**：清单 L2（effective_ratio）的 `pex` 随事件下发——引擎仅在
        # **无 bar** 时采用（有 bar 真实收盘胜）；L1（有 bar）与 L3（与引擎公式同式）
        # 不下发，避免冗余/漂移。
        pex_ov = (float(item["pex"])
                  if item.get("pex_source") == "effective_ratio"
                  and item.get("pex") is not None else None)
        out.append(CorporateAction(
            symbol=str(item["symbol"]),
            ex_date=TradingDate.from_ymd(ex),
            pay_date=None,
            record_date=(TradingDate.from_ymd(str(item["record_date"]))
                         if item.get("record_date") else None),
            cash_div_per_share=0.0,
            stk_div_per_share=0.0,
            ann_date=None,
            rights_ratio=float(item.get("ratio") or 0.0),
            rights_price=float(item.get("price") or 0.0),
            rights_ex_date=TradingDate.from_ymd(ex),
            rights_listing_date=(TradingDate.from_ymd(str(item["listing_date"]))
                                 if item.get("listing_date") else None),
            pex_override=pex_ov,
        ))
    # **R18′-b**：`consolidations_confirmed`（股本级三重证据确证）**默认建模**——
    # 不受代理开关约束（确证 ≠ 造数；§85.2 方法论定论：判定走股本 × 因子交叉）。
    for item in data.get("consolidations_confirmed") or ():
        ex = str(item.get("ex_date") or "")
        if not (lo <= ex <= hi):
            continue
        out.append(CorporateAction(
            symbol=str(item["symbol"]),
            ex_date=TradingDate.from_ymd(ex),
            pay_date=None, record_date=None,
            cash_div_per_share=0.0, stk_div_per_share=0.0, ann_date=None,
            consolidation_ratio=float(item.get("consolidation_ratio") or 1.0),
            consolidation_source="confirmed",       # R22′：来源标记
        ))
    for item in (data.get("consolidations") or ()
                 if consolidations_modeled() else ()):
        ex = str(item.get("ex_date") or "")
        if not (lo <= ex <= hi):
            continue
        out.append(CorporateAction(
            symbol=str(item["symbol"]),
            ex_date=TradingDate.from_ymd(ex),
            pay_date=None, record_date=None,
            cash_div_per_share=0.0, stk_div_per_share=0.0, ann_date=None,
            consolidation_ratio=float(item.get("consolidation_ratio") or 1.0),
        ))
    out.sort(key=lambda a: (a.ex_date, a.symbol))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="公司行动探针（R1′/R2′/R6′）")
    ap.add_argument("--out", default=str(CORP_ACTION_DIR), help="清单输出目录")
    ap.add_argument("--root", default=str(MARKET_DATA_DIR), help="主库根")
    ap.add_argument("--snapshot", default=None, help="akshare 配股快照 CSV")
    ap.add_argument("--years", default=None, help="扫描年区间，如 1991-2026")
    args = ap.parse_args(argv)
    if not args.snapshot:
        print("[error] 需 --snapshot <akshare stock_pg_em CSV>（离线快照）")
        return 2
    years = None
    if args.years:
        y0, y1 = args.years.split("-")
        years = [int(y0), int(y1)]
    data = build_artifact(args.root, args.snapshot, years=years)
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "corp_actions.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2,
                               sort_keys=True) + "\n", encoding="utf-8")
    # **R18′-a**：确证扫描全报告归档（confirmed/rejected 全量，可复现）
    (out / "consolidations_confirmation.json").write_text(
        json.dumps(data.get("consolidation_confirmation_report") or {},
                   ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    st = data["stats"]
    print(f"[ok] 清单落盘：{path}")
    print(f"     跳变总 {st['jumps_total']}（向上 {st['up_total']} / 向下 "
          f"{st['down_total']}）｜分红解释：向上 {st['up_explained_by_dividend']}"
          f" / 向下 {st['down_explained_by_dividend']}")
    print(f"     配股候选 {st['up_candidates']} ⇒ 校验通过 "
          f"{st['rights_validated']} / 挂起 {st['rights_suspended']} / 无快照匹配 "
          f"{st['rights_unmatched']}；缩股候选 {st['consolidation_candidates']}")
    cr = data.get("consolidation_confirmation_report") or {}
    cst = cr.get("stats") or {}
    print(f"     缩股确证（股本级，{cst.get('years')}）：股本减少 "
          f"{cst.get('share_drops')} / 因子跳变(去重) {cst.get('adj_jumps_dedup')}"
          f" ⇒ 确证 {cst.get('confirmed')}（{[c['symbol'] + ' ' + c['ex_date'] for c in cr.get('confirmed') or []]}）"
          f" / 排除 {cst.get('rejected')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
