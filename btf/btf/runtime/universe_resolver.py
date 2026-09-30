# -*- coding: utf-8 -*-
"""**UniverseResolver** —— 标的宇宙解析（R4 拆分；原 `BTFRuntime._resolve_*`）。

为什么拆出来：宇宙解析有三种口径（explicit / all / index）+ 两条回退路径 +
两条强制披露（跨期新增、回退口径），混在 `build()` 里时任何一处调整都要在
长方法中定位；且它**只依赖 `feed` 能力与一个披露收集器**，不依赖整个 runtime
⇒ 可独立构造、可独立测试、可替换。

对外契约：`resolve()` 返回 `(instruments, monthly_index_universe | None)`——
第二项仅 `source=index` 时不为 None（由调用方决定是否回写到门面状态）。
"""
from __future__ import annotations

import logging
from collections.abc import Mapping, MutableSequence
from typing import Any

from btf.config.validation import ConfigError
from btf.domain.types import (
    Instrument,
    TradingDate,
    infer_instrument,
    parse_trading_date,
    rule_for,
)

logger = logging.getLogger(__name__)

__all__ = ["UniverseResolver"]


class UniverseResolver:
    """标的宇宙解析器（explicit / all / index）。"""

    def __init__(
        self,
        feed: Any,
        *,
        notes: MutableSequence[str] | None = None,
    ) -> None:
        """
        `feed`：数据源（用到 `universe` / 可选 `universe_span` / 可选 `root`）；
        `notes`：装配期披露收集器（缺省为空列表，披露不丢但仍可取回）。
        """
        self.feed = feed
        self.notes: MutableSequence[str] = notes if notes is not None else []

    # ── 入口 ──
    def resolve(
        self, universe_cfg: Mapping[str, Any], start: TradingDate,
        end: TradingDate | None = None,
    ) -> tuple[dict[str, Instrument], dict[str, list[str]] | None]:
        """解析宇宙；返回 `(instruments, 月度指数成分映射 | None)`。"""
        src = universe_cfg.get("source", "explicit")
        if src == "explicit":
            return self._explicit(universe_cfg, start), None
        if src == "all":
            return self._all(start, end), None
        if src == "index":
            return self._index(universe_cfg, start, end)
        raise ConfigError([
            f"run.universe.source: {src!r} 未支持（可选: explicit | all | "
            f"index（v0.5 V5-4，index_weight 月末快照机器复算））"])

    # ── explicit ──
    def _explicit(
        self, universe_cfg: Mapping[str, Any], start: TradingDate,
    ) -> dict[str, Instrument]:
        as_of = parse_trading_date(universe_cfg.get("as_of") or start.to_ymd())
        pool = {i.symbol: i for i in self.feed.universe(as_of)}
        out: dict[str, Instrument] = {}
        for sym in universe_cfg.get("symbols", []):
            if sym in pool:
                out[sym] = pool[sym]
            else:
                inst = infer_instrument(sym)
                logger.info(
                    "%s 不在 stock_basic 宇宙 → ETF 身份推断（子类=%s，"
                    "T+%d；M3 6.3 ETF BoardRule）",
                    sym, inst.etf_subclass.value, rule_for(inst).t_plus)
                out[sym] = inst
        return out

    # ── all（期间并集）──
    def _all(
        self, start: TradingDate, end: TradingDate | None,
    ) -> dict[str, Instrument]:
        """全市场宇宙 = **[start, end] 期间并集**（19 号 §20.3 / P0-NEW-4）。

        原实现只在**起点**求值一次，而 `universe(date)` 按日动态过滤 → 期间新
        上市标的全程不可见（十年 2,623 只 = 期末宇宙 48.3%；v77 区间 476 只
        = 8.6%）。传导路径：策略经 `screen_l2` **选得出**，但引擎宇宙与撮合
        **买不到**——零报错、零拒单、零披露。

        修复：优先 `feed.universe_span(start, end)`（一次 stock_basic 读的闭式
        并集）；无该方法的 Feed（如 MemoryFeed 静态宇宙）回退
        `universe(end) ∪ universe(start)`。跨期新增数量**强制披露**。
        """
        span = getattr(self.feed, "universe_span", None)
        fallback_note = ""
        if callable(span) and end is not None:
            pool = {i.symbol: i for i in span(start, end)}
        else:                                   # 回退：起止两快照并集
            pool = {i.symbol: i for i in self.feed.universe(end or start)}
            if end is not None:
                for inst in self.feed.universe(start):
                    pool.setdefault(inst.symbol, inst)
            if end is not None:
                # P2-NEW-3（19 号 §22.3）：回退口径**必须显式披露**——起止快照
                # 并集会漏"期间上市且期间退市"的标的（十年口径实测 13 只，0.23%）
                fallback_note = (
                    "（回退口径：该 Feed 未实现 universe_span，取起止快照并集"
                    "——可能遗漏期间内上市且退市的标的，量级约 0.2%）")
        base = {i.symbol for i in self.feed.universe(start)}
        added = len(pool) - len(base)
        note = (f"source=all 宇宙 = {start.to_ymd()}.."
                f"{(end or start).to_ymd()} 期间并集：{len(pool)} 标的"
                f"（起点快照 {len(base)} → 跨期新增 {added} 只）"
                if added else
                f"source=all 宇宙：{len(pool)} 标的（期间无新增）")
        note += fallback_note
        self.notes.append(note)
        logger.info("全市场宇宙（期间并集，P0-NEW-4）：%s", note)
        return pool

    # ── index（月末快照 PIT）──
    def _index(
        self, universe_cfg: Mapping[str, Any], start: TradingDate,
        end: TradingDate | None,
    ) -> tuple[dict[str, Instrument], dict[str, list[str]]]:
        """指数成分宇宙（v0.5 V5-4）：月度 PIT 映射 + 期间并集静态超集。

        引擎宇宙为静态身份 ⇒ instruments = 各月成分**并集**；逐月切换由策略
        消费（构造签名含 `index_universe` 参数者自动注入月度映射）。
        """
        from btf.data.index_universe import (
            IndexUniverseProvider,
            resolve_index,
        )

        name = universe_cfg.get("index")
        if not name:
            raise ConfigError(["run.universe.index 必填（source=index）："
                               "别名（hs300/zz500/sz50/…）或 index_code（000300.SH）"])
        index_code = resolve_index(name)
        provider = IndexUniverseProvider(getattr(self.feed, "root", None))
        end_ymd = (end or start).to_ymd()
        monthly = provider.monthly_codes(index_code, start.to_ymd(), end_ymd)
        if not any(monthly.values()):
            raise ConfigError([
                f"run.universe.index: {name!r}（{index_code}）在 "
                f"{start.to_ymd()}~{end_ymd} 无可用成分快照（index_weight）"])
        codes = provider.union_codes(index_code, start.to_ymd(), end_ymd)
        as_of = parse_trading_date(universe_cfg.get("as_of") or end_ymd)
        pool = {i.symbol: i for i in self.feed.universe(as_of)}
        out: dict[str, Instrument] = {}
        for code in codes:
            # ETF/未登记标的 → 身份推断兜底
            out[code] = pool.get(code) or infer_instrument(code)
        logger.info(
            "指数成分宇宙 %s（%s）：%d 个月度快照 → 并集 %d 标的（静态超集；"
            "逐月切换经 index_universe 注入策略）",
            name, index_code, len(monthly), len(out))
        return out, monthly
