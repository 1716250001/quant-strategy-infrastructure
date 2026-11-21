# common/ — 跨模块公共层（路径 / IO / 日历 / 代码 / 日志的唯一实现）

> 事实锚点：2026-11-21 ｜ 模块数：10 ｜ 入口：显式子模块导入，如 `from common.paths import MARKET_DATA_DIR`

## 职责

- 把此前散落在 `fetch` / `push` / `tools` 的重复底层能力收敛为一处，供各包单向复用。
- 只提供通用能力，不承载业务规则（业务判断留在 `fetch`/`tools`）。
- 统一三种存储布局（`by_code` / `by_year` / `single`）的读取与写入语义，布局调整时下游零改动。

## 关键模块

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 仅包说明与导入约定，不 re-export 符号，避免隐式依赖 |
| `paths.py` | 全项目路径唯一解析入口：从 `config` 取各目录，派生 `REPORT_DIR`/`DIVERGENCE_DIR`；提供 `ensure_dir`/`compact_date`/`pretty_date`/`data_file`/`report_file` |
| `reader.py` | 布局无关读取层：`detect_layout`/`list_codes`/`codes_from_store`/`iter_year_files`/`read_code`/`read_range`，带布局与代码清单缓存 |
| `parquet_store.py` | 落盘层：`merge_append`/`upsert_grouped`（按标的增量去重）、`upsert_by_year`（按年分区）、`migrate_to_year_partitions`、`YearBatchWriter`（攒批落盘）、原子写与 schema 对齐 |
| `calendar.py` | 交易日历唯一入口：`load_trade_cal`/`open_dates`/`prev_trade_dates`，读 `metadata/trade_cal.parquet` |
| `codes.py` | 证券代码转换：`etf_to_ts_code`/`stock_to_ts_code`/`to_secid`/`to_sina_symbol` |
| `dtypes.py` | 落盘前类型归一 `coerce_float_cols`（防 str 列导致 schema 漂移） |
| `jsonio.py` | JSON 读写封装：`load_json`/`save_json`/`print_json`（读写失败默认告警返回，不静默崩） |
| `db_version.py` | 库版本 `db_version.json` 写入口：`MD-YYYYMMDD.N` 同日递增，记录 `tables_changed`/`checkpoint_rev` |
| `logging_setup.py` | `setup_logging()`：控制台 + 5 MB×3 轮转文件，幂等，由入口显式调用 |

## 与其他包的关系

- 依赖方向：`common` → `config`（`paths`/`reader`/`db_version` 取目录常量）；包内 `reader` → `parquet_store`、`parquet_store`/`calendar`/`logging_setup` → `paths`。
- 被依赖：`fetch`/`push`/`tools`/`alt`/`cli` 均单向依赖本包（如 `push.pushplus` 用 `jsonio`，`cli.app` 用 `logging_setup`）。
- 禁止反向依赖：`common` 不得 import `cli`/`fetch`/`push`/`tools`/`alt`/`wind`；`parquet_store` 对 `reader` 只能函数内延迟导入（避免循环）。

## 红线

- 路径只能从 `common.paths` 或 `config` 取，禁止 `os.path.dirname(__file__) + ".."` 自行推算。
- 读取全量库一律走 `reader`（布局无关），禁止硬编码 `{table}/{ts_code}.parquet`（`by_year` 下文件名是年份）。
- 写入必须走 `parquet_store`；其写函数已接线 `_invalidate_reader_cache`，绕过则会出现"写后读旧"。
- 交易日历只能经 `common.calendar` 读 `trade_cal.parquet`，禁止各模块自读。
- 日志初始化不得在 import 时发生，只能由入口显式 `setup_logging()`；重复调用不得叠加 handler。
- `db_version` 写失败 best-effort 不阻断数据主流程；版本号格式与落点（`metadata/db_version.json`）不得另立。
