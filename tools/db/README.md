# tools/db — 全量数据库治理子包（体检 · 结构 · 迁移 · 断点 · 新鲜度 · 指纹）

> 事实锚点：2026-11-22 ｜ 模块数：13 ｜ 调用方式：`python main.py db <子命令>`（旧平铺名 `db-audit` 等为隐藏别名，等价）

## 职责

- 收口主库（115 表，库版本 MD-20261118.15）的库侧治理：结构扫描、健康体检、布局迁移、冗余清理、断点对账、新鲜度、量级锚、全库指纹。
- 除 `schema_align fix`、`clean --apply`、`migrate --apply`、`meta-close --apply`、`ckpt_clean_once --apply` 外均只读；所有写命令默认干跑。
- 不取数、不补数：补拉仍走 `gap-update` / `backfill`；本包只给证据与登记。
- `__init__.py` 仅声明 2026-11-05 W4 分层（18 号 §330.2），无逻辑。

## 模块清单

| 文件 | 作用 | 对外入口 |
|---|---|---|
| `audit.py` | 体检五项：断点×落盘、重复二分（复合主键 vs 整行重复）、截断防护、缺口按今天截断、规模 | `db audit` → `run_audit()` |
| `schema.py` | 全库结构扫描，pyarrow 元数据取精确行数，写 `db_schema.json` | `db schema` → `run_schema()` |
| `schema_align.py` | 跨年度分区 arrow schema 漂移 `scan`/`fix`，逐文件原子写 | `db schema-align` → `main()` |
| `clean.py` | 主键多版本压成一行、保留首非空，自动备份＋非空率校验 | `db clean` → `run_clean()` |
| `migrate.py` | by_code → by_year 迁移，分年流式＋行数双向校验 | `db migrate` → `run_migrate()` |
| `ckpt_clean_once.py` | 清 page 域 `once` 死键，写前自动 `.bak-once-<时间戳>` | `python -m tools.db.ckpt_clean_once [--apply]` |
| `fingerprint.py` | 全库树 parquet/state/other 三类分账指纹，快速档/`--deep` 两档 | `db fingerprint --build / --verify` |
| `freshness.py` | ds（内容）/dw（落盘）双条件分档，只读恒 exit 0 | `db freshness` → `run()` |
| `meta_close.py` | 元数据刷新后九步 R12 收尾（声明重算＋补表重建），默认干跑 | `db meta-close --apply` |
| `scale_check.py` | L1 量级锚 / L2 跨表勾稽 / L3 月度稳定性，只读 | `db scale-check --report / --stats` |
| `gen_report.py` | 由 `db_schema.json` 生成 Markdown 结构报告 | `db report` → `run()` |
| `checkpoint_registry.py` | 断点登记只读门面 `get/timestamp/describe/domains_of` | 仅 import |
| `known_gaps_patch.py` | 非 fund 表残余缺口补丁：校验、去重合并、备份＋原子写，默认干跑 | `python -m tools.db.known_gaps_patch --patch <json> [--apply]` |

### 数据目录：`tools/db/data/known_gaps.json`

- 位置：`tools/db/data/known_gaps.json`（2026-11-21 由 `tools/known_gaps.json` 迁入）；同目录保留两份 reprobe 备份 `.bak-reprobe-20261109_070150`、`.bak-reprobe-20261116_072053`。
- 用途：主库「源端空返回」登记表；读方 6 处——`check_coverage`、`regress_pipeline`（R17/R18）、`meta_close`、`fund_stale`、`fetch/backfill.py`、`fetch/schedule_runner.py`。
- 写入纪律：日常**只读对账**，禁止手改或脚本直写；改写须走 `python main.py db meta-close --apply`（自动备份＋整跑 regress）并单独批准。
- 另一受控写入口为 `db stale-fix --apply`（只清 stale 并同步 `evidence`/`count`，写前自备份）；两者之外无写权限。

### 非 fund 表残余缺口补丁（`known_gaps_patch.py`）

```text
python tools/db/known_gaps_patch.py --patch <json>          # 干跑，只打印统计
python tools/db/known_gaps_patch.py --patch <json> --apply  # 写盘（需单独批准）
```

- 补丁结构：`{"patch": {<table>: {source_empty_ranges, source_empty_evidence, note, real_gap_backfill}}}`，只增不减。
- 校验：表名须在 `config` 表集合（`DB_AUDIT_PKEYS ∪ TABLE_DATE_COL`）内；`code` 匹配 `^\d{6,7}\.(SH|SZ|BJ)$`；`from/to` 为合法 8 位日期；`keys` 为正整数。
- 合并：既有 dict 字段原样保留；range 按 `(code, from, to)` 去重追加；evidence 逐码合并（既有 `probe_rows` ≥ 新值时不覆盖）；`note` 追加；`real_gap_backfill` 整体写入。
- `--apply` 先把目标复制为同目录 `.bak-known-gaps-<YYYYmmdd_HHMMSS>`，再以临时文件 `+ os.replace` 原子写入（UTF-8 无 BOM、1 空格缩进）。
- **边界**：`adj_factor` / `daily_basic` / `stk_limit` / `fund_adj` 为 by_date 表，本次只登记「码+区间」残余键；`fetch/backfill.py` 现有 schema 只认 `source_empty`（码级）/`source_empty_dates`（日期级），**不改其判定**，pair 级消费待 D10（允许改 `fetch/**` 后实施）。

## 与 CLI 的对应关系

| CLI | 模块 |
|---|---|
| `db audit` | `tools/db/audit.py` |
| `db schema` | `tools/db/schema.py` |
| `db report` | `tools/db/gen_report.py` |
| `db clean` | `tools/db/clean.py` |
| `db migrate` | `tools/db/migrate.py` |
| `db freshness` | `tools/db/freshness.py` |
| `db scale-check` | `tools/db/scale_check.py` |
| `db schema-align` | `tools/db/schema_align.py` |
| `db fingerprint` | `tools/db/fingerprint.py` |
| `db ckpt` | `tools/ckpt_tool.py`（根） |
| `db meta-close` | `tools/db/meta_close.py` |
| `db backup` | `tools/data_backup.py`（根） |
| `db stale-fix` | `tools/research/fund_stale.py` |

## 红线

- `known_gaps.json` 只读对账；改写只能经 `meta-close --apply` 或 `stale-fix --apply`，且须单独批准。
- 写库命令默认干跑；`migrate` 的 `--apply` 与 `--dry-run` 同给时按干跑。
- `freshness` 恒 exit 0，仅 `--strict` 有红/黄才非零；缺时间戳显示「未知」，不「修绿」。
- `scale_check` 不进 `n_issue`、不改 `db audit` 退出码；越界只报不改数据。
- `schema_align` 对列集合真不一致的表不动、不补列；cast 失败的分区如实报告。
- `ckpt_clean_once` 只删严格等于 `'once'` 的键；`fingerprint` 不接 regress，由人工/定时调用。
- `checkpoint_registry` 无写方法，写侧仍归各 owner 模块。
