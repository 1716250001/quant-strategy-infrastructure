# 12 架构决策记录（ADR）

> **本文档汇总 btf 当前生效的架构决策**——对应实现版本 **btf 1.1.0（2026-09-29 定版；2026-09-30 CLI 收编）**。
> 每条 ADR 给出：**决策 / 现状（代码位置或产物）/ 依据与边界**。状态只有两种：**生效**、**未采纳（保留评估）**。
> 关联：分层与依赖铁律见 [03](03-总体架构方案对比与推荐.md)；技术栈见 [07](07-技术选型建议与权衡.md)。

---

## ADR-1 采用「分层可插拔 + 事件驱动内核」

- **决策**：领域核心零第三方依赖；**结论只出自事件驱动层**；数据/成本/滑点/风控/指标/存储全部插件化。
- **现状**：`btf.domain`（白名单标准库）→ 引擎四模块 → 分析/实验/数据 → `btf.app` → `btf.cli`/`btf.api`；**10 条 import-linter 契约**机器守卫。
- **依据**：A 股约束（T+1/涨跌停/停牌/ST）在事后修正层无法穷尽验证；事件层逐事件可查、可单测。
- **边界**：向量化仅用于筛选与校验（参数扫描、数据不变量校验），**不作结论**。

## ADR-2 数据存储：直读现有 Parquet 主库（标准层与 DuckDB 未采纳）

- **决策**：btf **只读**直读 `market_data`（年分区 Parquet）；`tables_meta` 登记 18 表三布局；读取层统一布局与量纲。
- **现状**：`btf.data.parquet_reader`（谓词下推、`memory_map` 仅主库）+ `core.YearTableStore`（唯一 IO 入口、有界缓存、线程安全）。
- **未采纳（保留评估）**：中间"Parquet 标准层"与 DuckDB 查询层——当前规模下 PyArrow 足够，引入即引入第二套口径与额外构建负担。
- **性能证据**：子集宇宙谓词下推使十年全市场回测 **32.9s**；LIQ 十年预计算 **4.95–4.97s**。

## ADR-3 单进程起步，进程池并行，不引入分布式与消息中间件

- **决策**：事件循环单进程（确定性优先）；参数扫描用 `ProcessPoolExecutor`（含 initializer 预热）；预计算用 `ThreadPoolExecutor`（pyarrow 释放 GIL）。
- **现状**：`btf/optimize/grid.py`（workers 缺省 8）；`data/liq.precompute_liq_series`（逐年并行 + 共享 store 加锁、IO 在锁外）。
- **边界**：无 MQ、无分布式、无调度服务（NG4）；**进程池是唯一并行手段**。

## ADR-4 配置体系：分层合并 + JSON Schema 校验 + 禁硬编码路径

- **决策**：默认层 → `BTF_*` 环境 → 用户 YAML → CLI `--set`（`a__b=v`）；Schema `backtest.v1` **拒绝未知键**；路径可经环境变量覆盖。
- **现状**：`btf/config/{loader,validation,paths}.py`；`btf.app` 为覆盖解析唯一实现（`bt config-check [--resolve-strategy] [--dump]` 可预检）；`mask_config` 脱敏。
- **理由**：未知键静默忽略 = 用户以为生效实则未生效（真实事故类型）；死键必须在校验期暴露。

## ADR-5 事件模型：强类型事件 + JSONL 溯源日志

- **决策**：7 类事件（`market_open/close`、`order_submitted/rejected`、`fill`、`corporate_action`、`session_end`）以 frozen dataclass 定义；日志为 JSONL，可采样但**必须披露**。
- **现状**：`btf/domain/events.py` + `btf/engine/events_log.py`（预算前置 + 尾部 ring + 哨兵行 `kind="_sampled"`）。
- **边界**：事件日志是回放/对账依据，**不是**交易指令通道。

## ADR-6 复权与记账：名义价记账 + 公司行动按两时点入账

- **决策**：引擎以**不复权价**记账；除权/派息通过 `CorporateAction` 事件在 `ex_date`（份额/成本调整）与 `pay_date`（现金到账）分别处理；复权序列仅供研究与校验（**不进记账**）。
- **现状**：`btf/data/adjust.py`（`AdjustService`，消费侧）+ `btf/engine/loop.py` 步骤 ① + CC-4 因子一致性守卫。
- **理由**：复权基准漂移会污染成本/持仓与 NAV；名义价记账 + 事件调整是唯一可对账口径。

