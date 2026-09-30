# -*- coding: utf-8 -*-
"""领域类型：TradingDate / AssetClass / Instrument / BoardRule（04 §8.2.1-8.2.2）。

零第三方依赖（03 §7.3 铁律 3）——仅 typing/dataclasses/enum/datetime。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

from btf.domain.cache import BoundedDict


class AssetClass(Enum):
    """资产类别（↗ 多资产扩展点）。"""

    STOCK = "stock"        # MVP
    ETF = "etf"            # MVP（H1：奇点战法=ETF 轮动）
    CONVERTIBLE = "cb"     # v0.5 可转债
    FUTURE = "future"      # v1.0 预留
    OPTION = "option"      # 远期预留


class EtfSubclass(Enum):
    """ETF 子类（终审 E2：T+N 与费率按子类分，非资产类单维）。"""

    STOCK = "stock"            # 股票型 ETF：T+1
    BOND = "bond"              # 债券（含可转债）ETF：T+0（池内 511180/511380 实例）
    GOLD = "gold"              # 黄金 ETF：T+0
    CROSS_BORDER = "cross"     # 跨境 ETF：T+0
    MONEY = "money"            # 货币 ETF：T+0


#: from_ymd 进程级 memo（B3 十年尺度：68 万次调用/年 ≈ 1.1s——交易日域
#: ≤1 万个值，缓存收益恒定；CP1 性能专项 2026-09-27）。
#: PF-9/P2-5（19 号附录 D.3）：原为**无界** dict（键=任意 8 位数字串——
#: 调用方传入非交易日/脏串即无限增长）；今改 LRU 有界（2 万条 ≫ 交易日域）。
_YMD_CACHE: BoundedDict[str, TradingDate] = BoundedDict(
    maxsize=20_000, name="ymd")


@dataclass(frozen=True, order=True)
class TradingDate:
    """交易日值对象（frozen，可排序可哈希）。

    内部 ISO date；与 Tushare YYYYMMDD 字符串的互转**只发生在数据适配层**。
    """

    iso: date

    # ── 构造便捷 ──
    @classmethod
    def from_iso(cls, y: int, m: int, d: int) -> TradingDate:
        return cls(date(y, m, d))

    @classmethod
    def from_ymd(cls, s: str) -> TradingDate:
        """'YYYYMMDD' → TradingDate（仅适配层使用；非 8 位数字串抛 ValueError）。

        进程级 memo：适配层逐 bar 调用（同日截面共享同一日期串），交易日域
        有限（≤1 万）→ 命中率恒定 100%。非法输入不缓存（异常路径照旧）。
        """
        cached = _YMD_CACHE.get(s) if type(s) is str else None
        if cached is not None:
            return cached
        if len(s) != 8 or not s.isdigit():
            raise ValueError(f"非法 YYYYMMDD: {s!r}")
        out = cls(date(int(s[:4]), int(s[4:6]), int(s[6:8])))
        if type(s) is str:
            _YMD_CACHE[s] = out
        return out

    def to_ymd(self) -> str:
        """→ 'YYYYMMDD'（仅适配层使用）。"""
        return self.iso.strftime("%Y%m%d")

    # ── 代理 date 的常用比较/运算（order=True 已提供 < <= > >=）──
    def __repr__(self) -> str:  # pragma: no cover
        return f"TradingDate({self.iso.isoformat()})"


# ── 日期互转**单一真源**（R4 剩余；19 号 §41.5「日期 helper 三份」）────
# 此前 `runtime._ymd`（'YYYY-MM-DD'→TradingDate）、`experiment.store._ymd`
# （鸭子→'YYYYMMDD'）、`data.state._ymd_to_date`（'YYYYMMDD'→date）三处各写
# 一份，语义漂移风险由本处收口：解析/格式化口径只有一处。
def parse_trading_date(value: Any) -> TradingDate:
    """任意日期形态 → `TradingDate`（适配层唯一入口）。

    接受：`TradingDate` / `date`（含 `datetime`，取日期部分）/ `'YYYYMMDD'` /
    `'YYYY-MM-DD'`（含其它单字符分隔符，如 `YYYY/MM/DD`）。
    """
    if isinstance(value, TradingDate):
        return value
    if isinstance(value, datetime):
        return TradingDate(value.date())
    if isinstance(value, date):
        return TradingDate(value)
    if isinstance(value, str):
        # 统一去分隔符后按 8 位数字校验（`from_ymd` 自带缓存与非法值报错）
        compact = value.strip().replace("-", "").replace("/", "")
        return TradingDate.from_ymd(compact)
    raise TypeError(f"无法解析为 TradingDate: {type(value).__name__}")


def ymd_of(value: Any) -> str:
    """任意日期形态 → `'YYYYMMDD'`（无分隔符）。

    `None` 与不可解析值按调用方语义处理：本函数只接受可解析形态（与
    `parse_trading_date` 同域），避免"静默返回空串"掩盖数据缺失。
    """
    return parse_trading_date(value).to_ymd()


@dataclass(frozen=True)
class Instrument:
    """金融工具唯一标识与静态属性（值对象：不可变、按代码全等）。

    退市股是一等公民：delist_date 非空者必须可进标的池（幸存者偏差治理）。
    """

    symbol: str                      # 标准代码（Tushare ts_code 口径，如 "000001.SZ"）
    asset_class: AssetClass
    name: str = ""
    board: str | None = None         # 板块："main"|"gem"|"star"|"bse"（股票）
    etf_subclass: EtfSubclass | None = None  # ETF 子类（E2：T+N 按此分）
    currency: str = "CNY"
    lot_size: int = 100              # 最小交易单位（股/份）
    price_tick: float = 0.01
    list_date: TradingDate | None = None
    delist_date: TradingDate | None = None


@dataclass(frozen=True)
class BoardRule:
    """板块/子类交易规则包（BrokerageModel 思想；规则按日期区间生效版本由数据层提供）。

    t_plus 语义（终审 E2 修正）：资产 × 子类双维——
        股票=1；股票型 ETF=1；债券/黄金/跨境/货币 ETF=0；可转债=0。
    """

    board: str
    t_plus: int = 1
    limit_up_pct: float | None = 0.10    # None=无涨跌幅制度（上市首日等）
    limit_down_pct: float | None = 0.10
    applies_to: AssetClass = AssetClass.STOCK
    etf_subclass: EtfSubclass | None = None


# ── 内置规则包（MVP；随注册表演进，PoC 阶段常量表足够）──
#: 主板：±10%，T+1
MAIN_BOARD = BoardRule(board="main")
#: 创业板：±20%，T+1
GEM_BOARD = BoardRule(board="gem", limit_up_pct=0.20, limit_down_pct=0.20)
#: 科创板：±20%，T+1
STAR_BOARD = BoardRule(board="star", limit_up_pct=0.20, limit_down_pct=0.20)
#: 北交所：±30%，T+1 [待验证：数据完备度]
BSE_BOARD = BoardRule(board="bse", limit_up_pct=0.30, limit_down_pct=0.30)
#: ETF 规则包（E2 子类分）：股票型 T+1 ±10%；债券/黄金/跨境/货币 T+0 ±10%
#: （跨境/商品 ETF 涨跌幅差异 [待验证：品种口径]）
ETF_STOCK_RULE = BoardRule(
    board="etf", t_plus=1, applies_to=AssetClass.ETF, etf_subclass=EtfSubclass.STOCK
)
ETF_T0_RULES: dict[EtfSubclass, BoardRule] = {
    sub: BoardRule(board="etf", t_plus=0, applies_to=AssetClass.ETF, etf_subclass=sub)
    for sub in (EtfSubclass.BOND, EtfSubclass.GOLD, EtfSubclass.CROSS_BORDER, EtfSubclass.MONEY)
}


def rule_for(instrument: Instrument) -> BoardRule:
    """规则查找（PoC 简版：静态表；v0.5 演进为日期区间生效版本）。"""
    if instrument.asset_class is AssetClass.ETF:
        sub = instrument.etf_subclass or EtfSubclass.STOCK
        return ETF_STOCK_RULE if sub is EtfSubclass.STOCK else ETF_T0_RULES[sub]
    return {
        "main": MAIN_BOARD, "gem": GEM_BOARD,
        "star": STAR_BOARD, "bse": BSE_BOARD,
    }.get(instrument.board or "main", MAIN_BOARD)


#: 跨境/境外类关键词（ETF 子类推断的名称口径）
_CROSS_WORDS = ("恒生", "港股", "纳斯达克", "标普", "道琼斯", "日经", "德国", "法国",
                "亚太", "中概", "海外", "跨境", "QDII", "美国", "香港")


def infer_etf_subclass(symbol: str, name: str = "") -> EtfSubclass:
    """ETF 子类推断（M3 任务 6.3；名称优先，代码段兜底）——决定 T+N（终审 E2）。

    债券/黄金/跨境/货币 → T+0；股票型 → T+1（`rule_for` 消费本结果）。
    """
    if any(word in name for word in _CROSS_WORDS):
        return EtfSubclass.CROSS_BORDER
    if "货币" in name or "现金" in name:
        return EtfSubclass.MONEY
    if "黄金" in name or "金ETF" in name:
        return EtfSubclass.GOLD
    if "债" in name:                       # 债券/可转债 ETF（511180/511380 实例）
        return EtfSubclass.BOND
    if symbol[:3] == "513":                # 沪市 QDII 代码段
        return EtfSubclass.CROSS_BORDER
    if symbol[:3] == "511":                # 沪市债券代码段（无「债」名者兜底）
        return EtfSubclass.BOND
    return EtfSubclass.STOCK


def infer_instrument(symbol: str, name: str = "") -> Instrument:
    """非 stock_basic 标的（ETF/LOF/未登记）静态身份推断（M3 任务 6.3 交付）。

    runtime 宇宙解析对 ETF 的兜底：资产类=ETF + 子类（T+N 由此定），
    取代 M1 的「默认 main/lot100」占位（511180/511380 → BOND → **T+0**）。
    """
    return Instrument(symbol, AssetClass.ETF, name=name, board="etf",
                      etf_subclass=infer_etf_subclass(symbol, name), lot_size=100)


__all__ = [
    "AssetClass",
    "BoardRule",
    "EtfSubclass",
    "Instrument",
    "TradingDate",
    "infer_etf_subclass",
    "infer_instrument",
    "rule_for",
]
