# -*- coding: utf-8 -*-
"""BTFRuntime：单进程运行时门面（04 §8.3.8；M1 任务 4.1 最小纵切）。

链路：load_config（四层合并 + Schema 校验）→ build（registry 契约
协商 + 组件组装 + 策略 module:Class 动态加载）→ run（Engine 执行 +
RunStore 落盘：manifest 骨架→产物→指标补全）→ load_run（产物重载）。

M2 任务 5.5 接线：run() 默认落盘（store 可注入以便测试隔离）；异常时
manifest 落 FAILED 终态（崩溃 run 可审计，08 §13.2）。

留白显式化（防止静默跳过——装配期显式日志/报错；M1 留白已随 M2/M3 兑现）：
    - risk.rules 装配为规则链 + 引擎⑧步接线（M2 5.2 已交付；非空链 → info 日志）；
    - rules.source：static / mirror_json（M3 6.1 已交付；缺参数显式报错）；
    - seed 记录进 manifest，引擎确定性；随机策略消费路径 M2 起；
    - universe 源支持 explicit/all；hs300 等指数成分（index_weight 机器复算）归 v0.5。

分层：本模块属外层（cli | viz | registry | runtime），可组装全部内层。
"""
from __future__ import annotations

import importlib
import inspect
import logging
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from btf import registry
from btf.analytics.run_metrics import compute_run_metrics
from btf.config.paths import RUNS_DIR
from btf.config.validation import ConfigError
from btf.data.coverage import check_coverage
from btf.data.memory import MemoryFeed  # noqa: F401  # 类型注记
from btf.domain.contracts import CONTRACT_VERSION
from btf.domain.orders import Fee, Fill, OrderSide
from btf.domain.types import (
    Instrument,
    TradingDate,
)
from btf.engine.loop import Engine
from btf.experiment.manifest import RunManifest, metrics_digest_of
from btf.experiment.store import LocalRunStore, RunBundle
from btf.risk.manager import RuleChainManager
from btf.runtime.component_assembler import (  # 再导出对外异常
    ComponentAssembler,
    DataQualityError,
)
from btf.runtime.config_resolver import ConfigResolver
from btf.runtime.run_orchestrator import RunOrchestrator
from btf.runtime.universe_resolver import UniverseResolver
from btf.strategy.base import StrategyBase
from btf.strategy.rules import MirrorJsonRulesProvider, StaticRulesProvider

logger = logging.getLogger(__name__)


# R4：产物常量 `DATA_QUALITY` 的唯一归属在 `btf.runtime.run_orchestrator`
# （落盘编排的产物清单随编排一处，避免"常量在原处、用法在新处"的漂移）。
# R4：异常类随装配期门一并下沉到 `component_assembler`，此处**再导出**
# （`btf.runtime.DataQualityError` 对外不变，且只有一个类对象）

#: 极短区间阈值（交易日；BB-4，19 号 §33.6④/OBS-1）：低于此值告警（不阻断）。
#: 单源：CLI 打印与 runtime 日志共用本常量。
SHORT_PERIOD_TRADING_DAYS = 20

#: 数据指纹**进程内 memo**（BB-1）：键 = (root, tables, years, mode)。
#: 为什么需要：指纹是**披露字段**，同一进程内重复 build/run（网格搜索、批量
#: `--set` 扫描、测试）不应重复付算力；进程生命周期内数据视为不变（跨进程
#: 的 `bt run` 会重新实算，故数据重写仍可被检出——见 tests 的变异性检查）。
_FINGERPRINT_MEMO: dict[tuple, Any] = {}


def _ymd(s: str) -> TradingDate:
    return TradingDate.from_ymd(s.replace("-", ""))


def make_store(root: str | Path | None = None) -> LocalRunStore:
    """RunStore 工厂（CLI/外部经本门面取——铁律 5：cli 不直连 experiment）。"""
    return LocalRunStore(Path(root) if root else RUNS_DIR)