## ADR-7 入口策略：单一 Facade（`BTFRuntime`）+ 应用服务层 + 双入口

- **决策**：装配根唯一（`BTFRuntime`）；CLI 与 API **共用 `btf.app`**（同源实现，天然字节等价）；入口层不得含编排（契约 10）。
- **现状**：`btf/runtime.py` + `btf/app/services.py` + `btf/cli/main.py`（7 命令）+ `btf/api.py`。
- **边界**：不提供 REST/gRPC/Web（非目标）。

## ADR-8 实验管理：自建轻量 RunStore（不引入 MLflow/W&B）

- **决策**：产物 = 目录 + JSON/JSONL + HTML；四版本锁定写 `manifest.json`；对账用 `metrics_digest` + `bt verify`。
- **现状**：`btf/experiment/{store,manifest}.py`（`local_jsonl`、原子写 + fsync、状态机 `RUNNING/COMPLETED/FAILED`、manifest 主版本校验）。
- **理由**：无需服务端；纯文本产物**可 diff / 可 audit**，与"可复现优先"一致。

## ADR-9 规则参数单一真源：RulesProvider 对接赤潮规则源

- **决策**：规则数值**禁止**在 YAML 手抄；经 `rules: {source: mirror_json|static, path: …}` 声明加载；仅显式 `override: true` 的研究变量可覆盖。
- **现状**：`rules_provider` 注册点 + `rules_version`/`rules_overrides` 入 manifest + **四方机检**（源 ↔ 镜像 ↔ 消费绑定 ↔ btf 缓存，含状态机语义锚点）。
- **理由**：规则升级后"行为是否漂移"必须有可比对基准，手抄即失去基准。

## ADR-10 研究层（向量化）不作为结论来源（API 未交付）

- **决策**：不交付向量化研究 API；研究需求由 **`btf.data` 读取层 + Notebook** 满足；参数扫描由 `grid_search`（走标准引擎链路）承担。
- **现状**：`btf.optimize.grid`（每组合 `load_config → build → run`，与单跑逐位一致）；**无** 向量化回测入口。
- **理由**：两套口径的治理成本高于收益；研究结论不进入验收证据链。

## ADR-11 依赖管理：最小依赖集 + 依赖预算门禁

- **决策**：运行依赖保持最小（pyarrow/numpy/pyyaml/jsonschema + 可选 plotly）；`pandas` 仅研究与测试；不引入编译型扩展；依赖预算常跑门禁。
- **现状**：`pyproject.toml` + `tools/deps_budget.py`（`bt check` 第 4 项）；`matplotlib` 不引入（图表用 Plotly 或内置降级渲染）。
- **理由**：单人维护下"少依赖 = 少升级风险"。

---

## ADR 索引（当前状态）

| ADR | 主题 | 状态 |
|---|---|---|
| ADR-1 | 分层可插拔 + 事件驱动内核 | 生效 |
| ADR-2 | 主库直读（标准层/DuckDB 未采纳） | 生效 |
| ADR-3 | 单进程 + 进程池/线程池（限定场景） | 生效 |
| ADR-4 | 分层配置 + Schema + 禁硬编码路径 | 生效 |
| ADR-5 | 强类型事件 + JSONL 溯源日志 | 生效 |
| ADR-6 | 名义价记账 + 两时点公司行动 | 生效 |
| ADR-7 | 单一 Facade + 应用服务层 + 双入口 | 生效 |
| ADR-8 | 自建轻量 RunStore | 生效 |
| ADR-9 | 规则参数单一真源（RulesProvider） | 生效 |
| ADR-10 | 研究层不作结论来源（API 未交付） | 生效 |
| ADR-11 | 最小依赖集 + 依赖预算门禁 | 生效 |

**新增 ADR 的流程**：涉及 S1 契约（[03 §7.4](03-总体架构方案对比与推荐.md)）的破坏性变更必须先在此登记，再改代码 + 全量回归 + CHANGELOG。

---

> **下一篇**：[13-可视化架构与设计.md](13-可视化架构与设计.md) —— 报告章节、图表插件与渲染级断言。
