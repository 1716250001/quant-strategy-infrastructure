# config/ — 全局配置单一真源（路径 / 表结构 / 参数 / 凭据）

> 事实锚点：2026-11-21 ｜ 模块数：15（14 个子模块 + `__init__`）｜ 入口：`from config import X` / `import config.paths`

## 职责

- 集中所有路径、表结构、标的清单、限频、补数/日更/排班参数与凭据；一处修改全局生效。
- `__init__.py` 只做 re-export（`from config.<子模块> import *`）并持有 `__version__ = "7.0.0"`，保持既有 `from config import X` 公开 API 不变。
- 纯声明层：除 `paths.py` / `tokens.py` 读取环境变量（`tokens` 另读 `.env`）外不做 IO、不探测磁盘、不执行业务。

## 关键模块

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 版本号唯一真源 `__version__`；聚合 re-export 全部子模块 |
| `paths.py` | 盘符锚点派生路径：`PROJECT_DIR`/`DATA_ROOT`/`MARKET_DATA_DIR`/各表子目录；`CHICHAO_*` 环境变量可覆盖 |
| `tables.py` | `TABLE_DATE_COL`（表→分区列，写入决策权威）、`SOURCE_START_BASELINE`、`DB_CLEAN_TARGETS`、`DB_AUDIT_PKEYS` |
| `daily.py` | `DAILY_UPDATE_GROUPS`（core/extend）、`DAILY_UPDATE_EXTRA`、`PERIODIC_UPDATE_TARGETS`、基金净值参数 |
| `backfill.py` | 补数执行参数：起点、指数清单、`BACKFILL_PAGE_CONF` 分页、`BACKFILL_FLUSH_CONF` 批量落盘 |
| `backfill_targets.py` | `BACKFILL_TARGETS` 补数目标单一字面量（豁免 ≤500 行） |
| `schedule.py` | 周期排班：`FREQ_DAYS`、`SCHEDULE`、`RELEASE_DAY`、`GRAYSCALE`、`EXCLUDED` |
| `scale_anchors.py` | `SCALE_ANCHORS` 全库数值域量级锚（表→列→lo/hi/why） |
| `checkpoint_registry.py` | 断点载体登记：`CheckpointDomain`、`DOMAINS` 及其查询函数（纯声明，无 IO） |
| `tokens.py` | `_secret()` 凭据读取（环境变量 > 代码目录 `.env` > 空串）；`TS_TOKEN`/`PUSHPLUS_TOKEN`/`PUSHPLUS_CHANNEL` |
| `universe.py` | 标的清单：赤潮目录、`INDEX_MAP`、`WIDE_ETF`、基金代码模式、期货/期权标的 |
| `params.py` | 计算参数 `LOOKBACK` 等 + IV/波动率监控（`IV_OPTION_ETFS`、`IV_MA_WINDOWS`） |
| `download.py` | 限频与下载：`API_RATE_LIMIT`、`TS_RETRY_DELAYS`、`FETCH_MAX_WORKERS`、`DOWNLOAD_PHASES` |
| `monitor.py` | 盘中监控：`MONITOR_SYMBOLS`/`MONITOR_PERIODS`、东财退避、新浪备用源参数 |
| `permissions.py` | `LEAKY_PERMISSIONS` 权限漏网登记、`LEAKY_IDLE_EXEMPT` 日更空转豁免 |

## 配置真源表

| 配置项类别 | 真源模块 | 典型符号 |
| --- | --- | --- |
| 路径 / 盘符锚点 | `paths.py` | `PROJECT_DIR`、`MARKET_DATA_DIR`、`ALT_DATA_DIR` |
| 表结构（分区列 / 源端起点 / 清理目标 / 体检主键） | `tables.py` | `TABLE_DATE_COL`、`SOURCE_START_BASELINE`、`DB_CLEAN_TARGETS`、`DB_AUDIT_PKEYS` |
| 日更分组 / 周期更新 | `daily.py` | `DAILY_UPDATE_GROUPS`、`DAILY_UPDATE_EXTRA`、`PERIODIC_UPDATE_TARGETS` |
| 补数目标 / 补数参数 | `backfill_targets.py` / `backfill.py` | `BACKFILL_TARGETS` / `BACKFILL_PAGE_CONF` |
| 周期排班频率 | `schedule.py` | `FREQ_DAYS`、`SCHEDULE` |
| 维度锚点（数值域量级） | `scale_anchors.py` | `SCALE_ANCHORS` |
| 断点载体登记 | `checkpoint_registry.py` | `DOMAINS` |
| 凭据 / 推送默认值 | `tokens.py` | `TS_TOKEN`、`PUSHPLUS_TOKEN` |
| 标的清单 | `universe.py` | `INDEX_MAP`、`WIDE_ETF` |
| 限频 / 并发 / 下载阶段 | `download.py` | `API_RATE_LIMIT`、`DOWNLOAD_PHASES` |
| 计算参数 / IV 监控 | `params.py` | `LOOKBACK`、`IV_MA_WINDOWS` |
| 权限漏网 / 空转豁免 | `permissions.py` | `LEAKY_PERMISSIONS` |
| 盘中监控参数 | `monitor.py` | `MONITOR_PERIODS`、`EM_BACKOFF_BASE` |
| 版本号 | `__init__.py` | `__version__` |

**禁止在业务模块硬编码路径/参数，一律经 config 取用**（路径禁止 `__file__ + ".."` 推算，表参数禁止在 fetch/tools 内复制字面量）。

## 与其他包的关系

- 依赖方向：`common` / `fetch` / `push` / `tools` / `cli` → `config`，单向；`config` 不依赖任何业务包。
- 包内仅允许子模块依赖 `config.paths`（`daily.py`/`tokens.py`/`universe.py` 即如此）；其余子模块之间不互引，避免循环。
- `common.paths` 是运行期路径入口，其值全部来自 `config.paths`；`config.paths` 才是锚点真源。
- 禁止反向依赖：`config` 不得 import `common`/`fetch`/`cli`/`tools`/`push`/`alt`/`wind`。

## 红线

- `__version__` 是版本唯一真源（`pyproject.toml` 经 setuptools attr 读取），全项目不得另写版本号。
- 凭据明文不得入库，只走环境变量 > `.env`；`.env` 勿外发/勿同步云端。
- `TABLE_DATE_COL` 是「按哪列分区」的写入决策权威；条目级 `date_col` 为条目真源，两处必须同值（`tools/config_check.py` 判据 ⑤b 机检）。
- `backfill_targets.py` 为单一字面量，禁止按行拆分；`schedule.py` 不含取数参数（参数一律取自 `BACKFILL_TARGETS`）。
- 标 `[ARCHIVED ... G1]` 的死配置保留仅为行号稳定，禁止新增引用。
