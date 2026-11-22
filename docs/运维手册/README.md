# 运维手册（docs\运维手册）

> **定位**：存放「不属于架构体系、但值得长期留存」的技术文档。
> 架构规范与账本**不在本目录**，见 §一。
> 事实锚点：2026-11-21

---

## 一、架构文档导航（在 `docs\代码架构\`）

| 体系 | 路径 | 版本 | 覆盖 |
|---|---|---|---|
| **BT-Foundation（btf）** | `../代码架构/回测工具架构/` | v0.4（btf 7.0.0 基线，2026-11-20 校准） | 01–17 架构规范（冻结）｜**18 使用指引**｜**19 CLI 手册**（旧 18 分步编码计划 / 旧 19 审查演进记录已于 2026-11-20 归档） |
| **MD-Foundation（mdf）** | `../代码架构/全量数据库架构/` | v1.5（2026-10-22） | 01–17 架构规范（**旧 18 审查记录已归档**） |

> ⚠ **移动或改名须同步守卫测试。**
> `btf/tests/integration/test_doc_claim_guards.py:42` 以 `_HERE.parents[3] / "docs" / "代码架构" / "回测工具架构"` 硬编码该路径；
> 目录一挪，文档声明守卫测试直接失败（`test_a_count_guard` 等会 FileNotFoundError）。

---

## 二、本目录收录标准

| ✅ 收 | ❌ 不收 |
|---|---|
| 运维手册、进程管理、环境配置 | 架构规范 01–17（→ `../代码架构/`） |
| 工具使用说明、CLI 补充手册 | 分步编码计划（原 18 号，已归档） |
| 故障排查与处置流程 | 审查账本（原 19 号 / 全库 18 号，已归档） |
| 一次性工程决策的长期说明 | 一次性派单 / 回执（→ 归档账本） |
| 跨会话需要反复查的操作口径 | 短期草稿（→ `_scratch/`，用完归档） |

**判据一句话**：**这里回答「怎么用、怎么排障」，`../代码架构/` 回答「为什么这么设计」。**

---

## 三、现有文档

| 文件 | 内容 | 最后更新 |
|---|---|---|
| `依赖归属表.md` | 五份依赖文件（根 `pyproject` / `requirements.txt` / `requirements.lock.txt` / btf 两份）各自角色、加依赖改哪几份、版本冲突裁决、发版后必做重装回读 | 2026-11-04 |
| `进程管理.md` | 采集进程定位与操作（`alt.oversize_fetch` / `alt.list_backfill`），含判据关键词、批量筛选、停止与清理 | 2026-10-23 |

---

## 四、与其他文档区的关系

| 位置 | 回答什么问题 |
|---|---|
| `代码/docs/运维手册/`（本目录） | 这个工具 / 进程怎么用、出问题怎么排 |
| `代码/docs/代码架构/` | 系统怎么设计、为什么这么设计、改了什么、谁验收的 |
| `文档/数据与工程/` | 某张表某天怎么取、某接口什么权限 |
| `赤潮/docs/` | 策略体系（v7.x 选股 + 风控规则） |
| `赤潮/reports/` | 派单、验收报告、作战包 |
| `赤潮/AGENTS.md` | 单一规则源（2026-11-19 起替代 `rules/single-source.md`）、当前持仓 |

> **引用不复制** —— 同一事实只在一处维护，其余位置写路径。避免口径漂移。

---

## 五、新增文档约定

1. 文件名用中文描述 + 必要时带日期（`操作说明-20261102.md`）。
2. 开头三行写清：**适用对象 / 前提条件 / 事实锚点日期**。
3. 涉及命令的，给可直接粘贴的完整命令，不写「略」。
4. 涉及判据的，给可验证的判据（退出码、grep 计数、哈希），不写「应该没问题」。

---

## 六、退出码契约（2026-11-21 D2 起）

| rc | 含义 |
|---|---|
| `0` | 成功（含显式 `source_empty` 且该表档位允许） |
| `1` | 受控业务失败（任一表 / 任一日 / 任一段失败） |
| `2` | 用法或配置错误（argparse、配置缺失、预算超限） |

- 只读命令未显式返回按 0；业务命令必须显式返回 `int`，失败不得吞成 0。
- 实例锁冲突固定归 `1`。
- `--json` 只约束 stdout 为纯 JSON，不改变退出码；失败提示写 stderr。
- 逐命令映射与已知缺口见 `代码/cli/README.md` §退出码契约。

## 七、残锁处置（2026-11-22 D9）

运行中可能留下三类锁文件，**一律不自动删除**（防误删活锁）：

| 锁 | 位置 | 超时/内容 |
|---|---|---|
| 断点锁 | `<表目录>/.checkpoint.json.lock` | 文件内容 `PID 时间戳`；`FileLock(timeout=30s)` |
| parquet 目录写锁 | `<表目录>/.write.lock` | 文件内容 `PID 时间戳`；**目录写锁超时 300s** |
| 计划任务锁 | `market_data/.schedule.lock` | 文件内容 `PID 时间戳`；日更入口持有 |

