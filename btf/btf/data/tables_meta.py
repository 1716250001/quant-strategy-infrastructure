# -*- coding: utf-8 -*-
"""18 张核心表元数据登记（05 §9.4 数据字典的数字化）。

口径与架构文档一致（含终审勘误：E3 etf_limit=2019-06-26、E4 moneyflow=2008-01-02）；
主键/起点与 db_schema.json 快照对齐。单位换算在此声明、由 Feed 落地（1.4 契约）。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Layout(Enum):
    """Parquet 物理布局（实测：by_year 14 表 / single 2 表 / metadata 2 表）。"""

    BY_YEAR = "by_year"    # {表}/{YYYY}.parquet，一年一文件含全市场
    SINGLE = "single"      # {表}/{表名}.parquet（宏观/静态/小快照）
    METADATA = "metadata"  # {主库}/metadata/{表名}.parquet


@dataclass(frozen=True)
class UnitConversion:
    """单位换算声明（05 §9.4：库内原始单位 → 契约单位）。"""

    field: str
    factor: float          # 契约值 = 原始值 × factor
    raw_unit: str
    contract_unit: str


@dataclass(frozen=True)
class TableMeta:
    """单表元数据：布局 / 日期列 / 契约起点 / 主键 / 单位换算 / 回测用途。"""

    name: str
    layout: Layout
    date_col: str                  # 主时间列（YYYYMMDD 字符串）
    start: str | None              # 实测数据起点（YYYYMMDD；None=静态表）
    pk: tuple[str, ...]            # 逻辑去重主键
    conversions: tuple[UnitConversion, ...] = ()
    note: str = ""


# ── 股票行情与状态（T-01..T-08）──
DAILY = TableMeta(
    name="daily", layout=Layout.BY_YEAR, date_col="trade_date", start="19901219",
    pk=("ts_code", "trade_date"),
    conversions=(
        UnitConversion("vol", 100.0, "手", "股"),
        UnitConversion("amount", 1000.0, "千元", "元"),
    ),
    note="个股日线·不复权；撮合基准价",
)
ADJ_FACTOR = TableMeta(
    name="adj_factor", layout=Layout.BY_YEAR, date_col="trade_date", start="19910703",
    pk=("ts_code", "trade_date"), note="复权因子；复权一律消费侧计算",
)
STK_LIMIT = TableMeta(
    name="stk_limit", layout=Layout.BY_YEAR, date_col="trade_date", start="20080102",
    pk=("ts_code", "trade_date"), note="涨跌停价（回测撮合硬约束）",
)
SUSPEND_D = TableMeta(
    name="suspend_d", layout=Layout.BY_YEAR, date_col="trade_date", start="20000104",
    pk=("trade_date", "ts_code"), note="停复牌；口径=当天在表即停牌",
)
NAMECHANGE = TableMeta(
    name="namechange", layout=Layout.BY_YEAR, date_col="start_date", start="19901201",
    pk=("ts_code", "start_date"),
    note="曾用名=ST 唯一来源（name 含 'ST' ∧ start≤d<end）",
)
DIVIDEND = TableMeta(
    name="dividend", layout=Layout.BY_YEAR, date_col="ex_date", start="19901231",
    pk=("ts_code", "end_date", "ann_date", "div_proc"),
    note="分红送转；仅'实施'态进入引擎；pay_date 两时点（N7-A/E5）",
)
STOCK_BASIC = TableMeta(
    name="stock_basic", layout=Layout.METADATA, date_col="list_date", start=None,
    pk=("ts_code",), note="股票列表；宇宙快照与退市口径（L/D/P）",
)
TRADE_CAL = TableMeta(
    name="trade_cal", layout=Layout.METADATA, date_col="cal_date", start=None,
    pk=("exchange", "cal_date"), note="交易日历唯一真源（至 2027-12-31）",
)

# ── 指数与估值（T-09/T-10/T-15/T-16）──
INDEX_DAILY = TableMeta(
    name="index_daily", layout=Layout.BY_YEAR, date_col="trade_date", start="19931014",
    pk=("ts_code", "trade_date"),
    conversions=(
        UnitConversion("vol", 100.0, "手", "股"),
        UnitConversion("amount", 1000.0, "千元", "元"),
    ),
    note="指数日线·基准收益",
)
DAILY_BASIC = TableMeta(
    name="daily_basic", layout=Layout.BY_YEAR, date_col="trade_date", start="19901219",
    pk=("ts_code", "trade_date"),
    conversions=(UnitConversion("total_mv", 10000.0, "万元", "元"),),
    note="每日估值；v7.7 L2 硬筛子数据源（MVP，H1）",
)
INDEX_WEIGHT = TableMeta(
    name="index_weight", layout=Layout.SINGLE, date_col="trade_date", start=None,
    pk=("index_code", "con_code", "trade_date"), note="指数成分权重（月末快照）",
)
INDEX_DAILYBASIC = TableMeta(
    name="index_dailybasic", layout=Layout.BY_YEAR, date_col="trade_date", start="20040102",
    pk=("ts_code", "trade_date"), note="指数每日估值；v7.7 估值定锚（MVP，H1）",
)

# ── 事件与资金（T-11/T-13/T-14/T-17）──
NEW_SHARE = TableMeta(
    name="new_share", layout=Layout.SINGLE, date_col="ipo_date", start="20160122",
    pk=("ts_code",), note="IPO；次新股过滤",
)
MONEYFLOW = TableMeta(
    name="moneyflow", layout=Layout.BY_YEAR, date_col="trade_date", start="20080102",
    pk=("ts_code", "trade_date"),
    conversions=(UnitConversion("net_mf_amount", 10000.0, "万元", "元"),),
    note="个股资金流向；v7.7 买点双条件（MVP，H1）；起点 2008-01-02（终审 E4）",
)
FINA_INDICATOR = TableMeta(
    name="fina_indicator", layout=Layout.BY_YEAR, date_col="end_date", start="19901231",
    pk=("ts_code", "end_date"), note="财务指标；ROE_WAA（年报缓存口径，PIT 按 ann_date）",
)
FX_DAILY = TableMeta(
    name="fx_daily", layout=Layout.BY_YEAR, date_col="trade_date", start="19380104",
    pk=("ts_code", "trade_date"),
    note="外汇/贵金属；奇点 XAU 依赖（backlog，R12；缺 BRLCNY 已知）",
)

# ── ETF（T-12，H1：奇点战法第一验收场景）──
FUND_DAILY = TableMeta(
    name="fund_daily", layout=Layout.BY_YEAR, date_col="trade_date", start="19980407",
    pk=("ts_code", "trade_date"),
    conversions=(
        UnitConversion("vol", 100.0, "手", "份"),
        UnitConversion("amount", 1000.0, "千元", "元"),
    ),
    note="ETF/基金日线；列同构 daily",
)
ETF_LIMIT = TableMeta(
    name="etf_limit", layout=Layout.BY_YEAR, date_col="trade_date", start="20190626",
    pk=("ts_code", "trade_date"),
    note="ETF 涨跌停价；起点 2019-06-26（终审 E3）——更早由 BoardRule 计算回退并披露",
)

#: 全部 18 表注册表（05 §9.4：T-01..T-18——14 BY_YEAR / 2 SINGLE / 2 METADATA）
TABLES: dict[str, TableMeta] = {
    t.name: t for t in (
        DAILY, ADJ_FACTOR, STK_LIMIT, SUSPEND_D, NAMECHANGE, DIVIDEND,
        STOCK_BASIC, TRADE_CAL, INDEX_DAILY, DAILY_BASIC, INDEX_WEIGHT,
        INDEX_DAILYBASIC, NEW_SHARE, MONEYFLOW, FINA_INDICATOR, FX_DAILY,
        FUND_DAILY, ETF_LIMIT,
    )
}

# ─────────────────────────────────────────────────────────────
# 标的路由（V3-1；M3 任务 6.3）——股票 / ETF·LOF / 指数 三表判定
# ------------------------------------------------------------
# 歧义处理（实证，必须双维判定）：`000xxx.SH` 是**沪市指数段**（上证指数
# 000001.SH），而 `000xxx.SZ` 是**深市主板股票**（平安银行 000001.SZ）
# ——后缀与代码段各自都不足以区分。
# ─────────────────────────────────────────────────────────────
#: 指数专用后缀（中证 / 国证）
INDEX_SUFFIXES = frozenset({"CSI", "CNI"})
#: 指数代码段（399/93x=深证与中证行业主题、H3x=国证、98x=国证产业）
INDEX_PREFIXES = ("399", "93", "H3", "98")
#: 沪市股票代码段（B 股 900 亦归 daily）
STOCK_PREFIXES = ("600", "601", "603", "605", "688", "689", "900")
#: 深市 / 北交所股票代码段前缀（两位）
STOCK_SHORT = frozenset({"00", "30", "20", "43", "83", "87", "92"})


def daily_table_of(symbol: str) -> str:
    """标的路由：股票 `daily` / ETF·LOF `fund_daily` / 指数 `index_daily`。"""
    code, _, suffix = symbol.partition(".")
    if suffix in INDEX_SUFFIXES or code.startswith(INDEX_PREFIXES):
        return "index_daily"
    if code.startswith("000") and suffix == "SH":
        return "index_daily"
    if code.startswith(STOCK_PREFIXES) or code[:2] in STOCK_SHORT:
        return "daily"
    return "fund_daily"                       # ETF/LOF（51x/56x/58x/159x/16x/50x）


__all__ = ["INDEX_PREFIXES", "INDEX_SUFFIXES", "STOCK_PREFIXES", "STOCK_SHORT",
           "TABLES", "Layout", "TableMeta", "UnitConversion", "daily_table_of"]
