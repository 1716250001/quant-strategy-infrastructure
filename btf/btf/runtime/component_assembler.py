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

    def guard_feed_routing(self) -> None:
        """装配期 feed↔标的路由校验（C-2 / 19 号 §163）。

        对宇宙逐标的取 `daily_table_of(sym)`，与当前 feed 的 `daily_tables`
        能力表比对；不匹配即 `ConfigError`，点名标的与所需表。**不采纳**
        自动路由（会改变同一配置历史 run 的数字，破坏可比性）。
        自定义 Feed 未声明 `daily_tables` ⇒ 跳过（不阻断第三方 Feed）。
        """
        from btf.data.tables_meta import daily_table_of

        caps = getattr(self.rt.feed, "daily_tables", None)
        if not caps:
            return
        feed_name = str((self.rt.config.get("data") or {}).get("feed") or "?")
        bad = [(sym, daily_table_of(sym)) for sym in self.rt.instruments
               if daily_table_of(sym) not in caps]
        if bad:
            raise ConfigError([
                f"feed↔标的路由不匹配：{sym} 需要 `{table}` 表，但 data.feed="
                f"{feed_name} 仅支持 {sorted(caps)}——请改用 data.feed="
                f"`mixed`（三表自动路由），或从宇宙移除该标的"
                for sym, table in bad])
        logger.info("feed 路由校验通过：%d 标的（%s 能力表 %s）",
                    len(self.rt.instruments), feed_name, sorted(caps))

    def guard_benchmark(self, period: Mapping[str, Any]) -> None:
        """装配期基准守卫（P1-NEW-6）：形制 + 区间覆盖（零行即 ConfigError）。

        **MM-6（19 号 §137 B）**：`report.benchmark` 可为 1–3 个代码的数组 ⇒
        **逐个**校验（任一个形制非法或区间零行即拒），错误信息点名是哪一个
        ——不允许「主基准过了、次基准跑完才崩」。
        """
        from btf.config.loader import benchmark_symbols

        symbols = benchmark_symbols(self.rt.config.get("report") or {})
        if not symbols:
            return
        from btf.data.index_series import probe_index_coverage

        for symbol in symbols:
            if not self.rt._BENCHMARK_RE.match(str(symbol)):
                raise ConfigError([
                    f"report.benchmark={symbol!r} 形制非法：应为"
                    f"「6 位数字.交易所」（如 000300.SH / 399006.SZ / 000905.SH）"])

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

    def guard_dataset_version(self, period: Mapping[str, Any]) -> None:
        """数据版本门禁（**RC-14**，2026-10-05）：声明版本 → 运行前校验哈希一致。

        声明键：`data.dataset.version` / `data.dataset.expected_hash`（期望内容
        哈希；同时声明须一致，否则自相矛盾即拒）与 `data.dataset.anchor_date`
        （期望数据水位**下限**）。机制：

          - **哈希门禁（严格相等）**：装配期实算 `content_hash`（档位 =
            `data.fingerprint_mode`，缺省 meta）并比对；不一致 ⇒ ConfigError
            （附两端哈希与档位——"拒绝静默换数"）；
          - **水位门禁（≥ 声明）**：实算水位 = 各表 `date_max` 最大值
            （**未裁剪**——与 manifest 披露的"裁剪到区间末"锚点不同义）；
          - 内存 Feed（无文件指纹）声明任何版本键 ⇒ ConfigError（无法校验，
            拒绝静默放行）；
          - 通过 ⇒ 记装配披露（声明 vs 实算 + 档位）——"这条结论锚在哪份数据"。

        **只在声明时计算**（opt-in；meta 档毫秒级 + 进程内 memo）——未声明
        路径零成本（BB-1 纪律：网格搜索不因门禁变慢）。
        """
        cfg = self.rt.config or {}
        # **T-18（19 号 §149 D-1）**：WF 折内允许**只跳哈希门禁**——折区间是整段
        # 冻结区间的子区间，实算指纹必然不同（机制冲突而非换数）；水位门禁照旧
        # （禁止一并放宽）。跳过由 run_walk_forward 显式置位，且**三处披露缺一即
        # 验收不通过**（本处 assembly_notes / manifest.version_gate / 结果 JSON+控制台）。
        skip_reason = getattr(self.rt, "version_gate_skip_reason", None)
        data_cfg = cfg.get("data") or {}
        ds_cfg = data_cfg.get("dataset") or {}
        declared_version = str(ds_cfg.get("version") or "").strip()
        declared_hash = str(ds_cfg.get("expected_hash") or "").strip()
        declared_anchor = str(ds_cfg.get("anchor_date") or "").strip()
        if not (declared_version or declared_hash or declared_anchor):
            return
        if (declared_version and declared_hash
                and declared_version != declared_hash):
            raise ConfigError([
                "data.dataset.version 与 data.dataset.expected_hash 声明不一致"
                f"（自相矛盾）：version={declared_version!r} / "
                f"expected_hash={declared_hash!r}（RC-14）"])
        declared = declared_version or declared_hash

        info = self.compute_data_version(period)
        mode = str(info.get("mode") or "?")
        if mode == "in-memory" or not info.get("tables_detail"):
            raise ConfigError([
                "声明了 data.dataset.version/expected_hash/anchor_date，但当前 "
                "Feed 为**内存 Feed**（无文件指纹）——数据版本门禁无法校验，"
                "拒绝静默放行（RC-14：门禁的意义即「不一致即拒」）"])
        computed_hash = str(info.get("content_hash") or "")
        if declared and computed_hash != declared:
            if skip_reason:
                self.rt.version_gate_skipped_hash = True
                self.rt.assembly_notes.append(
                    "数据版本门禁（RC-14）：**哈希门禁按折跳过**"
                    f"（version_gate={skip_reason}）：声明 {declared} ≠ 实算 "
                    f"{computed_hash}（档位 {mode}）——折区间为整段子区间，"
                    "指纹必然不同（机制约束，非换数）；anchor_date 水位门禁仍生效")
            else:
                raise ConfigError([
                    f"数据版本门禁未通过（RC-14）：声明 {declared} ≠ 实算 "
                    f"{computed_hash}（档位 {mode}）——数据快照与声明不一致；"
                    f"如确认换数，请更新 data.dataset.version/expected_hash"])
        watermarks = [d.get("date_max")
                      for d in info.get("tables_detail", {}).values()
                      if d.get("date_max")]
        actual_anchor = max(watermarks) if watermarks else ""
        if declared_anchor:
            if not actual_anchor:
                raise ConfigError([
                    "数据版本门禁未通过（RC-14）：声明了 "
                    f"data.dataset.anchor_date={declared_anchor}，但实算水位"
                    "为空（tables_detail 无 date_max）"])
            if actual_anchor < declared_anchor:
                raise ConfigError([
                    f"数据版本门禁未通过（RC-14）：数据水位 {actual_anchor} < "
                    f"声明下限 {declared_anchor}——数据未更新到声明水位；"
                    f"请先更新数据或下调声明"])
        self.rt.assembly_notes.append(
            "数据版本门禁通过（RC-14）：声明哈希 "
            f"{declared or '（未声明）'} / 声明水位 "
            f"{declared_anchor or '（未声明）'}；实算 {computed_hash}"
            f"（档位 {mode}）、水位 {actual_anchor or '—'}")