处置步骤：

1. 读锁文件首行 PID 与时间戳；
2. `Get-Process -Id <PID>`（或 `Get-CimInstance Win32_Process`）确认 PID **不存在**；
3. 确认当前无 `python.exe` 正在写库；
4. 人工删除锁文件；随后用 `db ckpt --show` / 日更试跑确认账实一致。

> 不得把「超时」当成「残锁」：活进程持锁时删除会导致双写。2026-11-21 的
> `.schedule.lock`（PID 已死）即按上述步骤由用户确认后删除。

## 八、格式化门禁与 BOM 契约（2026-11-22 D12）

- **以 `python tools/gate.py` 为准**：全量门禁 6 步（双 BOM / ruff check /
  ruff format --check / `main.py --help` / config-check / pytest）。
- 裸 `ruff format --check .` 会对 **11 个单 BOM 文件**报 `would be reformatted`
  ——这是 `ruff format` 写回时剥离 BOM 与 G3 字节契约的冲突，**不是格式漂移**。
- 权威清单在 `tools/gate.py::_BOM_FILES`，并有根测试
  `tests/test_doc_guard_engine.py::test_bom_file_list_matches_disk` 钉住
  「清单 == 实际单 BOM 文件集合」；BOM 文件增删必须先改清单再跑门禁。
- 双 BOM（≥2 个 BOM）不在豁免范围：gate 第 1 步直接红并点名文件。

## 八、库运维命令速查（`db` 命令族，2026-11-22 D9）

> 全部入口：`cd D:\量化策略\代码` 后 `.\_venv\Scripts\python.exe main.py db <子命令> --help`。
> 旧平铺别名 `db-scale-check` / `db-schema-align` / `db-fingerprint` / `ckpt` 仍可用，但文档统一写 `db <子命令>`。

| 子命令 | 用途 | 关键参数 / 默认 |
|---|---|---|
| `db ckpt --show` | 断点 × 磁盘交叉核对（只读） | `--json`；`--fix` 按磁盘回填；`--clear` 清虚记；`--table T`；默认干跑，`--execute` 才写 |
| `db audit` | 全库体检 **5 段**：`checkpoint` / `dup` / `truncation` / `gap` / `scale` | `--section all`（或逗号组合） `--only T` `--exact` `--json` |
| `db scale-check` | 数值域量级锚体检（≠ `db audit --section scale` 的目录/字节规模） | `--report`（默认） `--stats` `--tables T` `--json P` `--out P` |
| `db schema-align` | 跨年度分区 schema 漂移扫描/修复（≠ `db schema` 的建表定义校验） | `scan` / `fix` `[tables ...]` `--all` |
| `db fingerprint` | 全库指纹门禁（独立，不与 `db audit` 合并） | `--build` `--verify` `--report` `--deep` `--allow-added` `--root` `--baseline` |
| `db meta-close` | 元数据刷新后的 R12 收尾（声明重算/补表重建） | 默认干跑；`--apply` 写盘（先自动备份）并整跑 `regress` |
| `db backup` | 日备（硬链快照＋校验） | `--status` 只读；`--mirror` 日备（默认干跑）；`--execute` 真跑 |
| `db stale-fix` | 基金 `source_empty` 注册表对账 | 默认干跑；`--apply` 清理并同步 evidence/count（先自备份）；`--json` |

> 计数口径：`doctor` 现为 **13 组 / 32 项**（13 个 `check_*` 分组）；`db audit` 为 **5 段**。

## 九、T-01 六步日更 bat（2026-11-22 D9）

| 项 | 位置 / 内容 |
|---|---|
| 17:30 计划任务入口 | `代码\scripts\run_schedule_daily.bat` → `main.py schedule --apply`（纯 ASCII，`%~dp0` 自定位，日志 `代码\logs\schedule\<YYYYMMDD>.log`） |
| 六步 DB 维护 bat | `赤潮\scripts\db_maintenance_daily.bat`（日志 `赤潮\.context\automation\db-maintenance\logs\db-maintenance.log`） |
| 步骤 0 | `db_maintenance_precheck.py` 交易日预检；非交易日直接 `exit /b 0` |
| 步骤 1/6 | `main.py gap-update --group all`（主干失败 ⇒ `[FAIL]` + `exit /b 1`） |
| 步骤 2/6 | `main.py redtide-supply`（失败同上） |
| 步骤 3/6 | `main.py fund-nav`（失败同上） |
| 步骤 4/6 | `python -m alt.update --tier P0,P1,P3,P4,P0_EXT`（失败仅 `[WARN]`，非阻断） |
| 步骤 5/6 | `main.py db stale-fix --apply`（失败仅 `[WARN]`） |
| 步骤 6/6 | `main.py db fingerprint --verify --report`（失败仅 `[WARN]`） |
| `--dry-run` | 只跑步骤 1/4 的 `--dry-run`（2/3/5/6 无 dry-run 模式，跳过） |
