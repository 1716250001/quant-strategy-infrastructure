# alt — akshare 备用数据源（独立库）

> 事实锚点：2026-11-21 ｜ 模块数：14 个生产模块 + 1 个验证脚本 ｜ CLI 入口：`python main.py alt-update` / `alt-audit` / `alt-backfill` / `alt-us` / `alt-verify`

## 职责

- 采集主库没有的 akshare 数据：估值情绪锚、QVIX、海外宏观、涨停/赚钱效应快照、港股/美股/外盘基本面，落盘到 `D:\全量数据\alt_data`。
- 负责取数规格、按域名限流、归一化落盘、历史回补、每日增量、list 轮转、数据体检与主库联查。
- 只读主库（`market_data`），只写 alt 库与临时目录；不写 wind 库。
- 不做复权计算，只暴露复权展开入口；不承担主库 tushare 表的采集。

## 关键模块

| 文件 | 作用 |
|---|---|
| `__init__.py` | 包级说明与 `__all__` 导出清单 |
| `config.py` | alt 库路径/断点/限流参数；`assert_writable` 写保护与越界计数；日期归一化 |
| `spec.py` | 表规格唯一真源：`TableSpec` 字段 + `ALL_SPECS` / `BY_TIER` / `TIERS` |
| `io.py` | 归一化（日期、枚举、常量列）+ 落盘（复用 `common.parquet_store`），落盘前 QC |
| `reader.py` | 读取 alt 表、`join_main` 主库联查、`expand_qfq_factor` / `expand_us_qfq_factor`、`value_on` |
| `rate.py` | 按域名档位限流、被拦长静默、瞬态错误短退避 |
| `urls.py` | 接口名 → 真实上游域名唯一真源（`guess_url`） |
| `backfill.py` | 历史回补编排 + 独立三态断点 `AltCheckpoint`（支持 dry-run） |
| `update.py` | 每日增量：single 全量刷新 / by_year 按日增量 + 复核窗口 |
| `list_backfill.py` | list 模式引擎：清单解析 → 轮转调度 → 批量落盘 → 影子目录整表重建 |
| `us_backfill.py` | 美股建库专用入口（mode=list），整表重建 + 请求超时补丁 |
| `oversize_fetch.py` | 每标的多请求引擎（`hk_val_pct`），独立断点 + 分位/Schema 门禁 |
| `verify_spec.py` | 建库前规格校验（接口、日期列、去重键、枚举值等，零落盘） |
| `audit.py` | 数据体检：断点×落盘、日期集合、重复、缺口、规模、隔离、轮转 + 结构报告 |
| `compat.py` | akshare 兼容补丁（`fund_etf_scale_szse` 在 pandas>=2.0 的 TypeError） |
| `tests/verify_os_r1r.py` | OS-R1/R1R/R1R2 返工验证脚本（零副作用） |

## 与其他包的关系

- 单向依赖 `config`（工程路径）与 `common/*`（parquet_store / reader / calendar / dtypes）；`config` 及既有模块不反向依赖 alt。
- `cli/alt_cmds.py` 是 `main.py` 子命令的薄代理，转调本包各模块的 `main()`。
- 消费侧通过 `alt.reader` 读取并与主库联查；`fetch`、`wind` 不 import alt。
- 禁止反向依赖：alt 不 import `fetch`/`wind`，也不写它们的库。

## 红线

- 物理隔离：所有落盘先过 `alt.config.assert_writable`；主库及其备份目录在 `FORBIDDEN_WRITE_ROOTS` 中，越界抛 `AltPathViolationError` 并计数。
- 日期统一归一化为 `YYYYMMDD` 字符串向主库对齐；`by_year` 去重键必须含标的列或 `"*"`，enum 模式必须写入枚举列。
- 断点、轮转水位、更新状态各自独立文件，绝不与主库 `.backfill_checkpoint.json` 混用。
- 断点三态：调用失败不记；成功但空数据记断点；成功有数据落盘后记。
- 复权展开唯一入口 `alt.reader.expand_qfq_factor` / `expand_us_qfq_factor`，禁止自行 ffill 或按交易日等值 join。
- 域名判定唯一真源 `alt/urls.py`；被拦判定禁止裸数字匹配（如 `429`），必须带上下文。