def fee_segments() -> list[dict[str, Any]]:
    """费率分段明细（假设与披露章节；CLI/报告经本门面取——cli 不直连 execution）。

    铁律 5（表现层只认契约）：``btf.cli``/``btf.viz`` 不得 import
    ``btf.execution``，分段表经 runtime 门面序列化后供给。
    """
    from btf.execution.cost import A_SHARE_SEGMENTS

    return [{
        "effective_from": (s.effective_from.to_ymd() if s.effective_from else None),
        "stamp_duty_sell": s.stamp_duty_sell,
        "transfer_fee_rate": s.transfer_fee_rate,
        "transfer_scope": s.transfer_scope,
        "transfer_basis": s.transfer_basis,
    } for s in A_SHARE_SEGMENTS]


def _fill_from_row(row: Mapping[str, Any]) -> Fill:
    """落盘 fills 行 → Fill（R1 复核重算用）。"""
    fee = row.get("fee") or {}
    return Fill(
        fill_id=row["fill_id"], order_id=row.get("order_id", ""),
        symbol=row["symbol"], side=OrderSide(row["side"]), qty=int(row["qty"]),
        price=float(row["price"]),
        fee=Fee(commission=float(fee.get("commission", 0.0)),
                stamp_duty=float(fee.get("stamp_duty", 0.0)),
                transfer_fee=float(fee.get("transfer_fee", 0.0)),
                total=float(fee.get("total", 0.0))),
        fill_date=TradingDate.from_ymd(row["fill_date"]),
        fill_timing=row.get("fill_timing", "open"),
    )


