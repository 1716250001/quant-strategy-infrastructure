# -*- coding: utf-8 -*-
"""**ComponentAssembler** —— 装配期的门与披露（R4 拆分；原 `BTFRuntime` 私有方法）。

收口的是"**装配期到底要过哪些门、产出哪些披露**"这一族职责：

| 函数 | 职责 |
|---|---|
| `guard_benchmark(rt, period)` | 基准装配期守卫（形制 + `index_daily` 零行即 `ConfigError`，避免跑完整段才报错——P1-NEW-6） |
| `data_tables(rt)` | 本次实际用到的表集（指纹只覆盖它；按宇宙标的路由——BB-1） |
| `run_data_quality(rt, period)` | 数据不变量校验（CC-1..CC-5）+ 装配期门 + 产物载荷（批 8） |
| `compute_data_version(rt, period)` | 数据内容指纹（三档 meta/fast/full；**仅在落盘路径**实算——BB-1） |

形态说明：本轮把它们落成**接受 `rt` 的函数**（而非持有 runtime 的类）——
方法体与拆分前**逐字一致**（由脚本搬移），故语义等价性由既有 824 测试直接验证；
后续子批可再收敛为持有显式依赖的类（配合 `RunOrchestrator` 一并做）。
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from btf.config.validation import ConfigError
from btf.domain.types import TradingDate, parse_trading_date

logger = logging.getLogger(__name__)


class DataQualityError(RuntimeError):
    """数据不变量校验失败（`data.quality.strict=true`，fail-closed）。

    BB-1/P2-NEW-8 系列后随装配期门一并下沉到本模块；`btf.runtime` **再导出**
    该名（对外 API 不变，调用方无需改动）。
    """

__all__ = ["ComponentAssembler", "DataQualityError"]


def _ymd(s: str) -> TradingDate:
    """`_runtime` 时期的私有别名 —— 现统一走 `domain.types` 单一真源（R4）。"""
    return parse_trading_date(s)

#: 数据指纹**进程内 memo**（BB-1；随 `compute_data_version` 一并下沉到此）：
#: 键 = (root, tables, years, mode)。同一进程内重复 build/run（网格/批量 `--set`）
#: 不应重复付算力；跨进程的 `bt run` 仍会重新实算 ⇒ 数据重写可检出。
_FINGERPRINT_MEMO: dict[tuple, Any] = {}

class ComponentAssembler:
    """装配期的**门与披露**（R4 第二刀：由模块函数升级为持有显式依赖的协作类）。

    依赖只有装配根 `rt`（显式传入，不再散落在门面里），对外四件事：

    | 方法 | 职责 |
    |---|---|
    | `guard_benchmark(period)` | 基准装配期守卫（形制 + `index_daily` 零行即 `ConfigError`——P1-NEW-6；避免跑完整段才报错） |
    | `data_tables()` | 本次实际用到的表集（指纹只覆盖它；按宇宙标的路由——BB-1） |
    | `run_data_quality(period)` | 数据不变量校验 CC-1..CC-5 + 装配期门 + 产物载荷（批 8） |
    | `compute_data_version(period)` | 数据内容指纹（三档 meta/fast/full；**仅在落盘路径**实算——BB-1） |

    纪律：四者都**可能改变运行是否继续**（抛 `ConfigError` / `DataQualityError`），
    故集中在一处便于审计"装配期到底过了哪些门"。
    """

    def __init__(self, rt: Any) -> None:
        self.rt = rt

    def data_tables(self) -> list[str]:
        """区间涉及的表（BB-1：指纹只覆盖"实际用到的数据"）。

        价格表按宇宙实际标的的 `daily_table_of` 取（股票/ETF/指数混合表场景）；
        另含身份与状态侧（stock_basic / suspend_d / adj_factor / 限价表）与
        基准（index_daily，配了 `report.benchmark` 才计入）。
        """
        from btf.data.tables_meta import daily_table_of

        tables = {daily_table_of(sym) for sym in self.rt.instruments} or {"daily"}
        tables |= {"stock_basic", "suspend_d", "adj_factor"}
        tables |= {"stk_limit", "etf_limit"}
        if ((self.rt.config.get("report") or {}).get("benchmark")):
            tables.add("index_daily")
        return sorted(tables)

    def guard_benchmark(self, period: Mapping[str, Any]) -> None:
        """装配期基准守卫（P1-NEW-6）：形制 + 区间覆盖（零行即 ConfigError）。"""
        symbol = ((self.rt.config.get("report") or {}).get("benchmark"))
        if not symbol:
            return
        if not self.rt._BENCHMARK_RE.match(str(symbol)):
            raise ConfigError([
                f"report.benchmark={symbol!r} 形制非法：应为"
                f"「6 位数字.交易所」（如 000300.SH / 399006.SZ / 000905.SH）"])
        from btf.data.index_series import probe_index_coverage

        n = probe_index_coverage(
            str(symbol), _ymd(period["start"]).to_ymd(),
            _ymd(period["end"]).to_ymd(), root=getattr(self.rt.feed, "root", None),
            store=self.rt._table_store())
        if n == 0:
            raise ConfigError([
                f"report.benchmark={symbol} 在区间 {period['start']}~"
                f"{period['end']} 内无任何行情（index_daily 零行）——"
                f"请核对基准代码或区间；装配期即拒绝，避免跑完整段回测才报错"])
        logger.info("基准装配期校验通过：%s（区间内 %d 个交易日有行情）",
                    symbol, n)

    def run_data_quality(self, period: Mapping[str, Any]) -> dict[str, Any]:
        """装配期数据不变量校验（CC-1..CC-5；CC-6 产物/披露见 `run()` 与报告）。

        - `data.quality.enabled`（缺省 true）：关闭即记录 `enabled=false` 并披露
          （**不**静默跳过——铁律新 16）；
        - `data.quality.max_symbols`（缺省 200）：宇宙超限时按**字典序前缀**
          决定性抽样（可复核），并在报告/日志中标注 `sampled_symbols=true`；
        - `data.quality.strict`（缺省 false）：true → 发现异常即 `DataQualityError`
          （fail-closed）；false → 告警 + 记入产物（"个别脏点不阻断整轮"）。
        """
        from time import perf_counter

        qcfg = dict((self.rt.config.get("data") or {}).get("quality") or {})
        if not qcfg.get("enabled", True):
            logger.info("数据不变量校验：配置关闭（data.quality.enabled=false）")
            return {"enabled": False,
                    "note": "已由配置关闭（data.quality.enabled=false）——"
                            "本 run 未做 CC-1..CC-5 校验（披露而非静默）"}
        root = getattr(self.rt.feed, "root", None)
        if root is None:
            logger.info("数据不变量校验：内存 Feed（无文件可校验，标注 skipped）")
            return {"enabled": False, "mode": "in-memory",
                    "note": "内存 Feed：无文件级数据可校验（非静默跳过）"}
        symbols = sorted(self.rt.instruments)      # dict[symbol, Instrument] 的键
        cap = int(qcfg.get("max_symbols", 200) or 0)
        sampled = bool(cap) and len(symbols) > cap
        if sampled:
            symbols = symbols[:cap]
        classes = {i.asset_class.value for i in self.rt.instruments.values()}
        price_tables = ("daily",) + (("fund_daily",) if "etf" in classes else ())
        limit_tables = ("stk_limit",) + (("etf_limit",) if "etf" in classes else ())
        t0 = perf_counter()
        from btf.data.quality import calendar_days, run_checks

        start_ymd = _ymd(period["start"]).to_ymd()
        end_ymd = _ymd(period["end"]).to_ymd()
        # ── ST 名单（批 9 / DD-1；P2-NEW-8）───────────────────────────────
        # 此前 `run_checks` **未传** `st_symbols` ⇒ CC-2 恒按板块上限（主板
        # 10%）⇒ ST 股 5%~10% 的越界**漏检**（功能未接线 + 零覆盖）。
        # 现经 `StateSynthesizer.st_pairs` 取区间内真实 ST 集合：
        #   · 日历同源：`cal` 用 `quality.calendar_days`（与 run_checks 内部同口径）；
        #   · 抽样一致：只在抽样后的 `symbols` 子集内保留（检查集一致 + 省内存）；
        #   · 只算一次（装配期），成本为 O(交易日 × log)。
        st_pairs: set[tuple[str, str]] = set()
        st_source = "unavailable"
        synth = getattr(self.rt.feed, "states", None)
        if synth is not None and hasattr(synth, "st_pairs"):
            try:
                cal = calendar_days(root, start_ymd, end_ymd)
                st_pairs = synth.st_pairs(start_ymd, end_ymd, symbols, cal=cal)
                st_source = synth.st_source()
            except Exception as exc:            # 取不到 = 不可用（不静默）
                logger.warning("数据不变量校验：ST 名单获取失败（%s: %s）——"
                               "CC-2 将只按板块上限判定", type(exc).__name__, exc)
        if st_source == "unavailable":
            logger.warning(
                "数据不变量校验：ST 名单不可用（namechange 缺失/取不到）→ CC-2 "
                "按板块上限判定，**会漏检 ST 股 5%~10% 的越界**（披露而非静默；"
                "铁律新 16）")
            self.rt.assembly_notes.append(
                "CC-2 ST 上限：ST 名单不可用 → 只按板块上限判定，"
                "**可能漏检 ST 越界**（非静默）")

        report = run_checks(root, start_ymd, end_ymd, symbols=set(symbols),
                            price_tables=price_tables, limit_tables=limit_tables,
                            st_symbols=st_pairs)
        payload = report.as_dict()
        payload["sampled_symbols"] = sampled
        payload["checked_symbols"] = len(symbols)
        payload["mode"] = "assembly"
        # 新 16：ST 分支**必须可核**（不得恒空/占位）
        payload["st_source"] = st_source
        payload["st_pairs_count"] = len(st_pairs)
        logger.info("%s", report.summary_line())
        if not report.ok:
            logger.warning(
                "数据不变量校验发现 %d 行异常（%s）——详见 run 目录 "
                "data_quality_report.json / 报告「数据质量」章节",
                report.n_findings,
                "、".join(f"{r.rule}:{r.n_findings}"
                          for r in report.rules if not r.ok))
        # 报告披露（CC-6）：走既有**强制章节**「假设与披露」通道（assembly_notes
        # → manifest → 报告），不新增模板章节（避免动渲染器）
        self.rt.assembly_notes.append(
            "数据不变量校验（CC-1..CC-5，装配期）："
            + ("全部通过" if report.ok else
               f"发现 {report.n_findings} 行异常（"
               + "、".join(f"{r.rule}:{r.n_findings}"
                           for r in report.rules if not r.ok)
               + "）——明细见 run 目录 data_quality_report.json")
            + (f"；标的抽样 {len(symbols)}/{len(self.rt.instruments)}"
               "（data.quality.max_symbols）" if sampled else ""))
        payload["seconds_assembly"] = round(perf_counter() - t0, 4)
        if qcfg.get("strict", False) and not report.ok:
            raise DataQualityError(
                f"数据不变量校验未通过（strict=true，fail-closed）："
                f"{report.summary_line()}")
        return payload

    def compute_data_version(self, period: Mapping[str, Any]) -> dict[str, Any]:
        """实算数据指纹（BB-1 重路）：content_hash / tables / anchor_date 全落真值。"""
        from time import perf_counter

        from btf.data.version import compute

        start_ymd = _ymd(period["start"]).to_ymd()
        end_ymd = _ymd(period["end"]).to_ymd()
        root = getattr(self.rt.feed, "root", None)
        if root is None:
            # 内存 Feed / 测试替身：无文件可指纹——**显式**标注来源（非
            # `unknown` 占位；铁律新 16 要的是"不恒空且不误导"）
            logger.info("数据指纹：内存 Feed（无文件指纹，标注 in-memory）")
            return {"content_hash": (
                        f"sha256:in-memory:{len(self.rt.instruments)}sym"),
                    "tables": [], "anchor_date": end_ymd,
                    "mode": "in-memory",
                    "note": "内存 Feed：无文件级指纹（非占位 unknown）"}
        tables = self.rt._data_tables()
        years = list(range(int(start_ymd[:4]), int(end_ymd[:4]) + 1))
        # 档位（§39.4「保留 full 开关」+ BB-1 实测修正缺省）：
        #   meta = Parquet 元数据（行数/行组统计/大小+mtime）——**毫秒级**，缺省；
        #   fast = 键列内容抽样——大表单次 ≈7.2s（10 年 6 表实测），显式开；
        #   full = 全列抽样——可检出**数值级修订**，重。
        # 缺省之所以从 fast 降到 meta：装配期指纹曾按"每组合一次"落到网格搜索
        # 路径上（12 组合 ×8 进程 8.0s → **43.8s**，B4 外推 3647s 超预算），
        # 且单次 `bt run` 亦 +7.2s 启动开销；meta 档把成本压到 ~0。
        mode = str((self.rt.config.get("data") or {}).get(
            "fingerprint_mode") or "meta")
        if mode not in ("meta", "fast", "full"):
            raise ConfigError([
                f"data.fingerprint_mode={mode!r} 非法"
                f"（可选 meta（缺省）| fast | full）"])
        t0 = perf_counter()
        key = (str(root), tuple(tables), tuple(years), mode)
        fp = _FINGERPRINT_MEMO.get(key)
        if fp is None:                       # 进程内 memo：同输入只算一次
            fp = compute(tables, Path(root), mode=mode, years=years)
            _FINGERPRINT_MEMO[key] = fp
        self.rt.data_fingerprint_seconds = perf_counter() - t0
        detail = fp.as_dict()
        watermarks = [d for t in detail.values()
                      if (d := t.get("date_max")) is not None]
        # 数据水位 = 表内最大日期，**但不晚于区间末**（部分表非年分片——如
        # 静态/单文件表——其日期会延伸到未来，与本次回测无关；不裁剪会把
        # 锚点写成未来日期，误导"数据水位"语义）
        anchor = min(max(watermarks), end_ymd) if watermarks else end_ymd
        logger.info(
            "数据指纹（%s 档，%d 表 × %d 年，%.2fs）：%s | 数据水位 %s",
            mode, len(tables), len(years), self.rt.data_fingerprint_seconds,
            fp.digest, anchor)
        return {
            "content_hash": fp.digest,
            "tables": tables,
            "anchor_date": anchor,
            "tables_detail": detail,
            "mode": mode,
        }