class BTFRuntime:
    """运行时门面（04 §8.3.8 签名；M1 内核纵切）。

    用法：
        rt = BTFRuntime().load_config("backtest.yaml").build()
        result = rt.run()
    """

    contract_version = CONTRACT_VERSION

    def __init__(self) -> None:
        self.config: dict[str, Any] | None = None
        self.feed: Any = None
        self.strategy: StrategyBase | None = None
        self.cost_model: Any = None
        self.slippage: Any = None
        self.handler: Any = None
        self.rebalancer: Any = None
        self.risk_manager: RuleChainManager | None = None
        self.rules_provider: Any = None
        self.instruments: dict[str, Instrument] = {}
        self.last_run_id: str | None = None      # 最近一次落盘 run（M2 5.5）
        # 披露与守卫状态（19 号审查：P1-11 覆盖度 / P1-6 成本 / P0-3 风控）
        self.coverage: Any = None
        self.cost_model_name: str = "zero"
        self.risk_allow_empty: bool = False
        #: 装配期披露（非致命；如 P0-NEW-4「跨期新增 N 只」）——与 coverage
        #: 的降级通道分开：本列表是**信息**而非降级。**随产物落盘**（P1-NEW-5：
        #: 经 manifest 进报告披露区，不再只落日志）
        self.assembly_notes: list[str] = []
        #: 基准代码（P0-NEW-7：元信息不混入数值指标集；报告披露区消费）
        self.benchmark_symbol: str = ""
        #: 数据指纹真值（BB-1 重路：装配期实算；落 manifest.data_version）
        self.data_version_info: dict[str, Any] = {}
        #: 数据不变量校验结果（批 8 CC-1..CC-6；装配期实算，落 run 目录 + 报告）
        self.data_quality: dict[str, Any] = {}
        #: 分步耗时（R5-4 / P2-4：`bt run --verbose` 与 optimize 观测；
        #: 键为阶段名，值为秒——**实测值**，不得恒空，铁律新 16）
        self.step_timings: dict[str, float] = {}
        #: 指纹计算耗时（秒；BB-1 验收「须实测启动开销增量」留痕）
        self.data_fingerprint_seconds: float = 0.0
        #: 共享表级缓存（Z-3，19 号 §26.3）：基准守卫（装配期）与相对指标
        #: 取数（run 期）**必须共用同一 store**——原实现两处各自 `new` 一个
        #: `YearTableStore` → 同一批年份 `index_daily` 读两遍，且与
        #: `index_series` 模块 docstring「不造私有缓存」的声明不符。
        #: max_entries=16：覆盖十年基准区间逐年谓词下推条目且不触发 FIFO 淘汰。
        self._data_store: Any = None

    # ── ① 配置 ──
    def load_config(
        self,
        source: str | Path | Mapping[str, Any] | None = None,
        *,
        environ: Mapping[str, str] | None = None,
        overrides: Mapping[str, Any] | None = None,
    ) -> BTFRuntime:
        """四层合并 + Schema 校验（失败即抛，不进入 build）。

        R4 拆分：逻辑下沉到 `ConfigResolver`（本方法只做状态赋值与链式返回）。
        """
        self.config = ConfigResolver().resolve(
            source, environ=environ, overrides=overrides)
        return self

    # ── ② 组装 ──
    def build(self) -> BTFRuntime:
        if self.config is None:
            raise RuntimeError("先 load_config 再 build")
        cfg = self.config

        data_cfg = cfg["data"]
        from time import perf_counter as _pc

        self.feed = registry.create(
            registry.DATA_FEED, data_cfg["feed"], data_cfg.get("feed_params"))
        # ── 状态面板**构造期注入**（R4；19 号 §47.4 余项 ①）─────────────
        # 反转 `feed → state` 例外边（契约新 8 的最后一条豁免）：feed 不再
        # import state，改由装配根注入 `StateSynthesizer` —— 状态面板是撮合
        # （涨跌停/停牌/ST/退市）与风控的唯一可交易性依据，故**必须**注入；
        # 自定义 Feed（MemoryFeed/场景源）自带状态实现，无本方法即跳过。
        attach = getattr(self.feed, "attach_state_provider", None)
        if callable(attach):
            from btf.data.state import StateSynthesizer

            attach(StateSynthesizer(getattr(self.feed, "root", None)))
        self.step_timings.clear()          # R5-4：重复 build 不累积（幂等）
        _t = _pc()

        run_cfg = cfg["run"]
        period = run_cfg["period"]
        # 区间覆盖度守卫（19 号 P1-11 / §C.3.2）：区间早于必需表起点（如
        # stk_limit 20080102）默认 **ConfigError**——原实现对数据起点零校验，
        # 2008 年前的涨跌停约束静默失效（成交系统性偏乐观且不披露）；
        # 显式 `data.allow_degraded_limit=true` 可放行（报告强制披露）
        self.coverage = check_coverage(
            _ymd(period["start"]).to_ymd(), _ymd(period["end"]).to_ymd())
        allow_degraded = bool((cfg.get("data") or {}).get(
            "allow_degraded_limit", False))
        if self.coverage.fatal:
            if not allow_degraded:
                raise ConfigError([
                    f"回测区间覆盖度校验失败：{note}（如确需降级运行，显式设置 "
                    f"data.allow_degraded_limit=true——报告将披露该降级）"
                    for note in self.coverage.fatal])
            logger.warning("区间覆盖度降级运行（allow_degraded_limit=true）：%s",
                           "；".join(self.coverage.fatal))
        elif self.coverage.degraded:
            logger.info("区间覆盖度提示：%s", "；".join(self.coverage.degraded))

        # 基准装配期守卫（P1-NEW-6，19 号 §23.1/§23.3）：基准号拼错/区间不覆盖
        # 原须**跑完整段回测**才在 `_benchmark_metrics` 报错（长区间 = 白烧）；
        # 与"区间已有装配期守卫"的既有标准不一致 → 现装配期即探测（fail-closed）。
        self._guard_benchmark(period)
        self.step_timings["装配·前置与守卫"] = _pc() - _t
        _t = _pc()

        # 数据指纹（BB-1「重路」，19 号 §39.4 / 裁决六-3）**在落盘路径计算**
        # （见 `run()`）——披露字段只在有产物时有意义：不落盘路径（网格搜索
        # 每组合 build+persist=False、内存回测）**零成本**。此处仅提示档位。
        # 规则单一真源（H2）必须**先于策略加载**装配——策略构造签名注入
        # （`rules` / `liq_series`）依赖它（19 号审查实证 bug：原顺序在策略
        # 之后 → 注入恒不生效；该路径从未端到端跑通故未暴露）
        if isinstance(cfg.get("rules"), dict):
            self.rules_provider = self._build_rules_provider(cfg["rules"])
        self.index_universe: dict[str, list[str]] | None = None   # V5-4
        self.instruments = self._resolve_instruments(
            run_cfg.get("universe") or {}, _ymd(period["start"]),
            _ymd(period["end"]))

        # 数据不变量校验（**批 8：CC-1..CC-6**，§39.2 裁决六-1）：装配期**一次**
        # （区间 + 宇宙裁剪；走 data.core 唯一入口 + 谓词下推）。三条设计要点
        # 的落地：① fail-closed 边界 = 本处 `strict` 决定硬失败/告警+记入；
        # ② 性能 = 区间化 + 标的裁剪（`max_symbols` 上限，抽样即披露）；
        # ③ 新 15/新 16 = 机检含负向对照 + `checked_rows` 不为 0。
        # ⚠ 必须在 `self.instruments` 解析**之后**（依赖宇宙标的；首版置于
        # `_guard_benchmark` 之后 → 宇宙为空 → 空标集短路、校验静默退化为 0 行，
        # 由集成测试 `test_default_records_and_discloses` 抓出）。
        self.data_quality = self._run_data_quality(period)
        self.step_timings["装配·数据质检"] = _pc() - _t
        _t = _pc()

        self.strategy = self._load_strategy(run_cfg)

        exec_cfg = cfg.get("execution", {})
        # 成本模型显式化（19 号审查 P1-6）：未显式配置 = 系统性零费用回测
        # ——记录名字 + 警告；报告侧由 `_derived_disclosures` 强制披露
        self.cost_model_name = exec_cfg.get("cost_model", "zero")
        if self.cost_model_name == "zero":
            logger.warning(
                "execution.cost_model 未配置（默认 zero）：本回测为**零费用"
                "基线**，实盘需按分段费率（tiered_v1）重估——报告将披露")
        self.cost_model = registry.create(
            registry.COST_MODEL, self.cost_model_name)
        slip_cfg = exec_cfg.get("slippage", {}) or {}
        self.slippage = registry.create(
            registry.SLIPPAGE_MODEL, slip_cfg.get("model", "none"),
            slip_cfg.get("params"))
        handler_cls = registry.resolve(
            registry.EXECUTION_HANDLER, exec_cfg.get("handler", "next_open"))
        self.handler = handler_cls(
            cost_model=self.cost_model, slippage_model=self.slippage,
            instruments=self.instruments,
            **(exec_cfg.get("handler_params") or {}))
        rebalancer_params = {"instruments": self.instruments}
        rebalancer_params.update(exec_cfg.get("rebalancer_params") or {})
        self.rebalancer = registry.create(
            registry.REBALANCER, exec_cfg.get("rebalancer", "full"),
            rebalancer_params)

        risk_cfg = cfg.get("risk", {}) or {}
        rules = [
            registry.create(registry.RISK_RULE, r["name"], r.get("params"))
            for r in risk_cfg.get("rules", []) or []
        ]
        # fail-closed（19 号审查 P0-3；**Q1 裁决 A —— X-7，2026-09-28**）：
        # 空风控链**默认拒绝运行**（默认 False）——原实现仅 warning 后全量
        # 订单裸奔；v0.5.1 曾把默认层写成 True（实为 fail-open，与 CHANGELOG
        # 措辞矛盾，见 19 号 §17.4.2）。逃生开关须显式声明调试意图，并经
        # manifest 配置回显 + 报告假设章节披露（`viz.report._derived_disclosures`）
        self.risk_allow_empty = bool(risk_cfg.get("allow_empty_chain", False))
        if not rules and not self.risk_allow_empty:
            raise ConfigError([
                "risk.rules 为空且 risk.allow_empty_chain=false：拒绝以无风控"
                "状态运行（fail-closed）。如确需裸奔回测（仅调试），"
                "显式设置 risk.allow_empty_chain=true——该标记将在报告中披露。"])
        self.risk_manager = RuleChainManager(rules)
        if rules:
            logger.info(
                "风控链已装配（%d 条，M2 5.2 引擎时序已接线）", len(rules))
        elif self.risk_allow_empty:
            logger.warning(
                "风控链为空且 risk.allow_empty_chain=true：全部订单放行"
                "（调试模式，报告将披露）")

        if "seed" in cfg:
            logger.info("seed=%s 已记录（引擎确定性路径——随机策略 M2 起消费）",
                        cfg["seed"])
        self.step_timings["装配·组件与策略"] = _pc() - _t
        return self

    def _resolve_instruments(
        self, universe_cfg: Mapping[str, Any], start: TradingDate,
        end: TradingDate | None = None,
    ) -> dict[str, Instrument]:
        """宇宙解析（explicit / all / index——v0.5 V5-4 指数成分）。

        R4 拆分：三种口径与两条回退/披露路径已下沉到 `UniverseResolver`；
        本方法只做**委托 + 回写** `index_universe`（月度 PIT 映射）。
        """
        instruments, monthly = UniverseResolver(
            self.feed, notes=self.assembly_notes).resolve(
                universe_cfg, start, end)
        if monthly is not None:
            self.index_universe = monthly
        return instruments



    def _load_strategy(self, run_cfg: Mapping[str, Any]) -> StrategyBase:
        """module:Class 动态加载 + 构造参数注入（装配期，非 doc 的 init 期）。"""
        spec = run_cfg["strategy"]
        module_name, _, class_name = spec.partition(":")
        try:
            cls = getattr(importlib.import_module(module_name), class_name)
        except (ImportError, AttributeError) as exc:
            raise ConfigError(
                [f"run.strategy: 加载失败 {spec!r}（{exc}）"]) from exc
        # 版本协商（19 号审查 P1-2/EX-2）：策略是最高频扩展点，原实现
        # importlib 直载**不校验 contract_version**（版本不兼容静默通过）；
        # 与 registry 插件同口径——未声明/主版本不符 → 拒载
        from btf.domain.contracts import negotiate

        negotiate(f"strategy:{spec}", getattr(cls, "contract_version", None))
        params = dict(run_cfg.get("params") or {})
        # 规则单一真源（H2）：策略声明 rules 参数 → 注入 RulesProvider
        # （YAML 不得出现规则参数字面量）
        signature = inspect.signature(cls.__init__).parameters
        if self.rules_provider is not None and "rules" in signature:
            params["rules"] = self.rules_provider
        # 指数成分宇宙（v0.5 V5-4）：策略声明 index_universe → 注入月度映射
        if self.index_universe is not None and "index_universe" in signature:
            params["index_universe"] = self.index_universe
        # LIQ 序列预计算（19 号报告 P0-1/§9.2）：策略声明 liq_series → 注入
        # 区间序列（阈值取 RulesProvider「L0_LIQ」，与策略消费同源）
        if ("liq_series" in signature and self.rules_provider is not None
                and "run" in self.config):
            thresholds = self.rules_provider.get("L0_LIQ")
            period = (self.config.get("run") or {}).get("period") or {}
            if thresholds and period.get("start") and period.get("end"):
                from btf.data.liq import precompute_liq_series

                params["liq_series"] = precompute_liq_series(
                    _ymd(period["start"]).to_ymd(), _ymd(period["end"]).to_ymd(),
                    thresholds)
                logger.info("LIQ 序列预计算注入：%d 交易日（%s~%s）",
                            len(params["liq_series"]), period["start"],
                            period["end"])
        return cls(**params)

    def _build_rules_provider(self, rules_cfg: Mapping[str, Any]) -> Any:
        """规则参数提供者（H2 单一真源）——**经 registry 名字表**（19 号
        P1-1/EX-1：原 if 硬分派 → 自定义 provider 注册后零源码改动生效）。"""
        src = rules_cfg.get("source")
        if src == "static":
            # 保留既有无参构造语义（version/params 由名字表工厂默认值决定）
            return StaticRulesProvider(
                params={}, version=rules_cfg.get("rules_version", "static"))
        if src == "mirror_json":
            path = rules_cfg.get("path")
            if not path:
                raise ConfigError(["rules.path 必填（source=mirror_json）："
                                   "镜像 JSON 由 tools/export_rules_mirror.py 导出"])
            return MirrorJsonRulesProvider(path, overrides=self._rules_overrides())
        # 扩展点兜底（19 号 P1-1/EX-1）：自定义 provider 经
        # `registry.register(RULES_PROVIDER, name, factory)` 注册后，
        # 配置 `rules.source: <name>` **零源码改动即生效**
        try:
            return registry.create(registry.RULES_PROVIDER, str(src), rules_cfg)
        except Exception as exc:
            raise ConfigError([
                f"rules.source: {src!r} 未支持（内置: static | mirror_json；"
                f"扩展：经 registry.register 注册后即可用）：{exc}"]) from exc

    # ── ③ 执行 ──
    def _guard_benchmark(self, period: Mapping[str, Any]) -> None:
        """装配期基准守卫（R4：逻辑下沉到 `component_assembler`）。"""
        return ComponentAssembler(self).guard_benchmark(period)
    def _data_tables(self) -> list[str]:
        """本次实际用到的表集（R4：逻辑下沉到 `component_assembler`）。"""
        return ComponentAssembler(self).data_tables()
    def _run_data_quality(self, period: Mapping[str, Any]) -> dict[str, Any]:
        """数据不变量校验（R4：逻辑下沉到 `component_assembler`）。"""
        return ComponentAssembler(self).run_data_quality(period)
    def _compute_data_version(self, period: Mapping[str, Any]) -> dict[str, Any]:
        """数据内容指纹（R4：逻辑下沉到 `component_assembler`）。"""
        return ComponentAssembler(self).compute_data_version(period)

    def run(self, *, store: LocalRunStore | None = None,
            persist: bool = True) -> Any:
        """Engine 执行 + RunStore 落盘（04 §8.3.8；08 §13.3）。

        R4 第二刀：落盘编排已下沉到 `RunOrchestrator`（本方法只做委托）；
        时序、异常终态与产物纪律见该类文档。
        """
        return RunOrchestrator(self).run(store=store, persist=persist)


    @staticmethod
    def _warn_if_short_period(result: Any) -> None:
        """极短区间告警（BB-4 / OBS-1，19 号 §33.6④）：**不阻断**。

        极短区间的部分指标"数学有定义、业务无意义"（实测：3 日区间
        `information_ratio = −31.83`），会污染 `optimize --objective` 排序；
        API 侧记 warning，CLI 侧另行打印到 stdout（用户可见）。
        """
        n_days = getattr(result, "n_days", 0) or 0
        if 0 < n_days < SHORT_PERIOD_TRADING_DAYS:
            logger.warning(
                "极短区间：%d 个交易日（< %d）——部分指标数学有定义但业务无意义，"
                "勿用于 optimize --objective 排序（19 号 OBS-1/BB-4）",
                n_days, SHORT_PERIOD_TRADING_DAYS)

    def _analyzer_names(self) -> list[str] | None:
        """`analysis.metrics` 名字表（19 号 EX-1：注册自定义 Analyzer 后
        经配置指定即生效——零源码改动；缺省 None = 内置全集）。"""
        names = ((self.config or {}).get("analysis") or {}).get("metrics")
        return [str(n) for n in names] if names else None

    #: 基准代码形制（schema 同步：`^\d{6}\.(SH|SZ|BJ)$`）
    _BENCHMARK_RE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")



    def step_timings_line(self) -> str:
        """分步耗时单行摘要（R5-4：`bt run --verbose` 消费；**不得恒空**）。"""
        if not self.step_timings:
            return "（无分步计时）"
        parts = [f"{k}={v:.2f}s" for k, v in self.step_timings.items()]
        parts.append(f"合计={sum(self.step_timings.values()):.2f}s")
        return " | ".join(parts)



    def _table_store(self) -> Any:
        """共享表级缓存（Z-3）：懒建一次，基准守卫与取数共用（免双重 IO）。

        root 取 feed 的 root（MemoryFeed 等无 root → `YearTableStore` 默认
        主库路径，与 `index_series` 原行为一致）。
        """
        if self._data_store is None:
            from btf.data.core import YearTableStore

            self._data_store = YearTableStore(
                root=getattr(self.feed, "root", None), max_entries=16)
        return self._data_store

    @staticmethod
    def _snapshot_ymd(snap: Any) -> str:
        """快照日期 → YMD（容忍 `TradingDate` 与已序列化字符串两种形态）。"""
        date = getattr(snap, "date", None)
        if date is None and isinstance(snap, Mapping):
            date = snap.get("date")
        return date.to_ymd() if hasattr(date, "to_ymd") else str(date)

    @staticmethod
    def _snapshot_value(snap: Any) -> float:
        """快照 NAV（同上双形态容忍）。"""
        value = getattr(snap, "total_value", None)
        if value is None and isinstance(snap, Mapping):
            value = snap.get("total_value")
        return float(value)

    def _benchmark_metrics(self, snapshots: Sequence[Any]) -> dict[str, Any]:
        """基准相对指标（19 号 R2.5 / P1-10）：配置 `report.benchmark` 才计算。

        死键转活键——原 `report.benchmark` 校验通过但零消费、不报错、无痕迹。
        基准缺日 → `BenchmarkError`（fail-closed，禁止前向填充）。

        R4 剩余：参数改为 **`snapshots`**（以便作为 `compute_run_metrics(extras=…)`
        的可调用对象——三处链共用同一形态）；**兼容旧形态**：传入 `RunResult`
        （有 `.snapshots`）时自动取值——与 `compute_all` / `_snapshot_ymd` 的
        "双形态容忍"口径一致（复核路径是 `SnapshotRecord`，运行路径是
        `PortfolioSnapshot`，本处则可能是整个 result）。
        """
        if hasattr(snapshots, "snapshots"):
            snapshots = snapshots.snapshots
        symbol = ((self.config.get("report") or {}).get("benchmark"))
        return self._relative_metrics_for(symbol, snapshots)

    def _relative_metrics_for(
        self, symbol: Any, snapshots: Sequence[Any], *,
        analysis: Mapping[str, Any] | None = None,
    ) -> dict[str, float]:
        """数值型相对指标（**元信息已剥离**；run 与 R1 复核共用同一实现）。

        P0-NEW-7（19 号 §24.3）后：返回 dict **只含数值**——`benchmark_symbol`
        属元信息，混入数值指标集会让 `save_metrics` 的 `float()` 在回测**跑完
        之后**崩（长区间白烧）；现元信息留 `self.benchmark_symbol`（披露用）。

        本助手同时供 `verify_run` 使用（R1 复核须与落盘口径一致：原复核只算
        基础 15 项、落盘含基准项 → 配了基准的 run 复核摘要必然不符）。
        """
        if not symbol or not snapshots:
            return {}
        from btf.analytics.benchmark import relative_metrics
        from btf.data.index_series import load_index_closes

        # 快照两种形态都要吃（复核路径 `bundle.snapshots` 是 `SnapshotRecord`，
        # 其 `date` 已是 YMD 字符串；运行路径是 PortfolioSnapshot，date 为
        # TradingDate）——与 `compute_all` 的双形态容忍口径一致
        days = [self._snapshot_ymd(s) for s in snapshots]
        _days, bench_closes = load_index_closes(
            str(symbol), days[0], days[-1], trading_days=days,
            store=self._table_store())
        navs = [self._snapshot_value(s) for s in snapshots]
        # 配置来源双通道：run 取当前 config；**verify** 取 manifest 生效配置
        # （复核路径 `self.config` 为 None——原写法会 AttributeError）
        if analysis is None:
            analysis = dict((self.config or {}).get("analysis") or {})
        metrics = relative_metrics(
            navs, bench_closes, symbol=str(symbol),
            risk_free_rate=float(analysis.get("risk_free_rate", 0.0)),
            annualization_factor=int(analysis.get("annualization_factor", 252)))
        self.benchmark_symbol = str(metrics.pop("benchmark_symbol", symbol) or "")
        logger.info("基准对比：%s（%d 日对齐）→ %d 项相对指标已并入 metrics",
                    symbol, len(days), len(metrics))
        return metrics

    def _run_engine(self, event_log_path) -> Any:
        """Engine 构造与执行（落盘与否共用）。"""
        run_cfg = self.config["run"]
        period = run_cfg["period"]
        # 截面限制 = 交易宇宙（V3-1 混合表必需；v0.5 V5-3 起对所有 Feed
        # 生效——标的谓词下推令子集宇宙场景（B2/B4）免扫全市场年表）
        # ⚠️ 注释订正（19 号 §20.3.4 / §20.5）：原写「全市场宇宙（source=all）
        # **语义不变**」与事实**相反**——source=all 原在起点冻结宇宙，十年
        # 损失 48.3% 标的（P0-NEW-4，已修：`_resolve_all_universe` 期间并集）。
        # 现状口径：截面恒 = 装配期宇宙（静态），跨期新增由装配期披露。
        cross = sorted(self.instruments) if self.instruments else None
        engine = Engine(
            self.feed, self.strategy,
            start=_ymd(period["start"]), end=_ymd(period["end"]),
            initial_cash=run_cfg.get("initial_cash", 1_000_000.0),
            handler=self.handler, rebalancer=self.rebalancer,
            risk_manager=self.risk_manager,
            instruments=self.instruments,
            cross_section=cross,
            event_log_path=event_log_path,
        )
        return engine.run()

    def _rules_version(self) -> str:
        """规则源版本指纹（H2：RulesProvider 提供；无 → "none"）。"""
        provider = getattr(self, "rules_provider", None)
        return provider.rules_version() if provider is not None else "none"

    def _rules_overrides(self) -> list[dict[str, Any]]:
        """显式 override 清单（覆盖即留痕，04 §8.3.7）。"""
        rules_cfg = self.config.get("rules") or {}
        overrides = rules_cfg.get("overrides") or []
        return [dict(o) for o in overrides]

    def _analysis_config(self) -> dict[str, Any]:
        """指标配置（annualization_factor / risk_free_rate；04 §8.5）。"""
        return dict(self.config.get("analysis") or {})

    # ── ④ 加载历史 run ──
    def load_run(self, run_id: str, *, store: LocalRunStore | None = None
                 ) -> RunBundle:
        """产物重载（04 §8.3.8；报告再生成与 R1 对账入口）。"""
        return (store or LocalRunStore()).load(run_id)

    def load_manifest(self, run_id: str,
                      store: LocalRunStore | None = None) -> RunManifest:
        """只读取 manifest（R1 复核：四版本 + metrics_digest 比对）。"""
        return (store or LocalRunStore()).read_manifest(run_id)

    # ── ⑤ R1 复核（08 §13.1）──
    def verify_run(self, run_id: str, *, store: LocalRunStore | None = None
                   ) -> dict[str, Any]:
        """R1 复核：从**落盘产物**重算指标 → 与 manifest.metrics_digest 比对。

        复核路径与 run() 的落盘路径分离（报告/归档后仍可自校验）：产物
        snapshots/fills → compute_all → metrics_digest_of → 比对。
        """
        bundle = self.load_run(run_id, store=store)
        fills = [_fill_from_row(row) for row in bundle.fills]
        # 基准相对指标（P0-NEW-7 衍生修复）：落盘 metrics 含基准项，复核原只算
        # 基础 15 项 → 配了基准的 run 摘要**必然不符**（假告警）；基准号取自
        # manifest 生效配置（与落盘同源，不依赖当前 runtime.config）。
        symbol = ((bundle.manifest.config_effective.get("report") or {})
                  .get("benchmark"))
        metrics = compute_run_metrics(
            bundle.snapshots, fills, self._metrics_config(bundle),
            analyzer_names=self._analyzer_names(),
            resolver=lambda name: registry.resolve(registry.ANALYZER, name),
            extras=lambda snaps: self._relative_metrics_for(
                symbol, snaps, analysis=self._metrics_config(bundle)))
        digest = metrics_digest_of(metrics)
        expected = bundle.manifest.metrics_digest
        # 产物完整性：metrics.json 回读的摘要亦须与 manifest 一致（篡改可检出）
        stored = metrics_digest_of(bundle.metrics) if bundle.metrics else None
        return {
            "run_id": run_id,
            "status": bundle.manifest.status,
            "digest": digest,
            "expected": expected,
            "stored_digest": stored,
            "match": digest == expected and stored == expected,
            "recompute_match": digest == expected,
            "stored_match": stored == expected,
            "n_snapshots": len(bundle.snapshots),
            "n_fills": len(fills),
            "metrics": metrics,
        }

    def _metrics_config(self, bundle: RunBundle) -> dict[str, Any]:
        """复核用指标配置（取自 manifest 生效配置的 analysis 段）。"""
        return dict(bundle.manifest.config_effective.get("analysis") or {})


__all__ = ["BTFRuntime", "DataQualityError", "fee_segments", "make_store"]
