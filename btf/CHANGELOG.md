# 变更日志（btf 回测工具）

本文件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 结构；
版本号遵循语义化版本（M0 由 18 号分步编码计划定义）。

## [1.0.0] — 2026-09-29

**发布主题：btf 工具 **1.0 定版**（可自证 / 可自检 / 可自退）—— FF-3 定版批次**

> 依据：**老大令「审查通过，定版 V1.0」**（2026-09-29 21:31）+ 19 号 **§56.6 FF-3 改动清单**（赤潮全仓独立扫描）；
> 执行留痕见该文 **§57**。**边界**：本文的"1.0"只对**工具**成立（可自证/自检/自退），**研究体系恒演进、无 1.0 概念**（16 号）。

### 变更（Changed）——定版动作

- **版本定版**：`btf/_version.py` → **`"1.0.0"`**（单一真源；`pyproject` 走 `dynamic` + `attr`，未安装亦生效）；
  实测 `import btf` → `1.0.0`；真入口 run 的 `manifest.code_version.package_version` = **`btf==1.0.0`**
  （防 **NOTE-4** 类"版本声明与产物不符"复发；`run_id 20260929_215213_562fd0`）。
- **两条写死版本前缀的测试改形制断言**（bump 后必红，赤潮扫描发现）：
  - `tests/test_smoke.py::test_import_and_version`：`startswith("0.5")` → **`re.fullmatch(r"\d+\.\d+\.\d+", …)`**；
  - `tests/integration/test_api_facade.py::test_import_btf_is_light`：子进程 code 串内同改（**不再写死任何前缀**，v2.0 亦不复发）。
- **文档收口（1–17）**：版本行统一 `1.0.0`；计数（**876** 测试 / `bt check` **9/9** / 契约 **10**）与基准
  （**B1 8.86s · B2 16.95s · B3 43.3s · B4 1670s · LIQ 4.90s**，隔离单跑）全量同步；
  **删除已关闭项与过时表述**（R4 剩余 / EX-4 卡口 / 0.X 托管口径 / 首版错误数字）——只保留最新内容。
- **文档 18 重建**：时间线总览表（**1–22 行**：阶段 0 / PoC-1–3 + M1–M3 / v0.2 … 0.5.16 / **1.0.0 定版**）；
  批次段**按时间正序重排** + 标题统一 `### <版本> 交付：<主题>（<日期>）` + 表列统一 `| # | 项 | 落地与实测证据 |`；
  新增 **v1.0.0 交付段**；§八·B 删除与订正总表 **+12 行**（27–38）；§八·C 收口更新为 1.0.0 态。
- **FF-1 留痕（P2-NEW-11 复核）**：§28.4 台账 #3 / §28.5 结论行 / §38.7 三区清单 已更正为
  **+43.33pp / IR 0.112 / P3 0.640 / P2 −8.60pp**；§51.6（历史审查段）保留原数字 + **勘误标注**（存真原则）；
  本轮**逐处复核通过**（19 号 §57.1）。
- **入册两条披露（赤潮 §56.2.4 增量观察）**：① 修正后 **P1 超额符号由负转正**（−26.43% → **+2.98%**）；
  ② **P2 在 `top_k` 5↔10 上符号翻转**（−8.60% / **+2.58%**）⇒「参数不稳健」的证据**比首版更强**。
- **登记（环境诱导）**：全量 pytest 在 WorkBuddy `sitecustomize` **safe-delete 守卫**下会出现
  `tests/integration/test_architecture_guard.py` 链式 4 失败（守卫拦 `unlink` ⇒ 违例文件删不掉）；
  **逐例独立单跑 4/4 全过、生产目录无残留** ⇒ 判**环境诱导**（非代码缺陷），同 §56.1。

### 定版日动作（判据 6）

- **跨版本源码冷备**：`python tools/cold_backup.py --reason "btf 1.0.0 定版…"`（**定版快照**，含本轮全部改动）；
  `--verify` 逐文件 sha256 一致 ⇒ **可回退**能力就位。

### 实测摘要（可复核，按铁律新 12）

```
$ ruff check .                          → All checks passed!
$ python -m pytest -q                   → 876 passed（exit 0，100%；隔离单跑）
$ python tools/check.py                 → bt check: 9/9 通过
$ lint-imports                          → Contracts: 10 kept, 0 broken
$ python tools/check_data_quality.py    → PASS（正样本 + ①–⑦ 双向）
$ python tools/check_io_boundary.py     → [ok]（80 文件；仅 core + parquet_reader 触磁盘）
$ python tools/run_v77_robustness.py --ee1
                                        → 六项与双路径参照值逐位一致（run_id 20260929_215213_562fd0）
$ python -c "import btf; print(btf.__version__)"          → 1.0.0
$ manifest.code_version.package_version                   → btf==1.0.0
$ python tools/cold_backup.py --verify 备份/<定版快照>      → 逐文件 sha256 一致
版本 0.5.16 → 1.0.0
```

### 余项（不阻塞定版）

- **私有托管 push**（判据 7 落地动作，老大）；数据抽样对账（需第二数据源）；数据治理 **OBS-7/8**；稳健性继续迭代（业务恒演进）。

## [0.5.16] — 2026-09-29

**发布主题：批 11 = 轮次十一处置 —— EE-3（基准端点缺陷 P1-NEW-9 定位+修正+回归）+ P3-NEW-6（档位容差可证伪化）**

> 依据：19 号 **§54 轮次十一**（**EE-1 ❌ 不通过 → P1-NEW-9**；**EE-2 ✅ 真交付 → P3-NEW-6** + 观察 5 项 + NOTE-4）；
> 执行留痕见该文 **§55**。**老大令：根据最新审查结果修正。** 性质：**证据/披露修正**（不改策略语义）。

### 修复（Fixed）——EE-3：**P1-NEW-9** 基准端点被静默截断

- **缺陷**：`tools/run_v77_robustness.py::_bench_series` 用 `start <= str(day) <= end`
  做**字典序比较**——`day` 来自 parquet（紧凑 `"20260923"`）、`start/end` 为 `"2026-09-23"`
  （连字符）；第 5 字符 `'0'(0x30) > '-'(0x2D)` ⇒ **`end` 所在年份的行被整体过滤** ⇒
  基准终点静默截到**上一年年末**（`2025-12-31`）而策略净值仍为完整区间 ⇒ **两侧端点不一致**。
- **症状**：① EE-1 对照"新口径"三格误报 `基准 +19.09% / 超额 +4.30% / IR −0.0365`
  （`+19.09%` 精确等于 `20230103→20251231` 的基准收益）；② **§50.5 稳健性矩阵 10 格全部受影响**
  （P1 基准由实值 **−13.21%** 被错算为 `+16.19%`）。
- **修复**：① 新增 `_ymd()` 规范化，**日期比较一律用紧凑式**；② `_excess_ir` 返回
  **四端点**（`strat_first/last`、`bench_first/last`），`run_cell` 逐格落盘 `aligned`；
  ③ `main` 对未对齐格打印 ⚠ 并写 `endpoint_misaligned`；④ 回归
  **`tests/unit/test_robustness_bench.py`（6 例）**——连字符≡紧凑 / **终点年份必被包含** /
  端点齐备 / 端点不一致可见 / **变异测试**（打残 `_ymd` 必红）。
- **验证**：修正后重跑 `run_id 20260929_203022_562fd0`（btf 0.5.16，端点 `aligned=True`）——
  **基准 `0.161882` / 超额 `0.071938` / IR `0.050426`**，与赤潮独立复算**逐位一致**（容差 1e-4）。
- **更正**：19 号 §50.5 / §53.1 / 时间线 #39 与
  `文档/市场研究/v7.7-多区间稳健性验证-20260929.md`（重写为 **v2**）同步；
  首版产物 `…-20260929-1436.json` 保留为**缺陷留痕**（其基准列不可用）。
- **矩阵重跑（修正后）**：P1 **+2.98%** / P2 **−8.60%** / P3 **+24.87%（IR 0.640）** /
  P4 **−0.76%** / **ALL +43.33%（IR 0.112）**——**方向性结论不变**
  （区间依赖 + 参数不稳健 + IR 远低于稳健门槛）。

### 修复（Fixed）——P3-NEW-6：档位容差由"细不可证伪"→**可区分**

- 原单测取 **+5.00%**（落在 `_PCT_TOL` 之内）⇒ 有无 1 档容差都不报（**弱断言**）；
  "恰在上界"的 +5.556% 因**浮点边界**（差 1 ULP）误报。
- **实现侧**：`_check_cc2` 比较加 **`_PCT_EPS = 1e-9`** 浮点松弛（语义：**恰在上界 ⇒ 未越界**）。
- **测试侧**：新增 `test_low_price_tick_at_upper_bound_not_reported`（1.80→1.90 ⇒ **不报**）
  ⇒ 该分支**可区分**（无容差必报）；可证伪边界写入测试 docstring。
- **变异测试（两路必红）**：`_TICK = 0.0` ⇒ exit 1；`_PCT_EPS = 0.0` ⇒ exit 1（还原后 0）。

### 登记（Registered）

- **OBS-6**：`strict=true` 只可读作"**CC-2 已标定**"——全市场十年 CC-3/4/5 仍有真实 findings
  ⇒ 整份质检**不可开 strict**（数据治理议题）。
- **OBS-7**：数据侧真实异常（**工具正确工作的证据**）：复权因子下降 **496** 行 / 除权缺因子 **12** 行 /
  停牌日有行情 **10** 行 / 收盘越涨跌停价 **6** 行。
- **OBS-8**：子口径④豁免量级 **31,145 行**（十年全市场）⇒ 与"数据完整性"合并审视。
- **NOTE-4**：版本声明与产物一致性⇒ **先定版再重跑**（本次产物记录 `btf==0.5.16`）；纪律入册。

### 实测摘要（可复核，按铁律新 12）

```
$ ruff check .                          → All checks passed!
$ python -m pytest -q                   → 876 passed（exit 0，100%；+7）
$ python tools/check.py                 → bt check: 9/9 通过
$ lint-imports                          → Contracts: 10 kept, 0 broken
$ python tools/check_data_quality.py    → PASS（正样本 + ①–⑦）
$ python tools/run_v77_robustness.py    → 10 格 / 0 失败；端点全 aligned=True
$ tools/check_plugin_boundary.py --update --reason "批 11（v0.5.16）…"
   → 核心基线刷新（72 文件）；1 文件留痕：btf/data/quality.py 346075aa → 362dd2d5
版本 0.5.15 → 0.5.16
```

### 未处置（登记，不属本批范围）

- **OBS-7 / OBS-8**：数据侧真实异常与缺口日 —— 归**数据治理议题**（本批只登记）。

## [0.5.15] — 2026-09-29

**发布主题：批 10 = EE-1..EE-2（裁决八派单）—— Z-6 新旧对照 + OBS-3 口径标定（数据不变量校验"暗角补全"）**

> 依据：19 号 **§52.3**（裁决八派单）；承 §51.5 问题二/问题三。执行留痕见该文 **§53**。
> **EE-1** 归属业务侧（执行方提供 CLI/产物支持）；**EE-2** 归属执行方、**1.0 前应完成**（不新增门槛数）。

### 修复（Fixed）——EE-2：CC-2 涨跌幅校验**口径标定**（OBS-3 收官）

**问题**：批 9 接线 ST 5% 后暴露"边界未标定"——真实主库十年仍有 **52~1024 行** 越界检出，
致 `data.quality.strict=true` 在真实长区间不可用。

**四轮归因 + 规则溯源（引交易所规则 + 生效日期，全部可复核）**，落为 **6 条子口径**：

| # | 子口径 | 依据（可核） | 效果 |
|---|---|---|---|
| 1 | **ST 5% 时点化**：主板 ST 5% 仅适用 **< 2026-07-06**；其后与普通股一致（10%）；非主板维持 20%/30% | 沪深《交易规则（2026 年修订）》**2026-04-24 发布 / 2026-07-06 施行**；证监会上海监管局 / 证券时报公告 | 消除 2026-07/08/09 的 ST 假阳性 |
| 2 | **退市整理期**：窗口 = 摘牌日前 **15 个交易日**（`≥2021-01-01`；此前 30）；窗口内上限 = 板块幅度；**首日不设涨跌幅**（豁免） | 深交所《交易规则》第 4.5.6 条 / 交易所投教问答；2020-12-31 退市新规由 30 → 15 | 消除整理期残余（含首日 −80% 类） |
| 3 | **价格档位容差**：`tol = max(0.5%, 1 档/pre_close)`（`_TICK=0.01` 元） | 涨停价 = 前收×(1±幅度) **四舍五入到分** ⇒ 低价股实际可涨略超名义上限（1.80 元股 = 0.56%） | 消除 5.56% 类轻微越界（7,114 行受影响） |
| 4 | **前一交易日无行情 ⇒ 不判**（不可比） | 原则性口径：覆盖停牌/复牌、新挂牌、数据缺口；比依赖 `suspend_d` 记录更稳健 | 31,145 行豁免（含 NEEQ 长期停牌） |
| 5 | **`*.BJ` 段整体口径外（明示豁免）** | 新三板机制多样（基础层集合竞价 ±50% / 做市·协议转让无限制）+ 北交所开市首日不限 + NEEQ `pre_close` 接续异常；**主库缺层级/交易方式/停牌标记字段** ⇒ 工具侧无法标定。**降级说明**：该段涨跌幅真实性不在 CC-2 覆盖内（待数据源补字段后收窄） | 消除 1,977 + 6 行残余（残余 2005→824→219→155→64→**0**） |
| 6 | **IPO 锚点 fallback**：`list_date` 缺失 ⇒ 以**首个行情日**为锚（窗口同 7 交易日） | 注册制上市前 5 交易日无涨跌幅；样例 688826.SH@20260818 首日 +516%（`list_date` 缺失致豁免未触发） | 22,452 行受益 |

**同时修一处自引入缺陷**：IPO 锚点分支曾把"`list_date` 存在但早于窗口起点"（老股）也送入 fallback
⇒ 老股被整体豁免 7 天（正是原守卫所防）；已改为**老股绝不豁免**（回归守卫：`test_ipo_anchor_window_expires`）。

**披露（铁律新 16）**：`data_quality_report.json` 新增 `extra.clauses`（6 条子口径 + 生效日期 +
豁免计数 + **降级说明**）——`st_source` 的等价口径标识；计数含
`exempt_delisting_first_rows` / `exempt_missing_prev_rows` / `exempt_neeq_period_rows` /
`exempt_ipo_anchor_fallback_rows` / `tick_tolerance_rows`（全部取真值）。

**实测（全市场十年，5819 标的 / 1080 万行）**：CC-2 **`ok=True`、findings=0**（标定前 2,005 行）
⇒ **CC-2 口径已标定、不再产生未标定越界**（原"暗角"消除）。
> ⚠ **精确边界（轮次十一 §54.4 观察 #3 采纳）**：本批**只标定了 CC-2**——全市场十年
> **CC-3（1）/ CC-4（2）/ CC-5（1）仍有真实 findings ⇒ `ok=False`** ⇒ **不等于"整份质检可开
> `strict`"**；启用前须逐类定性（真异常 vs 口径缺口），归**数据治理议题（OBS-6）**。

### 新增（Added）

- `tests/unit/test_data_quality_ee2.py`（**13 例**）：六条子口径**双向**断言（不误报 + 必报）
  + 时点分支（2026-07-06 前后）+ 档位容差（低价/高价对照）+ 前日无行情 + BJ 段 + IPO 锚点窗口有界。
- **质检机检第 ⑦ 项**（`tools/check_data_quality.py`）：**退市整理期双向对照**——临时造窗口后，
  首日 −80% **必须不报**、非首日 −60% **必须报**（任一侧不成立即 exit 1）。

### 实测摘要（可复核，按铁律新 12）

```
$ ruff check .                          → All checks passed!
$ python -m pytest -q                   → 869 passed（+14）
$ python tools/check.py                 → bt check: 9/9 通过
$ python tools/check_data_quality.py    → PASS（正样本 + ①–⑦ 负向对照，含整理期双向）
$ python tools/check_rules_four_way.py  → PASS（数值四方 + Z-6 语义锚点）
$ lint-imports                          → Contracts: 10 kept, 0 broken
【变异测试】打残 ST 时点分支 → 对应单测 exit 1；打残整理期首日豁免 → 机检 exit 1（还原后双绿）
【全市场十年】CC-2 findings 0（标定前 2,005 行；归因链 2005→824→219→155→64→0）
版本 0.5.14 → 0.5.15
```

### EE-1（业务侧）：Z-6 新旧对照（执行方提供产物）

同区间（2023-01-01~2026-09-23）/ 同基准（000300.SH）/ 同参数（`top_k=10`）重跑，真入口 `persist=True`：

| 指标 | 旧（§23.2，**Z-6 前**） | 新（**Z-6 口径**） |
|---|---|---|
| 总收益 | +23.62% | **+23.38%** |
| 年化 / Sharpe / 最大回撤 | 6.10% / 0.650 / −12.77% | **6.04% / 0.6446 / −12.96%** |
| 成交笔数 | 447 | **441** |
| 基准区间收益 | +16.19% | ~~+19.09%~~ ⇒ **+16.19%（勘误，见下）** |
| 超额 / IR | +7.44% / 0.0548 | ~~+4.30% / −0.0365~~ ⇒ **+7.19% / 0.0504（勘误，见下）** |

> ### ⚠ **勘误（轮次十一 §54.3 → 批 11 = EE-3 修正，v0.5.16；19 号 §55.1）**
> 上表"新（Z-6 口径）"的**基准 / 超额 / IR 三格不成立**：`+19.09% / +4.30% / −0.0365` 系
> `tools/run_v77_robustness.py::_bench_series` 的**日期比较未规范化**（紧凑 `20260923` vs
> 连字符 `2026-09-23` 的字典序）导致**基准终点被静默截到 `2025-12-31`**（**P1-NEW-9**）。
> **正确值（引擎 `metrics.json` 与赤潮独立复算「双路径」逐位一致）**：
> **基准 +16.19% / 超额 +7.19% / IR 0.0504**（`run_id 20260929_203022_562fd0`，端点 `aligned=True`）。
> ⇒ **"IR 转负 / 旧结论不再成立"不成立**；正确判定：**Z-6 净影响极小（超额 −0.25pp / IR −0.0044）、
> IR 仍为正**；但**性质不变**（IR≈0.05 ⇒ "v7.7 有效"仍站不住）。修复与回归见 **v0.5.16**。

⇒ **两点结论**：① **Z-6 对策略自身指标影响极小**（−0.24pp 收益 / −6 笔成交 / 超额 −0.25pp / IR −0.0044）；
② **旧数并未"失效"，仅微降**（+7.44%/0.0548 → +7.19%/0.0504）；**"v7.7 有效"依旧不成立**
（`IR≈0.05` ⇒ 超额波动 ≈ 超额 20 倍——与旧口径**同结论、理由不同**）。

## [0.5.14] — 2026-09-29

**发布主题：关闭项清零 —— Z-6/X-1 入闸确认期（规则源澄清 + 策略对齐）+ Y-3 技能注册
+ 冷备机制 + 托管准备 + v7.7 多区间稳健性首轮**

> 依据：19 号 §26.5 / §22.6（Z-6、X-1）· §20.7-5（Y-3）· §37.4.2 / §38.3⑥ / §39.3（冷备、托管）·
> §23.2 / §38.7.3（多区间稳健性）。执行留痕见该文 **§50**。**老大令：完成所有关闭项。**

### 变更（Changed）——Z-6 / X-1：`L0-LIQ` 入闸确认期落地（**策略语义变更**）

- **规则源先澄清**（`赤潮/rules/single-source.md` L0-LIQ 行 + `AGENTS-template.md`）：
  「原始 CRISIS 解除后须**连续 3 个交易日**「非 CRISIS 且非 WATCH」方进入 RECOVERY；
  **确认期内（含被 CRISIS/WATCH 打断后重新计时）状态归属 = CRISIS（仓位 0、禁止新增风险）**」
  —— 治 Y-1「确认期状态归属未定义」的歧义（19 号 §26.5）。
- **btf 对齐**（`btf/data/liq.py`）：状态机由"CRISIS 次日即 RECOVERY"改为
  **单步递推两段窗口**（记忆 = `phase` / `phase_days`）：
  | 原始判定 `raw` | 机器相位 | 最终状态 `status` |
  |---|---|---|
  | CRISIS | → CONFIRM:0 | **CRISIS** |
  | WATCH ∧ phase=IDLE | IDLE | WATCH（普通） |
  | WATCH ∧ phase∈{CONFIRM,RECOVERY} | → CONFIRM:0 | **CRISIS**（打断，重新计时） |
  | NORMAL ∧ phase=CONFIRM ∧ days+1 < 3 | CONFIRM:days+1 | **CRISIS（确认期，0 成）** |
  | NORMAL ∧ phase=CONFIRM ∧ days+1 ≥ 3 | → RECOVERY:0 | RECOVERY（第 3 个平静日入闸） |
  | NORMAL ∧ phase=RECOVERY ∧ days+1 < 3 | RECOVERY:days+1 | RECOVERY（≤1 成） |
  | NORMAL ∧ phase=RECOVERY ∧ days+1 ≥ 3 | → IDLE:0 | NORMAL |
  ⇒ 序列 `CRISIS → CRISIS×2（确认期）→ RECOVERY×3 → NORMAL`，**仓位上限单调恢复**
  （0 → 1 成 → 5 成）；**危机后不再直接回 5 成上限**（19 号 §21.3 的裁决点）。
- **API**：`state_of(..., prev=<前一日 LiqState>)`（原 `prev_statuses` 历史序列 →
  单步递推，O(1)）；`LiqState` 新增 `raw`（原始判定）/ `phase` / `phase_days`（机器记忆）
  ——**双字段披露**：确认期内 `raw=NORMAL/WATCH` 而 `status=CRISIS`，可核、不可混淆。
- **实测（904 日窗口）**：`NORMAL 845 | CRISIS 24 | RECOVERY 21 | WATCH 14`；
  **确认期日 15**；RECOVERY 段 7 / 最长 3 日；平均仓位上限 **47.28%**
  （X-1 修复后为 48.24%，修复前 33.90% —— 确认期按 0 成的保守化 −0.96pp）。
- **四处守卫同步更新**：四方机检（`tools/check_rules_four_way.py`）新增 Z-6 语义锚点
  （规则源须写明"确认期归属 = CRISIS"；btf 须有 `days + 1 < confirm_days` 与 `phase_days`；
  并保留 X-1 自我维持回归守卫）；系统级分布断言新增**结构性**守卫（存在确认期日 +
  每个 RECOVERY 段前恰有 ≥3 日 CRISIS）；单测序列推进（含 WATCH 打断、久远危机不误触发）；
  fixture 级变异样本（`LIQ_PY_NO_CONFIRM` / `LIQ_PY_CONFIRM_5` 必须被抓出）。

### 新增（Added）

- **`tools/cold_backup.py`（跨版本源码冷备）**：一条命令产出可独立校验的冷备包
  （`btf-src/` + `MANIFEST.json` + `SHA256SUMS.txt` + `COLD_BACKUP.md`），
  `--verify <目录>` 逐文件复算 sha256；**fail-closed**（不覆盖、自校验失败即非零）；
  配套 `tests/unit/test_cold_backup.py` **7 例**（含变异测试：篡改/缺失必被检出）。
- **`tools/run_v77_robustness.py`（多区间稳健性矩阵）**：5 区间 × 2 参数 × 双基准，
  真入口 `persist=True` 逐格落盘 + 显式超额/IR（日频 `mean/std×√252`），产物 JSON 可复核。
- **`btf/.gitignore`**：v1.0 私有托管准备（数据/产物/缓存/草稿不入库；
  **插件基线留痕入库**——它是非 git 期"核心 diff 为零"的证据链）。

### 实测摘要（可复核，按铁律新 12）

```
$ ruff check .                          → All checks passed!
$ python tools/check_rules_four_way.py  → PASS（数值四方 + 状态机语义锚点，含 Z-6）
$ python -m pytest tests/unit tests/integration tests/system tests/regression -q
                                        → 全绿（exit 0，100%）
$ python -m pytest tests/perf/test_liq_precompute.py -q -s
                                        → LIQ 十年预计算 4.42s（软目标 5s 内；2430 日）
$ tools/run_v77_robustness.py           → 10 格完成 / 0 失败（5 区间 × top_k{10,5}）
   全区间 2016-01~2026-09：+73.54%（HS300 +33.46%）| 超额 +40.08pp | **IR 0.094**
   P3 熊市震荡（2022-2024）：+4.88% | 超额 +35.11pp | IR **1.508**
   P2 结构牛（2019-2021）：+57.77% | 超额 **−17.72pp** | IR −0.632
版本 0.5.13 → 0.5.14
```

### 关闭项状态（截至本版）

| 项 | 结果 |
|---|---|
| **Z-6 / X-1**（规则源澄清 + btf 入闸对齐） | ✅ **关闭**（规则源已写明；btf 已对齐；四处守卫 + 变异样本） |
| **Y-3**（`btf-code-audit` 技能注册） | ✅ **关闭**（注册到 `赤潮/.agents/skills/`（平台位）+ `D:\量化策略\.codebuddy\skills\`；frontmatter YAML 解析通过） |
| **跨版本源码冷备** | ✅ **机制落地并已实跑**（`tools/cold_backup.py`；v0.5.14 冷备 + 自校验通过） |
| **v1.0 私有托管** | ✅ **准备就绪**（`.gitignore` + 定版日三步清单）；**执行**按裁决五-1 定于 v1.0 定版日 |
| **多区间稳健性** | ◐ **首轮证据已产出**（10 格 × 双基准，见上）；结论"区间依赖 + 参数不稳健" ⇒ 按 §38.7.3 作**体系恒演进项**持续 |

## [0.5.13] — 2026-09-29

**发布主题：EX-4 完全体 —— IO 单一入口（"只有 `data.core` 触 IO"），R4 剩余清零**

> 依据：19 号 §9.2（EX-4 原始条文）/ §47.4 余项 ②（"**EX-4 完全体**：需给 `core` 增通用
> 访问器 + 多模块迁移 + 性能复验"）；执行留痕见该文 **§49**。**老大令：开工 EX-4 完全体。**

### 变更（Changed）

- **`data` 域七模块的 20 处直连 IO 全部收拢到 `core`**（原各自 `pq.read_table` /
  `from btf.data import parquet_reader as pr`）：
  | 模块 | 迁移前 | 迁移后（经 `YearTableStore`） |
  |---|---|---|
  | `data/version.py` | `pq.ParquetFile` footer + `pq.read_schema` + `pq.read_table` + 自建布局解析 | `file_metas` / `file_stems` / `schema_names` / `read_file` |
  | `data/state.py` | 7 处 `pq.read_table`（stk_limit/suspend_d/namechange/stock_basic/daily/etf_limit/fund_daily） | `read_file(table, year=…)` / `file_stems` |
  | `data/quality.py` | 5 处 `pq.read_table`（stock_basic/trade_cal/dividend/suspend_d） | `read_file` |
  | `data/feed.py` | 6 处 `parquet_reader.{peek_columns,read_full,read_codes,iter_day_slices}` | `core` 同名方法 |
  | `data/index_universe.py` | `pq.read_table("index_weight")` | `read_file("index_weight")` |
  | `data/liq.py` / `data/fundamentals.py` | `pr.read_range` | `_store(root).read_range` |
- **`parquet_reader` 增 4 个公共 API**（磁盘知识的唯一实现处）：`table_files` /
  `file_stems` / `read_file`（缺失 → None）/ `schema_names` / `file_metas`
  （`FileMeta` 数据类：stem / size / mtime_ns / num_rows / num_row_groups /
  行组列统计 min-max **原始值**——指纹侧按 `repr` 格式化，与 BB-1 **逐字节一致**）。
- **`core.YearTableStore` 增 9 个出口**：`read_full` / `read_range` / `peek_columns` /
  `read_codes` / `iter_day_slices` / `read_file` / `schema_names` / `file_stems` /
  `file_metas`（**零新增缓存**——语义与直呼 reader 逐位一致，无性能特征变化）。
- **缺失语义保持**：`read_file` → `None`、`schema_names` → `[]`、`file_metas` → `[]`
  （列表语义**容忍缺失**）；而**读路径严格性不削弱**（目录缺失仍 `ReaderError`，
  防静默漏读）——两者边界由 `test_core_io_surface.py` 双向锁定。
- **连带收紧（登记）**：`quality._calendar_days` 移除"非登记布局兜底"
  （`root/trade_cal/trade_cal.parquet`，无测试/无部署依据）；布局唯一真源 =
  `tables_meta`（`metadata/trade_cal.parquet`），缺失仍 **fail-closed** 显式报错。

### 新增（Added）

- **`tools/check_io_boundary.py`（`bt check` 第 9 项）**：AST 扫描 `btf/**/*.py`——
  ① `import pyarrow.parquet` 只允许出现在 `data/parquet_reader.py` 与 `data/core.py`；
  ② `from btf.data.parquet_reader import …` 只允许 `core.py`；
  ③ `pq.read_table/read_schema/ParquetFile/write_table` 调用点同样受限。
  **变异性自检**（`--self-test`）：3 例注入违例必检出 + 4 例合规样本不误报；
  另做**变异测试**：把 `state.py` 改回直连 → 机检 `exit 1`。
- **契约新 9 强化（零豁免）**：`source_modules` 由 4 个上层包扩为**整个 `btf`**，
  豁免仅剩 `btf.data.core -> btf.data.parquet_reader` 一条
  （原 3 处近端豁免随迁移删除）→ `Contracts: 10 kept, 0 broken`。
- **`tests/unit/test_core_io_surface.py`（11 例）**：语义等值（`read_range`/`read_full`/
  `read_codes`/`iter_day_slices`/`peek_columns` 与 reader **逐行等价**）+ 缺失语义
  （None / [] / metadata 布局）+ 元数据档同源 + 读路径严格性回归。

### 实测摘要（可复核，按铁律新 12）

```
$ ruff check .                          → All checks passed!
$ python -m pytest tests/unit tests/integration tests/system tests/regression -q
                                        → 全绿（exit 0，100%）
$ python -m pytest --collect-only       → 843 tests collected（+11）
$ python tools/check.py                 → bt check: 9/9 通过（新增第 9 项=IO 单一入口）
$ python tools/check_io_boundary.py     → [ok] 80 文件扫描；仅 core + parquet_reader 可触磁盘
$ python tools/check_io_boundary.py --self-test
                                        → [self-test:ok] 注入违例 3 例全检出；合规 4 例全通过
$ lint-imports                          → Contracts: 10 kept, 0 broken（新 9 = 整个 btf，1 条豁免）
$ tools/check_plugin_boundary.py --update --reason "EX-4 完全体（v0.5.13）…"
   → 核心 9 文件留痕（data/{core,feed,fundamentals,index_universe,liq,parquet_reader,quality,state,version}.py）
版本 0.5.12 → 0.5.13
```

### 性能复验（实施要求第 3 条）

- **结构层零新增缓存**：委托方法皆纯转发；原 `load`/`read_year_where` 年表缓存语义不动
  ⇒ 与迁移前**同一 IO 次数**。
- **A/B 直接实测**（`core.iter_day_slices` vs `parquet_reader.iter_day_slices`，20 标的 ×
  十年 `daily` 全量流式，交替两轮）：**core 0.458 / 0.467s vs reader 0.463s**
  ⇒ **无可测开销**（首轮 0.949s 为页缓存冷启动）。
- **基准隔离复验**：B1 **8.86s**（软 10s 内）· B2 **16.95s**（硬 20s 内，超软目标）·
  B3 十年 **43.3s**（预算 60s，余量 1.39x）· **B4 1670s**（预算 1800s，余量 **1.08x**）·
  LIQ **4.90s**（软目标内）。数字与归因入 `tests/perf/BASELINE.md` §六·B。
- **OBS-5（新登记）**：B4 **并发**其它 pytest 时测得 22.5s → 外推 1871s（0.96x）**假失败**；
  **隔离单跑** 20.0s → 1670s（1.08x）通过 ⇒ **跑 L5 前须确认无并发 `python` 进程**；
  **预算 1800s（09 §14.5 需求）不因机器态放宽**。
- **OBS-4 更新**：B4 余量 1.03x → **1.08x**（仍显著偏高，机器态；未改门槛）。

### 效果（EX-4 条款逐条对照）

| §9.2 原条款 | 落地证据 |
|---|---|
| "**全系统唯一允许'认识磁盘'的公共模块 = `data.core`**" | `pyarrow.parquet` 在 `btf/` 内仅 `core.py` + `parquet_reader.py`（后者是 core 的实现细节）；其余 78 文件零触达（机检） |
| "业务模块不直呼 `parquet_reader`" | import-linter 新 9（整个 btf，1 条豁免）+ `bt check` 第 9 项 |
| "布局解析单源" | `tables_meta` + `parquet_reader.table_files`；`version.py` 自建 `_table_files` **已删除** |
| "迁移须附语义等值验证" | `test_core_io_surface.py` 5 项逐行等价断言 |

## [0.5.12] — 2026-09-29

**发布主题：R4 剩余（第三刀 / 余项 ①）—— `feed → state` 例外边**反转**：契约新 8 达成零豁免**

> 依据：19 号 §47.4 余项 ①（"`feed → state` 例外边反转（契约新 8 唯一已知例外）"）；执行留痕见该文 **§48**。
> 性质：**依赖反转**（DIP）——不改任何对外行为，消除 data 域内最后一条豁免边。

### 变更（Changed）

- **`TushareParquetFeed` / `MixedDailyFeed` 不再 import `btf.data.state`**（原为新 8 的
  唯一豁免边）：状态面板改为**构造期注入**——
  - `TushareParquetFeed(root, *, state_provider=None)` / `MixedDailyFeed(root, *, state_provider=None)`；
  - `attach_state_provider(provider)`（幂等；`MixedDailyFeed` 透传至股票侧原型，**同一对象**不各建一份）；
  - **`BTFRuntime.build`（装配根）自动注入** `StateSynthesizer(feed.root)`——经 `BTFRuntime`
    运行的一切路径行为不变；
  - **未注入而取状态 → `RuntimeError`（显式报错，不静默降级）**：状态面板是撮合
    （涨跌停/停牌/ST/退市）与风控的**唯一可交易性依据**，缺它必须失败；报错文案给出
    可执行修复指引（`state_provider=StateSynthesizer(root)`）。
- **契约新 8 豁免行移除**：`pyproject.toml` 删去 `"btf.data.feed -> btf.data.state"`
  → `lint-imports` 仍 **10 kept, 0 broken**（新 8 现为**零豁免**：data 域六模块互不依赖，
  全由机器守卫）。
- **测试站点显式注入**（5 文件）：B2/B3 基准、`test_determinism`、奇点复跑（4 处）、
  行动等价（G9）——其中 **B3 的 `universe()` 夹具保持免注入**，恰好反证"仅状态路径需要注入"。

### 新增（Added）

- **`tests/unit/test_state_injection.py`（7 例）**：① 未注入 → `states` / `trading_states`
  **必须**抛 `RuntimeError`（能触发即非伪防线）且文案可执行；② 注入可用 + 幂等覆盖；
  ③ `MixedDailyFeed` 透传**同一对象**（不各建一份）；④ 真合成器注入后端到端可用
  （`trading_states` 返回状态）。

### 实测摘要（可复核，按铁律新 12）

```
$ ruff check .                          → All checks passed!
$ python -m pytest tests/unit tests/integration tests/system tests/regression -q
                                        → 全绿（exit 0，100%）
$ python -m pytest tests/benchmark tests/perf -q
                                        → 全绿（15 项 100%；B2/B3 注入路径复验）
$ python -m pytest --collect-only       → 832 tests collected（+7）
$ python tools/check.py                 → bt check: 8/8 通过
$ lint-imports                          → Contracts: 10 kept, 0 broken（新 8 零豁免）
$ tools/check_plugin_boundary.py --update --reason "R4 余项①（v0.5.12）…"
   → 核心 72 文件；本轮 2 文件留痕（btf/data/feed.py 4b2c7226→cceea62f；
     btf/runtime/_runtime.py f040d118→5b661855）
版本 0.5.11 → 0.5.12
```

### 未执行（R4 剩余仅 1 项）

- **EX-4 完全体**（"只有 `data.core` 触 IO"）：需给 `core` 增通用访问器 +
  `state/liq/fundamentals/quality/version/index_*` 逐个迁移 + 性能复验 ⇒ 单独立批。

## [0.5.11] — 2026-09-29

**发布主题：R4 剩余（第二刀）—— `RunOrchestrator` 下沉 + `ComponentAssembler` 类化（拆分四件套齐备）**

> 依据：19 号 §46.4（R4 余项 #1：`run()` 落盘编排未抽出）。**老大令：继续。**

### 变更（Changed）

- **`btf/runtime/run_orchestrator.py::RunOrchestrator`** —— `run()` 的**落盘编排整体下沉**
  （方法体逐字搬移）：① create_run 骨架 → ② 数据指纹（**仅落盘路径**）→ ③ CC-6 质检产物
  （含"为何没校验"）→ ④ 引擎执行 → ⑤ 产物落盘 → ⑥ 指标（单点 `compute_run_metrics`）+ manifest
  补全；**异常 → `mark_failed` 后抛出**。`BTFRuntime.run` 现为**一行委托**。
  产物常量 `DATA_QUALITY` 随编排落位（单一归属在该模块）。
- **`ComponentAssembler` 由函数升级为类**（持有显式依赖 `rt`）：`guard_benchmark` /
  `data_tables` / `run_data_quality` / `compute_data_version` 四方法；`DataQualityError`
  随之下沉并再导出。

**拆分四件套现状（19 号 §9.3 / R4）**：`ConfigResolver` ✅ · `UniverseResolver` ✅ ·
`ComponentAssembler` ✅ · `RunOrchestrator` ✅ —— `btf/runtime` 包共 6 文件，门面只留状态与委托。

### 实测摘要（可复核，按铁律新 12）

```
$ ruff check .                          → All checks passed!
$ python -m pytest -q                   → 825 passed（全绿）
$ lint-imports                          → Contracts: 10 kept, 0 broken
$ tools/check_plugin_boundary.py        → [ok]（核心 71 文件；本轮 3 文件刷新留痕）
基准层（本机当前态，**全在硬门槛内**）：
  B1 8.75s / B2 16.1–17.7s / B3 42.1s / B4 外推 1753s（余量 1.03x）/ LIQ 4.83s
  ⚠ 漂移登记（BASELINE.md §六·A）：B2/B3/B4 一致偏高，改动均在热循环之外 ⇒ 判为机器态
    漂移（D-8/D-13 族），未改门槛；**OBS-4**：B4 余量降至 1.03x，待机器干净态复测/复归因
版本 0.5.10 → 0.5.11
```

### 未执行（R4 剩余，明确结转）

1. **`feed → state` 例外边反转**（契约新 8 唯一已知例外）：需把状态面板改为**构造期注入**
   （牵动 DataFeed 协议与 registry 工厂签名）⇒ 协议层变更，单独立批。
2. **EX-4 完全体**（"只有 `data.core` 触 IO"）：需给 `core` 增通用访问器 +
   `state/liq/fundamentals/quality/version/index_*` 逐个迁移 + 性能复验 ⇒ 单独立批。

## [0.5.10] — 2026-09-29

**发布主题：R4 剩余（第一刀）——《BTFRuntime` 拆分 + 重复编排收敛；并修 ST 板块口径（接线后实测暴露）**

> 依据：19 号 §41.5 / §44.4（R4 剩余：`BTFRuntime` 拆分、`compute_all` 三处链、日期 helper 三份、`feed→state` 例外边、EX-4 完全体）。
> 本批按**增量子批**推进（动核心装配根，一次做完风险过高）；已完成 3 项，**余 2 项明确结转**（见文末「未执行」）。

### 变更（Changed）—— `BTFRuntime` 拆分

- **`btf.runtime` 由单文件升为包**（对外 API **不变**：`BTFRuntime` / `make_store` / `fee_segments` /
  `DataQualityError` / `SHORT_PERIOD_TRADING_DAYS`），职责下沉到协作模块：

| 模块 | 职责（下沉内容） |
|---|---|
| `runtime/config_resolver.py` | **ConfigResolver**：四层合并 + Schema 校验（原 `load_config` 逻辑） |
| `runtime/universe_resolver.py` | **UniverseResolver**：explicit / all（期间并集 + 回退披露）/ index（月末快照 PIT）—— 三口径与两条披露路径整体搬出，只依赖 `feed` 与披露收集器 |
| `runtime/component_assembler.py` | **ComponentAssembler**：装配期门与披露（基准守卫 / 表集 / 数据不变量校验 CC 门 / 数据指纹）+ `DataQualityError` 随之下沉并再导出 |
| `runtime/_runtime.py` | `BTFRuntime` **门面**：状态 + 一行委托（不再是长方法堆叠） |

- 门面类方法改为**委托**（`_resolve_instruments` / `_guard_benchmark` / `_data_tables` /
  `_run_data_quality` / `_compute_data_version` / `load_config`）——**方法体逐字搬移**（脚本搬移 + 824 测试验证语义等价）。

### 新增 / 收敛（Added）

- **`btf/analytics/run_metrics.py::compute_run_metrics`** —— `compute_all` **三处调用链收敛为单点**
  （原 `runtime.run()` 落盘 / `runtime.verify_run()` 复核 / `optimize.grid` 组合各写一遍）：
  统一 `(snapshots, fills, config, analyzer_names, resolver, extras)`；`extras` 用于基准相对指标
  （网格场景不传 ⇒ 与既有结果逐位一致）。
- **`btf/domain/types.py`** —— 日期互转**单一真源**：新增 `parse_trading_date()` / `ymd_of()`
  （原 `runtime._ymd` / `experiment.store._ymd` / `data.state._ymd_to_date` **三份**各写一份 → 全部改用真源；
  脏日期仍按缺值处理，语义不变）。

### 修复（Fixed）—— ST 板块口径（**接线后实测暴露**）

- `quality._check_cc2`：**ST 5% 上限只适用主板**——创业板/科创板（300/301/688/689）与北交所
  （4xx/8xx/920）的 ST 股涨跌幅限制**不变**（20% / 30%）。批 9 接线前该分支从不生效故未暴露；
  生效后按"ST ⇒ 一律 5%"在 v77 十年区间产生 **4,393 行假阳性**（样例 `300089.SZ@20230103`）→
  修正后 **1,024 行**（**消除 3,369 假阳性**）。新增反向对照用例 `test_cc2_st_limit_is_board_aware`
  （创业板 ST +8% 不得报；主板 ST +8% 必须报）。

### 观察（OBS-3，登记待办）

- 修正后 v77 十年区间仍有 **1,024 行** CC-2 残留（样例 `600260.SH@20230113`）——疑为
  **退市整理期**（涨跌幅 10%、首日不限）与 ST 摘帽边界未标定所致；**默认路径为告警 + 产物 + 披露**
  （不阻断、不产生假阻断），`strict=true` 下会拒运行 ⇒ 需下一子批标定后再启用 strict。

### 实测摘要（可复核，按铁律新 12）

```
$ ruff check .                          → All checks passed!
$ python -m pytest -q                   → 825 passed（+1：ST 板块口径反向对照）
$ python tools/check.py                 → bt check: 8/8 通过
$ lint-imports                          → Contracts: 10 kept, 0 broken
$ python tools/check_data_quality.py    → PASS（5/5 + ⑥ ST 分支双向）
$ tools/check_plugin_boundary.py        → [ok]（**btf/runtime 由单文件升包 ⇒ 整体纳入 CORE 跟踪**；
                                           核心 71 文件，刷新留痕）
版本 0.5.9 → 0.5.10（btf/_version.py 单一真源）
```

### 未执行（R4 剩余，明确结转下一子批）

1. **RunOrchestrator**：`run()` 的落盘编排（create_run → 引擎 → 产物 → 指标 → 状态补全）
   尚未从门面抽出（与 ComponentAssembler 一并做成持有显式依赖的类更稳妥）。
2. **`feed → state` 例外边反转**（契约新 8 的唯一已知例外）：需把状态面板改为**构造期注入**
   （runtime 侧装配 `StateSynthesizer` 并注入 feed），牵动 DataFeed 协议与 registry 工厂签名。
3. **EX-4 完全体**（"只有 `data.core` 触 IO"）：需给 `core` 增加通用访问器并把
   `state/liq/fundamentals/quality/version/index_*` 逐个迁移 + 性能复验——**非一次可安全完成**，建议单独立批。

## [0.5.9] — 2026-09-29

**发布主题：批 9（裁决七）—— CC-2 的 **ST 5% 上限**接线（P2-NEW-8）：把"已交付但有暗角"的数据不变量校验补成"真在跑"**

> 依据：`19-架构审查与重构方案-20260927.md` §43.3（轮次九独立验收发现 **P2-NEW-8**）+ **§44.1 派单 DD-1..DD-3**；执行留痕见该文 **§45**。
> 性质：**修正批**（不新增能力）；影响面为**漏检**（无假阳性、不误阻断），不污染既有结论。

### 修复（Fixed）

- **【DD-1｜P2-NEW-8 核心】CC-2 的 ST 上限未接线**：`runtime._run_data_quality` 原调
  `run_checks(...)` **不传 `st_symbols`** ⇒ CC-2 **恒按板块上限**（主板 10%）⇒
  **ST 股 5%~10% 的越界漏检**（功能未接线 + 所有调用均传空集 ⇒ 零覆盖）。
  现经新增 `StateSynthesizer.st_pairs()`（事件时间线 + 二分，PF-5 已有能力）取
  **区间内真实 ST 集合**并传入：
  - **日历同源**：`cal` 用公开的 `quality.calendar_days()`（与 `run_checks` 内部同口径，§44.2 边界 1）；
  - **抽样一致**：只在抽样后的标的子集内保留（§44.2 边界 2）；
  - **fail-closed**：ST 名单取不到（`namechange` 缺失/异常）→ `st_source="unavailable"`
    + **告警 + 装配说明披露**（不静默，铁律新 16）；`strict=true` 下与其余校验一致可硬失败；
  - **性能（§44.2 边界 4）**：实测 `st_pairs` **0.014–0.020s**（20 标的 × 1 年 / × 10 年），
    相对 `run_checks`（0.19s / 1.51s）可忽略；装配期只算一次。

### 新增（Added）

- `StateSynthesizer.st_pairs(start_ymd, end_ymd, symbols=None, *, cal)` → `set[(symbol, ymd)]`
- `StateSynthesizer.st_source()` → `"namechange"` | `"unavailable"`（**取不到须披露**）
- `btf.data.quality.calendar_days()`（公开；供调用方与校验**同源**取日历）
- 产物字段：`data_quality_report.json` 新增 **`st_source`** 与 **`st_pairs_count`**（新 16：可核、非占位）

### 测试（DD-2，铁律新 15）

- 单测 `test_cc2_st_branch_is_triggerable`：**+8% 双向对照**——传 ST → 检出；不传 → **不检出**
  （证明该分支有区分度，且旧行为即漏检面）。
- 集成（**真入口**）`TestCC2STBranch` 3 例：ST 股 +8% 在 `bt run` 装配期**真检出**；
  `st_source="namechange"` / `st_pairs_count>0`；产物携带 ST 字段；无 ST 场景字段仍有值。
- 机检 `tools/check_data_quality.py` 负向对照 **⑥ ST 分支**（+8% 双向）。
- **变异测试闭环（已做）**：把 `st_pairs` 打残为 `set()` → 机检 **exit 1**
  （`[FAIL] CC-2 ST 分支未检出（伪防线）`）+ **2 个测试失败** ⇒ 防线可被证伪。

### 订正（Changed）

- **【DD-3】`quality.py` 注释语义订正**：原写 "`None` → 按板块上限（**偏保守**：ST 股只会被放宽，
  不会造成假阳性）"——**与事实相反**：`None` 时会**漏检** ST 越界（false negative）。
  改为如实表述 + 明确"调用方**必须**传入真实 ST 集合"；`run_checks` docstring 与 CC-2 分支注释同步。

### 实测摘要（可复核，按铁律新 12）

```
$ ruff check .                          → All checks passed!
$ python -m pytest -q                   → 824 passed（+4：ST 分支单测 1 + 真入口集成 3）
$ python tools/check.py                 → bt check: 8/8 通过（第 8 项含负向对照 ⑥ ST 分支）
$ lint-imports                          → Contracts: 10 kept, 0 broken
$ python tools/check_rules_four_way.py  → PASS
$ python tools/check_data_quality.py    → PASS（5/5 + ⑥ ST 分支双向）
变异测试：打残 ST 分支 → 机检 exit 1 + 2 测试失败（可证伪 ✓；已还原）
ST 开销实测：st_pairs 0.014s（1 年）/ 0.020s（10 年）；st_source=namechange
版本 0.5.8 → 0.5.9（btf/_version.py 单一真源）
```

## [0.5.8] — 2026-09-28

**发布主题：批 8（CC-1..CC-6 数据不变量校验）+ 附录 D.3 结转批 + 担保-1 + 新契约 7–10 + R4 第一刀——代码侧结转清零**

> 依据：`19-架构审查与重构方案-20260927.md` §39.2（裁决六-1 立批 CC，P1 级）/ §38.7.1（一区 #7 技术债、#8 风控 e2e）/
> 附录 D.3；执行留痕见该文 **§41**。验收用语按铁律新 12（实测摘要）、新 15（防线可被证伪）、新 16（禁空洞披露）。

### 新增（Added）

- **【CC-1..CC-6】数据不变量校验（新能力，P1）**：`btf/data/quality.py` —— 五项校验全部
  **Arrow 向量化 + 区间化 + 标的谓词下推**（走 `data.core` 唯一入口）：
  - **CC-1** OHLC 自相矛盾（`high≥max(o,c)` / `low≤min(o,c)` / `high≥low` / `close>0` / `vol,amount≥0`）；
  - **CC-2** 涨跌幅边界（主板 10%／创业 `300·301·302` 20%／科创 `688·689` 20%／北交所 `4xx·8xx·920` 30%／ST 5%，
    并含**新股上市 7 交易日**与**复牌首日**豁免）；
  - **CC-3** 日历一致性（行情日期 ⊆ `trade_cal` + **全日停牌日不得有行情行**——口径实测 `S` 型且无 `timing`）；
  - **CC-4** 复权因子（正性 + 除权事件 `(symbol, ex_date)` 有因子行 + 个股序列单调不减，容差 `max(1e-3, 1e-4·v)`）；
  - **CC-5** 量价互斥（有行情行 ⇒ `vol/amount>0`；收盘价落在当日 `[down, up]` 内）。
  - **装配期门**：`data.quality{enabled, strict, max_symbols}`（schema + DEFAULTS）——`strict=true` →
    `DataQualityError`（fail-closed）；缺省「告警 + 记入产物 + 报告披露」；**校验自身失败永远硬失败**（不静默跳过）。
  - **CC-6 工程化**：产物 `data_quality_report.json`（run 目录）+ **`bt check` 第 8/8 项**（正样本 +
    **负向对照 5/5 注入缺陷必检**）+ 强制章节「假设与披露」披露 + `checked_rows` 逐规则 > 0。
  - **实测**：20 标的 × 十年 **PASS 2.0s**（初版 45.6s → 谓词下推）；全市场一年 10.4s，残余
    **22 行 = 退市整理期** + **5 行 = 退市股因子断层（真异常，样例 600069.SH 6.415→0.6604）**——**保留为发现项**。
  - 测试 **22 项**（17 单测：逐条注入必红 + 豁免反向对照；5 集成：门/产物/抽样披露/渲染级）。

### 变更（Changed）

- **【IO-5/IO-6/PF-5/PF-4】** `state._closes_on` 逐日整表读 → **年表 + 按日索引**；`feed.universe()` →
  **实例级原始行缓存**；`_st_codes_on` → **事件时间线 + 二分**（O(log n)，语义逐位一致）；`_evict_year_cache`
  → **年切换触发**（`_maybe_evict` + 计数留痕）。
- **【PF-9】`btf/domain/cache.py::BoundedDict`（LRU + 容量 + `stats()`）** 替换 **5 处无界缓存**
  （`domain/types` 20k；`data/feed`·`data/adjust`·`engine/loop`·`strategy/context` 各 8k）。
- **【IO-3/P1-8】`dividend` 区间化**：全 37 年读 → **回看 5 年超集 + 行内过滤 + 进程内 memo**；
  **超集等价性单测：33,451 条逐条一致**；滞后守卫全表重测（实测最长 5 年，> 即红）。
- **【IO-7/P1-4】`optimize` 子进程 `initializer` 预热**（跨组合共享 `corporate_actions` memo + 模块装载）：
  **B4 11.1s → 7.0s**（外推 965s → **581s**，余量 **3.10x**）；进程池 vs 单跑逐位一致仍绿。
- **【IO-9】`pq.read_table(memory_map=True)`**——**仅主库路径**（Windows 下映射文件不可删，测试夹具走普通读）。
- **【IO-10/P2-1a】`atomic_write_text` 补 `fsync`**（文件 + 目录项；Windows 目录不可 open 时容错）——
  0.X 唯一兜底即"落盘可信"。
- **【PF-7/P2-2】`events_log` 预算判断前置** + 尾部 ring 存**事件对象**（延迟编码）+ 阈值 flush +
  `n_serialized`（采样模式不再"省盘不省 CPU"；**落盘行集逐行不变**）。
- **【P2-1b】`RunManifest.from_dict` 补主版本校验**（`runresult.vN` 不兼容即**拒绝解析**，不再静默错值）。
- **【P3-1】** `engine.loop` 死代码清理（`| {o.symbol for o in pending}`，此刻 `pending=[]`）——语义逐位一致。
- **【PF-10/P3-3】`registry.catalog` 按扩展点分组惰性装载**；`points()` 改静态清单（不再触发插件装载）。
- **【R5-4】`bt run --verbose`**：`runtime.step_timings` 分步耗时（装配·前置/质检/组件 ‖ 运行·指纹/引擎/落盘）
  + 数据质检与数据指纹摘要。
- **【担保-1 · 一区 #8】风控 e2e 3 → 9 例**（真实链、零逃生开关）：现金约束拒单（实测触发口径 `weight=1.2`；
  `weight=0.9` 或"现金极小"都会被再平衡器先缩量为 0 手——已留痕）/ ST·停牌·退市三态（状态注入）/ `reject_st`
  正反对照 / 规则顺序归因 / 未登记规则名装配期失败。
- **【新契约 7–10（R2-8）】import-linter 6 → 10 条**：新 7 `data.core` 纯度 / 新 8 data 域互不依赖 /
  新 9 上层不直接触 `parquet_reader` / 新 10 cli·api 只经门面 → **10 kept, 0 broken**
  （新 8 保留 1 处**已知例外** `feed → state`，R4 收敛时移除）。
- **【R4 第一刀 / P2-3】新增 `btf/app` 应用服务层**：**5 组重复收敛为单源**（环境过滤 / 类型推断 /
  run 主链路 / 报告装配 / dataset 编排）；layers 契约新增 `btf.app`；`api.py` 的"与 CLI 字节等价"
  改由**同一实现**保证（29 项 CLI/api 测试绿）。

### 修复（Fixed）

- **【D-12】`tools/check_domain_whitelist.py` 漏 `typing`**（pyproject 契约 3 注释明列，工具却未收录 →
  正常代码被误报；**声明 vs 实现不符第 13 处**）：补 `typing`；`collections` 由「仅 abc」放宽为整包
  （`BoundedDict` 需要 `OrderedDict`——标准库容器与 `dataclasses` 同级的中性依赖）。
- **【D-11】数据质检首版接线位置错**（置于 `instruments` 解析前 → 空标集静默退化为 0 行）：
  移至解析之后；由集成测试 `test_default_records_and_discloses` 抓出。

### 实测摘要（可复核，按铁律新 12）

```
$ ruff check .                          → All checks passed!
$ python -m pytest -q                   → 820 passed（+30）
$ python tools/check.py                 → bt check: 8/8 通过（第 8 项=数据不变量校验）
$ lint-imports                          → Contracts: 10 kept, 0 broken
$ python tools/check_data_quality.py    → PASS（正样本 + 负向对照 5/5）
$ python tools/check_rules_four_way.py  → PASS（数值四方 + 状态机语义锚点）

# 数据质检（主库实测）
20 标的 × 10 年：PASS 2.0s（CC-1..CC-5 全绿）
全市场 × 1 年：10.4s，残余 22 行（退市整理期）+ 5 行（退市股因子断层，真异常）

# 基准复验（隔离）
B4 7.0s（外推 581s，余量 3.10x）| IO-3 等价 33,451 条 | LIQ 4.95–4.97s | B1 9.22s

版本 0.5.7 → 0.5.8（btf/_version.py 单一真源）
```

## [0.5.7] — 2026-09-28

**发布主题：19 号轮次八（§33）+ 裁决四派单 —— 数据指纹去空洞（重路实算）+ 展示/告警/体验三项**

> 依据：`19-架构审查与重构方案-20260927.md` §33（四项能力验收）/ §34.3（**BB-1..BB-4**）/ **§39.4（BB-1「重路」释义与实施要点）**；
> 执行留痕见该文 **§40**。**BB-5**（铁律新 16 登记）由赤潮完成。
> **验收用语纪律（§6.3 新 12）**：以下为「命令 → 实测输出摘要」。

### 修复（Fixed）

- **【P2-NEW-7 / BB-1】`manifest.data_version` 空洞（第八种逃逸模式：空洞披露）**：
  原 `content_hash` 恒 `sha256:unknown`、`tables=[]`，而 `data/version.py::compute()`
  **零生产调用** ⇒ 报告「数据指纹」栏永为 unknown、数据更新后结论变化**无法归因**。
  **按裁决六-3 直接走「重路」（实算而非声明）**：
  - **接线点**：`run(persist=True)` **落盘路径**（`create_run` 之前）——**不是** `build()`（见下 D-7）；
  - **表集**：价格表按宇宙实际标的的 `daily_table_of` + `stock_basic`/`suspend_d`/`adj_factor`/`stk_limit`/`etf_limit`（配 `report.benchmark` 时加 `index_daily`）；
  - **区间化**：`compute(..., years=…)` 新增年过滤（只为区间涉及年份指纹）；
  - **档位**：`data.fingerprint_mode`：**`meta` 缺省**（Parquet 元数据：行数/行组统计/大小+mtime，毫秒级）／`fast`（键列抽样，10 年 6 表 ≈7.2s）／`full`（全列抽样，可检出数值级修订）；
  - **fail-closed**：计算失败即抛，**绝不**静默回落 `unknown`；内存 Feed 显式标注 `sha256:in-memory:Nsym`（非占位）。
  - **实测**：`content_hash = sha256:d43ed09d10a2bb07`／`tables` 6 表／`anchor_date = 20240630`（数据水位，裁剪到区间末）／`tables_detail` 逐表行数；报告「数据指纹」栏**渲染级可见**、全文无 `sha256:unknown`。

### 新增（Added）

- **【BB-3】`bt config-check --resolve-strategy`**：import + 类属性存在 + `StrategyBase` 子类检查
  （**默认关闭**，行为向后兼容）；类名拼错 → `exit 1` 精确到"无属性"，模块缺失 → 提示 `PYTHONPATH`。
- **【BB-4】极短区间告警**（OBS-1）：阈值单源 `runtime.SHORT_PERIOD_TRADING_DAYS = 20`；
  API 记 warning、CLI 打印 `[warn] …勿用于 optimize --objective 排序`，**不阻断**（exit 0）；≥20 交易日无告警。
- **【D-4】报告「复现说明」立为强制章节** + `DEFAULTS.report.sections` 默认**全章**：
  原默认表缺 `reproduce`/`appendix` 且过滤**只强制** `assumptions` ⇒ 真实 CLI 产物仅 4 章，
  **而「复现说明」正是承载 `metrics_digest` / 数据指纹 / 代码版本 / 种子的章节**。
  实测：`report.sections=[summary]` 时仍保留复现说明且指纹可见；默认产物 **6 章**。

### 变更（Changed）

- **【BB-2】终端 == 落盘**：`cli._fmt_metric`（`None`/NaN → `null`）——`bt run` 不再打印 Python `nan`
  （与 `metrics.json` 的 `null` 一致）。
- **【D-7】性能回归修复（BB-1 首版接线错误）**：指纹首版算在 `build()` ⇒ 网格搜索**每组合**各付一次
  （10 年 6 表 fast 档 **7.2s**）→ **B4 由 8.0s 恶化到 43.8s、外推 664s → 3647s**（超 1800s 预算）。
  改为**落盘路径** + 缺省 `meta` 档 + 进程内 memo ⇒ **B4 43.8s → 11.1–11.6s**（外推 926–965s，余量 1.9x）；
  单组合 inline **2.92s**（2430 日 / 509 成交）。不变量用例常驻：
  `test_fingerprint_only_on_persist_path` / `test_grid_path_pays_nothing`。
- **【D-8】墙钟门槛统一软/硬口径**（原仅 B2 有；治负载机假失败）：
  **B1 软 10s/硬 14s**、**LIQ 十年预计算 软 5s/硬 8s**（B2 已于 Z-4 为软 15s/硬 20s）。
  依据入 `tests/perf/BASELINE.md` §四/§六；**回归守卫交机器无关断言**（LIQ 语义等值 +
  `loads ≤ 40` 精确 IO + 分布锚点 RECOVERY 1.7%/最长 3 日 + B3 十年 <60s）。
  **归因留痕**：LIQ 3.7s→4.9s 经探针实测「带锁 4.94/4.78s vs 无锁 4.71s」⇒ X-9 锁开销仅 **2–4%**，
  非主因（属机器态/页缓存），故按口径处理而非改实现。
- **【D-9】插件边界基线补入 `btf/viz`**：原 `CORE` 列表遗漏报告层 ⇒ `viz/report.py` 多批改动
  **未被快照覆盖**（"核心 diff 为零"在报告层不可证）。本轮刷新按 AA-3 机制**两次留痕**（6 + 5 文件）。

### 实测摘要（可复核，按铁律新 12）

```
$ ruff check .                         → All checks passed!
$ python -m pytest -q                  → 789 passed（+18）
$ python tools/check.py                → bt check: 7/7 通过
$ python tools/check_rules_four_way.py → PASS（数值四方 + 状态机语义锚点）
$ tools/check_plugin_boundary.py --update --reason "批 7 …"（AA-3 留痕）
   → ① btf/{cli/main, config/loader, data/version, experiment/manifest, experiment/store, runtime}.py
   → ② btf/viz/{__init__, charts, contracts, render, report}.py（补入 viz）

# 数据指纹（真入口 bt run，1 标的 / 2024H1）
content_hash = sha256:d43ed09d10a2bb07 | tables = 6 表 | anchor_date = 20240630
报告「数据指纹」栏渲染真实值（渲染级断言）| 全文无 sha256:unknown
# 基准复验（隔离）
B4 11.1–11.6s（外推 926–965s，余量 1.9x）| 单组合 inline 2.92s
B1 9.22s（软 10s 内）| LIQ 4.95–4.97s（软 5s / 硬 8s）

版本 0.5.6 → 0.5.7（btf/_version.py 单一真源）
```

## [0.5.6] — 2026-09-28

**发布主题：19 号第七轮审查（§30）+ 裁决三派单 —— 伪防线转真断言（新 15）+ 计算链验证定性留痕 + 基线刷新留痕机制**

> 依据：`19-架构审查与重构方案-20260927.md` §30（第七轮独立验收）/ §31.3（**AA-1..AA-4**）；执行留痕见该文 **§32**。
> **AA-4**（铁律新 15 登记）已由赤潮完成；**Z-6**（Y-1 规则源澄清）归赤潮，本批未动。
> **验收用语纪律（§6.3 新 12）**：以下为「命令 → 实测输出摘要」。

### 修复（Fixed）

- **【P2-NEW-6 / AA-1】报告渲染机检第 6 项「恒真」（伪防线 → 真断言）**：
  判据 `"<script>alert" not in html` 写法正确，但合成 bundle 的 `title` **从不含 HTML 字符**
  ⇒ 断言**永不触发**（第七种逃逸模式：断言正确但 fixture 输入不含触发条件；轮次七实测
  "title 误加 `| safe`"这一 XSS 级真退化仍 6/6 PASS）。
  **修**：fixture 注入 `<script>alert(1)</script>` 探针（纯文本字段），第 6 项判据升级为**双向**
  （裸串**不存在** ∧ 必须**被转义**）；机检新增 **`--self-test`** 把变异性检查固化为可重跑自检。

### 新增（Added）

- **【AA-3】插件基线刷新留痕**（治 P3-NEW-2；非 git 下"核心 diff 为零"事后不可证）：
  `check_plugin_boundary.py` 新增 `--reason`；`--update` 把**变更文件 + 旧→新哈希 + 依据 + 时间**
  追加到 `tools/.plugin_baseline_history.jsonl` 并打印 **CHANGELOG 建议行**；无变更路径亦留提示。
  - **补记（retro）**：批 5（v0.5.5，09-28 16:42）那次基线刷新（机制建立前）→ 4 个核心文件
    `btf/runtime.py`、`btf/viz/report.py`、`btf/data/index_series.py`、`btf/analytics/benchmark.py`
    已事后登记，旧哈希不可得（标注 `null`）。
  - 常驻测试 `tests/unit/test_baseline_ledger.py`（3 项，按新 15 可证伪：历史可解析 /
    **变更路径真写记录** / 无变更不写噪声）。
- **【AA-2】`test_v77_full_range.py` 补「非系统级（计算链验证）」定性注**（治 P3-NEW-1）：
  文件头 ⚠️ 验证边界段 + 两处用例 docstring——明说两处 `persist=False` 覆盖不到落盘/序列化/
  报告/复核（**正是藏住 P0-NEW-7 五轮的那条路径**）并指向 `tests/system/test_cli_benchmark_e2e.py`。

### 实测摘要（可复核，按铁律新 12）

```
$ python -m pytest -q                  → 771 passed（+3：基线留痕机制用例）
$ python tools/check.py                → bt check: 7/7 通过
$ python tools/check_report_render.py --self-test
   → 变异性检查 A（去 | safe）      : PASS（断言已变红）→ 目标项 [0]=False  签名 <div=0 <script=0 转义<div=31
   → 变异性检查 B（title 误加 safe）: PASS（第 6 项已变红）→ 目标项 [5]=False  签名 <div=31 <script=9 探针裸奔=True
   → 还原后生产态                  : PASS（探针裸奔=False 探针转义=True）
$ python tools/check_plugin_boundary.py → [ok]（--update 无变更路径留痕提示）
$ ruff check .                         → All checks passed!

# AA-3 基线留痕（本批核心文件零变更 → 无需刷新；批 5 那次已 retro 补记）
tools/.plugin_baseline_history.jsonl: 1 行（kind=retro，4 文件，time=2026-09-28T16:42:00）

版本 0.5.5 → 0.5.6（btf/_version.py 单一真源）
```

## [0.5.5] — 2026-09-28

**发布主题：19 号第六轮审查（§26）+ 裁决二派单 —— 报告转义修复（P1-NEW-7）+ 渲染级断言 + 披露/性能收口**

> 依据：`19-架构审查与重构方案-20260927.md` §26（第六轮独立验收）/ §27.3（**Z-1..Z-6**）；执行留痕见该文 **§29**。
> **Z-6**（Y-1 规则源澄清）按裁决归属**赤潮**（文档侧），本批未动。
> **验收用语纪律（§6.3 新 12）**：以下为「命令 → 实测输出摘要」。

### 修复（Fixed）

- **【P1-NEW-7 / Z-1】HTML 报告正文整体被转义（六章节卡表失形、六图全灭）**：
  `viz/report.py` 模板 `{{ section.html }}` → **`| safe`**（`{{ css }}` 同处理；模板上方
  加**转义纪律注释**：纯文本字段**不得**加 `| safe`）。该缺陷自报告功能诞生即存在，
  因「文本级断言看不见标签」逃过 5 轮审查 + 765 测试（**第六种逃逸模式**）。
  真实产物重生成实测：**`<div>=31 / <script>=8 / <table>=6`，转义残留 = 0**
  （修复前同产物 `<div>=0 / <script>=0 / 转义=31/8`）。
- **【P2-NEW-5 / Z-3】基准取数双重 IO**：`runtime._table_store()`（共享
  `YearTableStore(max_entries=16)`）注入 `probe_index_coverage` 与 `load_index_closes`
  两处——装配期守卫与 run 期取数共用缓存，`index_daily` 只读一遍；
  `data/index_series.py` 注释与事实对齐（"不造私有缓存"仅在传参时成立）。
- **【连带】`relative_metrics` n=2 除零**：样本方差/协方差/跟踪误差除以 `(n−1)` →
  原守卫 `< 2` 使 2 日序列抛 `ZeroDivisionError`（非文档口径 `BenchmarkError`）→ 守卫改
  **`< 3`** + 明确信息 + 边界用例。

### 变更（Changed）

- **【Z-4】B2 性能门槛**：`B2_SOFT_TARGET_SECONDS = 15.0`（超限仅打印"超软目标（负载？）"）
  + **`B2_LIMIT_SECONDS = 20.0`**（隔离基线 14.03–14.55s 的 1.42x，余量 43%）——消除负载机
  假失败；取值依据写入 `tests/perf/BASELINE.md` §四「门槛余量纪律」（回归守卫交给
  B3 十年 < 60s + perf 计时套件）。

### 防线（Z-2 / Z-5；铁律新 14）

- **渲染级断言**（§6.3 新 14「渲染/序列化产物必须做渲染级断言」）三处落地：
  `tests/unit/test_viz_report.py::test_render_level_structure`（卡片/表格/脚本 + 反向断言
  `&lt;div class=` 不存在 + `<section` ≥6）、`tests/system/test_cli_benchmark_e2e.py`
  （**真入口**报告）；**旧文本级断言全部保留**（防反向退化）。
- **常跑机检**：新增 `tools/check_report_render.py`（合成产物、秒级、无需主库）并入
  **`bt check` 第 7/7 项**；**负向对照实证**：还原旧模板 → 机检判失败（exit 1），
  还原后 exit 0——证明该机检真能抓住本类缺陷。

### 实测摘要（可复核，按铁律新 12）

```
$ python -m pytest -q                  → 768 passed（+3：渲染级结构 / 共享 store / 除零边界）
$ python tools/check.py                → bt check: 7/7 通过（新增第 7 项「报告渲染级结构」）
$ python tools/check_report_render.py  → 渲染级结构通过（<div=31 <script=8 转义&lt;div=0）
$ python tools/check_rules_four_way.py → PASS（数值四方 + 状态机语义锚点）
$ ruff check .                         → All checks passed!

# Z-1 真实产物复核（重生成 20260928_150907_c4e1f5 的 report.html）
未转义 <div>=31 | <script>=8 | <table>=6 | 转义 &lt;div>=0 | 转义 &lt;script>=0

版本 0.5.4 → 0.5.5（btf/_version.py 单一真源）
```

## [0.5.4] — 2026-09-28

**发布主题：19 号第四·五轮审查（§22/§23/§24）发现项——P0-NEW-7 带基准 CLI 崩溃修复 + 披露闭环**

> 依据：`19-架构审查与重构方案-20260927.md` §22（第四轮独立验收）/ §23（可用性判定）/ §24（第五轮扩展性实测）
> + §22.7 / §23.3 / §24.7 待裁决；执行留痕见该文 **§25**。
> **验收用语纪律（§6.3 新 12）**：以下为「命令 → 实测输出摘要」。

### 修复（Fixed）

- **【P0-NEW-7】配置 `report.benchmark` 后 CLI run 必崩（跑完白烧）**：`relative_metrics` 的
  `benchmark_symbol`（字符串）混入数值指标集 → `store.save_metrics` 的 `float(v)` 在**回测
  跑完之后**抛 `ValueError`（长区间十几分钟白烧）。三层处置：
  ① **元信息剥离**——`benchmark_symbol` 改挂 `runtime.benchmark_symbol`（披露/报告用），
  metrics 只留 11 项数值相对指标；
  ② **数值守卫**（第二层）——`save_metrics` 对非数值键**指名报错**并给出处置建议；
  ③ **衍生修复**——`verify_run` **补算基准项**（基准号取自 `manifest.config_effective`，
  快照双形态容忍）：原复核只算基础 15 项而落盘含基准项 → 配基准的 run 复核摘要**必然不符**
  （假告警）；并修复复核路径 `self.config is None` 时的 `AttributeError`。
- **【P1-NEW-5】口径披露未闭环**：`runtime.assembly_notes`（宇宙口径，如 `source=all`
  期间并集"跨期新增 N 只"）原**零消费者**（只落日志、不进产物）。现 `RunManifest.assembly_notes`
  随产物落盘（缺键向后兼容）+ `viz/report` 新增「**口径与装配披露**」区（装配笔记 + 宇宙口径 +
  基准口径），与 `degraded_notes` **对称**。
- **【P1-NEW-6】基准无装配期守卫**：`report.benchmark` schema 加形制
  `^[0-9]{6}\.(SH|SZ|BJ)$`；runtime `_guard_benchmark` 装配期探测 `index_daily` 区间覆盖，
  **零行即 `ConfigError`**（原须跑完整段回测才报错）。
- **【P2-NEW-3】`universe_span` 回退漏网**：`MemoryFeed.universe_span`（精确窗口并集）；
  runtime 回退分支**显式披露**「起止快照并集，可能遗漏期间内上市且退市标的，量级约 0.2%」。
- **【P2-NEW-4】委托保护**：`MixedDailyFeed.universe_span` 加 `getattr` 兜底（delegate 无该
  可选方法时退化为起止快照并集，不再 `AttributeError`）。

### 防线 / 文档（§24.7）

- **铁律新 13**（§6.3）：**系统级端到端必须经"真入口"**（`bt run` → 落盘 → `bt report` →
  `bt verify`），禁止以内部 API + `persist=False` 充当系统级验收——治第五种逃逸模式
  （P0-NEW-7 逃过全部 755 测试的直接原因）。
  首例：`tests/system/test_cli_benchmark_e2e.py`（真入口四步 + 形制/无数据两种装配期拒绝）。
- **外部策略接入文档**：`examples/README.md` 补教程（策略放哪 / `PYTHONPATH` 注入 / 参数契约 /
  `rules`·`liq_series`·`index_universe` 自动注入钩子 / 基准与产物位置）。
- **§22.4 留痕**：并集优于逐日动态的实测理由（十年仅差 57s / 3%；`20160104` 并集多命中 43 只
  = 元数据噪声下超集更稳健）写入 `TushareParquetFeed.universe_span` docstring。
- **§22.6（Y-1 重定性）**：由「btf 待执行清单」转为**规则源澄清项**（`rules/single-source.md`
  明确入闸确认期状态归属），btf 侧不再列为代码待办。

### 实测摘要（可复核，按铁律新 12）

```
$ python -m pytest -q                  → 765 passed（+10：CLI 基准 e2e 3 + 披露接线 7）
$ python tools/check.py                → bt check: 6/6 通过
$ python tools/check_rules_four_way.py → PASS（数值四方 + 状态机语义锚点）
$ ruff check .                         → All checks passed!
$ tools/check_plugin_boundary.py       → [ok]

# 真入口四步（tests/system/test_cli_benchmark_e2e.py）
bt run（落盘不崩）→ metrics.json 纯数值（含 benchmark_total_return/excess_return/
tracking_error/information_ratio）→ bt report 含基准披露 → bt verify 退出码 0
# 装配期拒绝：000300（缺后缀）→ config-check 退出 1；999999.SH（无数据）→ ConfigError

版本 0.5.3 → 0.5.4（btf/_version.py 单一真源）
```

## [0.5.3] — 2026-09-28

**发布主题：19 号第三轮审查（§20）发现项——P0-NEW-4 宇宙起点冻结修复 + Y-2/Y-4 + X-11 兜底**

> 依据：`19-架构审查与重构方案-20260927.md` §20（第三轮独立验收）+ §20.7 建议；执行留痕见该文 **§21**。
> **验收用语纪律（§6.3 新 12）**：以下均为「命令 → 实测输出摘要」，不使用"全绿"等无据措辞。

### 修复（Fixed）

- **【P0-NEW-4】`source=all` 宇宙在起点冻结**：新增 `TushareParquetFeed.universe_span(start, end)`
  （期间并集闭式 `list_date ≤ end ∧ (delist_date 空 ∨ delist_date ≥ start)`，**一次** stock_basic 读；
  与 `universe` 共用 `_stock_basic_instruments`，差异只在在市谓词）+ `MixedDailyFeed` 委托 +
  runtime `_resolve_all_universe`（无 `universe_span` 的 Feed 回退起止快照并集）+
  **装配期强制披露**跨期新增数量（`assembly_notes`）；`runtime.py`「语义不变」注释订正。
  实测：十年 2,808 → **5,655** 标的（跨期新增 2,847 = 期末 52.4%）；v77 区间 5,067 → **5,690**（+623）；
  期间新上市锚点 688001.SH 由"全程不可见"转为可见。

### 防线（Y-2 / Y-4 / X-11 兜底）

- **Y-2**：新增 `tests/integration/test_risk_chain_e2e.py`（3 项）——**真实风控链**下端到端回测
  （不使用逃生开关），补 §20.4.3「13 处 `allow_empty_chain: true` 致风控路径覆盖偏薄」。
- **Y-4**：§6.3 立铁律**新 12**「禁止以'全绿'作验收用语，须附可复现实测输出摘要」；本段起采用该写法。
- **X-11 兜底**：`bt test` 子进程再加 `--basetemp`（显式 basetemp 下 pytest 不做历史临时目录 GC——
  第三轮审查环境实测批量删除 **329 个文件**被守卫拦截 → 退出码非 0 → 误判失败）+ 自建目录自行回收。

### 实测摘要（可复核）

```
$ python -m pytest -q                 → 755 passed（+7：universe_span 4 + risk e2e 3）
$ python tools/check.py               → bt check: 6/6 通过
$ python tools/check_rules_four_way.py → PASS（数值四方 + 状态机 RECOVERY 语义锚点）
$ ruff check .                        → All checks passed!
$ tools/check_plugin_boundary.py      → [ok]（核心 3 文件：data/feed、runtime、cli/main）

# P0-NEW-4 修复后 v77 端到端（2023-01~2026-09，source=all）
universe = 5,690（期间并集；起点快照 5,067 → 跨期新增 623，装配期已披露）
n_days 904 / fills 447 / rejections 6 / +23.62%
benchmark(000300.SH) +16.19% → excess +7.44% / IR 0.0548
LIQ 分布 {NORMAL 862, RECOVERY 19, WATCH 16, CRISIS 7} | 平均仓位上限 48.24%
```

> 未采纳项（留痕待裁决）：**Y-1**（RECOVERY 入闸侧「连续 3 日无危机+无 WATCH」）——落实后序列变为
> `CRISIS → NORMAL×3 → RECOVERY×3 → NORMAL`，与 §17.3.3 审查方已验收的 `RECOVERY×3 → NORMAL`
> 冲突，且使危机后立刻回到 5 成仓位上限（与"逐步恢复"本义相反）；现状量级已极小（十年 RECOVERY 1.7%）。

## [0.5.2] — 2026-09-28

**发布主题：19 号架构审查第二轮（§17）独立验收的待执行清单 X-1~X-12 全数执行——"语义正确性收口 + 声明与事实对齐"**

> 依据：`文档/回测工具架构/19-架构审查与重构方案-20260927.md` §17（第二轮独立验收）+ §18.1 老大裁决 + §18.2 待执行清单。

### 修复（Fixed）

- **【P0-NEW-1 / X-1】L0-LIQ `RECOVERY` 永久自维持（状态机卡死）**：`data/liq._finalize`
  删除自我维持子句 `or RECOVERY in prev_statuses[:1]`（CRISIS 后 3 日窗口期满自动回
  NORMAL，与规则源「CRISIS→RECOVERY→3日→NORMAL」对齐）。
  实测：十年 RECOVERY **27.1% → 1.7%**（19 段 × 最长 **3 日**，原最长 182 日）；
  v77 区间平均仓位上限 **33.90% → 48.24%**（+14.34pp，与报告修正值逐位吻合）。
  同修 `state_of` 与 `precompute_liq_series`（共用唯一口径）。
- **【X-5/X-6】`data.completeness` 死键残留清零**：删 `examples/config_rotation.yaml`
  死键；`cli/main.py` 打印改消费真实生效键 `allow_degraded_limit`；`test_config.py`
  env 夹具改用 `run.params`。全库 `grep completeness` 归零。
- **【连带修复①】样例策略缺 `contract_version`**：`examples/rotation.py` 补 S1 声明
  （死键被清后 config-check 放行、才暴露此层——版本协商期拒载）。
- **【连带修复②】配置宇宙未被 `ctx.universe()` 尊重（静默零成交）**：引擎
  `universe_loader` 原恒取 `feed.universe(date)`，与截面 `cross_section`（=
  `instruments`）**不同源** → 子集宇宙（`source=explicit|index`）下策略选出的标的
  不在截面 → 再平衡**零订单**（样例实测 728 日 0 成交 0 拒单）。现以 `instruments`
  为宇宙（无显式 `instruments` 时回落 feed）。
- **【X-9】`YearTableStore` 并发安全**：加 `threading.Lock`（缓存/计数器临界区，
  **IO 在锁外**保留 pyarrow 并行收益）+ 装载**双检**（并发重复装载只计一次 →
  `load_count` 断言可靠）；订正 `liq.py`「无共享状态」的失实注释。

### 变更（Changed）

- **【X-7 / 裁决 1】风控 Q1 改 A —— 默认 fail-closed**：默认层**不再声明**
  `allow_empty_chain` → 空风控链装配期 `ConfigError`；逃生开关须显式 `true`
  （仍进 manifest 回显 + 报告假设章节披露）。三内置规则 `strict` 默认改 **`True`**
  （缺盯市价/状态面板即拒单；需历史宽松语义者显式 `strict=false`）。
  失败面已评估并逐处显式声明调试意图（CLI/API/网格/指数宇宙/起点/运行时测试
  配置各加 `"risk": {"rules": [], "allow_empty_chain": True}`）。
- **【X-11】`bt test` 子进程隔离硬化**：子进程加 `-p no:cacheprovider` + 剥离
  `PYTEST_CURRENT_TEST`（父子共用 `.pytest_cache` 是"全量跑失败、单跑通过"最可能
  污染源）；本轮全量跑**未能复现**原失败（已转绿），硬化为防复发。
- **【X-10】perf 阈值改用镜像 JSON 真值**：`tests/perf/test_liq_precompute.py` 的
  `THRESHOLDS` 由自造副本（sh_crisis −3.0 / down_crisis 100…）改为
  `rules_mirror_v77.json` 真值（−5.0 / 800…），并加 `test_thresholds_match_mirror`
  防再次漂移。

### 防线（X-2/X-3/X-4/X-12）

- **X-2**：RECOVERY 序列测试改为**序列推进**（逐日推进 12 日断言 `RECOVERY×3 →
  NORMAL` + 60 日平静期零 RECOVERY），废弃"手工构造 prev_statuses"的伪造输入。
- **X-3**：`tests/system/test_v77_full_range.py` 补**状态分布断言**
  （NORMAL>90% / RECOVERY<5% / 连续 ≤6 日 / 平均仓位上限 >45%）。
- **X-4**：`tools/check_rules_four_way.py` 补**状态机语义锚点**——源「3 日恢复」×
  `md_core`「连续 3 日 status」× btf `_finalize` 窗口结构 + **自我维持回归守卫**
  （`_code_only` 去注释/docstring 后扫代码结构，避免修复留痕文本误报）。
- **X-12**：立铁律「**序列依赖逻辑必须做序列推进测试**」→ 写入 19 号 §6.2 依赖铁律
  第 11 条，并在 `tests/unit/test_v77_rules.py::TestRecoverySequence` 落首例。

### 测试

- 全量 **748 测试**绿（+16）· `bt check` **6/6** · 规则机检（数值四方 + 状态机语义）
  PASS · ruff 全清。

## [0.5.1] — 2026-09-27

**发布主题：19 号架构审查修正（R0 安全网 + R1 P0 止血 + 高优先 P1/P3）——"基础设施补完 + 声明收口"第一批**

### 新增

- **LIQ 序列预计算（P0-1）**：`data/liq.precompute_liq_series`——Arrow `index_in`+`take`
  对齐 / `group_by` 计数 / 逐年并行 / 免排序装载 / `index_daily` 谓词下推；
  `V77Strategy(liq_series=…)` 注入（runtime 装配期按 `L0_LIQ` 阈值预计算）。
  **十年 760s → 3.74s（203x）**，全区间 2430 日与 `state_of` 逐字段零差异。
- **表级资源层（Q2=A 最小抽象）**：`data/core.YearTableStore`——年装载 / 按日索引 /
  谓词下推（`read_year_where`）/ 有界缓存 / **装载计数**（性能断言用）；
  `data/index_series.load_index_closes`（基准取数，缺日 fail-closed）。
- **基准对比层（P1-10）**：`analytics/benchmark.relative_metrics`（11 项相对指标：
  超额/年化超额/跟踪误差/信息比率/beta/alpha/上下行捕获/基准收益/年化波动）+
  `runtime._benchmark_metrics` → `report.benchmark` **死键转活**（原校验通过但零消费）。
- **区间覆盖度守卫（P1-11）**：`data/coverage.check_coverage`——区间早于 `stk_limit`
  起点（2008-01-02）或起止倒置 → 装配期 `ConfigError`（原为**静默失效**：涨跌停约束
  消失、成交系统性偏乐观）；`data.allow_degraded_limit=true` 可降级并强制披露。
- **缺口披露对称（P2-9）**：`StateSynthesizer.stock_limit_missing_dates` →
  `degraded_notes()`（股票限价缺失与 ETF 限价回退走同一披露通道）。
- **V77 真实全区间端到端**（R1-2，首次）：`tests/system/test_v77_full_range.py`
  （2023-01~2026-09：904 日 / 408 成交 / +10.08% / Sharpe 0.359）。
- **性能门槛常跑化**（R0-2/R5-5）：`tests/perf/`（LIQ 预计算计时 + 等值 + 装载计数）
  + `tests/perf/BASELINE.md`（B1–B5 与专项基线档案）。

### 变更（Changed）

- **风控「fail-closed」（P0-3，Q1 保守 B 方案）**：`risk.rules` 为空须**显式**
  `risk.allow_empty_chain=true`（默认层已写并强制披露；`false` → 装配期
  `ConfigError`）；三内置规则新增 `strict` 参数（缺数据拒单而非放行）。
  > ⚠️ **措辞订正（2026-09-28，19 号 §17.4.2 / §18.1 裁决 1）**：本条标题
  > 「fail-closed」与实测**不符**——v0.5.1 默认层写的是 `allow_empty_chain=True`
  > （**默认放行**），实为 B 方案；三规则 `strict` 亦默认 `False`。**0.5.2 起按裁决
  > 改 A（默认拒绝）**，措辞自此与行为一致。
- **策略版本协商（P1-2/EX-2）**：`runtime._load_strategy` 经 `negotiate` 校验
  `contract_version`（未声明/主版本不符拒载）——顺带为 `MonthlyEqualWeight` /
  `QidianStrategy` / 测试夹具补声明。
- **扩展点接线（P1-1/EX-1）**：RUN_STORE 与 RULES_PROVIDER 经 registry 取用
  （未知 provider 走名字表兜底）；ANALYZER 经 `compute_all(analyzer_names, resolver)`
  注入（`analysis.metrics` 配置名字表，注册自定义指标零源码改动生效）。
- **死键清理（P1-5/P3-5）**：`data.completeness.missing_bars_action|max` **删除**
  （声明而不消费）；`report.sections` 接线（别名表映射；假设章节强制保留）。
  > ⚠️ **措辞订正（2026-09-28，19 号 §17.4.3）**：v0.5.1 仅改 schema/DEFAULTS，
  > **仍有 3 处消费点残留**（`examples/config_rotation.yaml`、`cli/main.py` 打印、
  > `tests/unit/test_config.py` env 注入）——其中样例 YAML 直接导致 2 个系统测试
  > 失败。**0.5.2 全库清零**。
- **装配顺序修复（实证 bug）**：`rules_provider` 必须先于策略加载装配——原顺序下
  `rules`/`liq_series` 注入**恒不生效**（该路径从未端到端跑通故未暴露）。
- schema：补 `analysis` 段（`risk_free_rate`/`annualization_factor`/`metrics`）+
  `risk.allow_empty_chain` + `data.allow_degraded_limit`；样例配置同步（index 宇宙新写法）。
- 性能：`closes(wanted)`（PF-3，原全截面 dict 十年 ≈9.7e6 写）、`snapshot` 真单遍
  （PF-8）、`TushareAdjustService` 经 `symbol_store.symbol_range`（P1-9，N 标的 N 次
  36 年全表 → 单次装载）。
- 版本 0.5.0 → **0.5.1**（`pyproject.toml` 改 `dynamic` 读 `btf._version`——**版本双源
  不一致修复**）；插件基线 59 文件。
- 注释/措辞订正（P3-1/P3-2/P3-6/P3-7）；`btf/_version.py` 保持单一真源。

### 测试

- 全量 **732 测试**绿（+27：19 号修正验收 22 + perf 3 + 系统 2）· `bt check` **6/6** ·
  六契约 KEPT · ruff 全清。
  > ⚠️ **措辞订正（2026-09-28，19 号 §17.4.1）**：实测**并非全绿**——3 个失败
  > （`test_example_rotation` ×2 = 样例配置死键；`test_runs_smoke_subset` = 子进程
  > 隔离）。另：v77「+10.08%」验收数字是在 **RECOVERY 状态机卡死**的世界里跑出的
  > （仓位上限被系统性低估 14.34pp），**不可采信**（19 号 §17.3.4）。
- 未执行项（R2 完整版 / R4 / R5 / P1-3/P1-4/P1-8/P2-x）与 Q1–Q4 处置**逐项留痕**于
  19 号报告附录 D（含回滚与依据）。

## [0.5.0] — 2026-09-27

**发布主题：v0.5 功能补全与性能达标（V5-1~V5-7 全项交付，v0.5 出口达成）**

### 新增

- **VPP 撮合量上限（V5-6/G10）**：`NextOpenHandler(volume_participation=rate)`——成交 =
  min(委托量, bar.volume×rate)；买入整手化（cap<一手 → 新拒单码 `VOLUME_CAP`）；部分成交
  余量当日取消（`vpp_stats` 披露）；现金检查按受限数量。配置 `execution.handler_params`。
- **G10 黄金案例（V5-1）**：`g10_vpp_partial_fill`（部分成交）+ `g10_vpp_volume_cap_rejected`
  （量上限拒单）→ **黄金集 G1–G10 共 16 案例全建，pending 清零**。
- **优化器 grid_search（V5-3）**：`btf/optimize/grid.py`（笛卡尔积保序 + ProcessPoolExecutor
  + objective 排名 NaN 末位 + workers=0 内联调试）+ registry `OPTIMIZER` 名字表 + CLI
  `bt optimize`（--grid/--objective/--workers/--out）。
- **指数成分宇宙（V5-4）**：`IndexUniverseProvider`（index_weight 月末快照机器复算，严格
  PIT：快照日<查询日；别名 hs300/zz500/sz50/zz1000/cyb；无前向填充）+ runtime
  `universe.source=index`（月度映射注入策略）+ `btf/strategy/monthly.py::MonthlyEqualWeight`。
- **ThresholdRebalancer（V5-5）**：阈值再平衡（带内不动/带外到目标/目标外全清）；
  threshold=0 与差额法逐单一致；registry `threshold` + `execution.rebalancer_params`。
- **api Facade（V5-7）**：`btf.run / btf.report / btf.dataset` 顶层函数（`__getattr__` 惰性
  暴露，import btf 零成本）；与 CLI 等价（同配置 digest 一致 / 同 run 报告字节一致）。
- **B4 参数扫描基准**：12 组合×8 进程 8.0s → 外推 1000 组 **667s < 1800s**（余量 2.70x）。

### 性能（Performance）

- **标的谓词下推**：`iter_day_slices(symbols=…)`——B2/B4 子集宇宙场景免扫全市场年表
  （12 组合×8 进程 47.9s→8.0s，6x；B3 全市场语义不变，成交数逐位一致）。
- **B3 全市场扫描十年尺度 129.3s → 32.9s（3.9x，< 60s 预算；18 号 A2 关闭 / M2 5.10 收口）**，
  语义零漂移（B2 2272 成交 / B3 十年 360,410 成交·50,998 拒单逐位一致 + 黄金集 L3 对账
  1e-10 全绿）：
  - `TradingDate.from_ymd` 进程级 memo（适配层逐 bar 调用 68 万次/年，交易日域 ≤1 万值）；
  - 列式截面 `_LazyCrossSection.closes()` 轻量收盘视图——引擎 mark 步骤免
    ~8M Bar/十年全量物化（「快照字段物化削减」）；
  - `StateSynthesizer` stk_limit **按日切片索引**：年表排序一次 + `{ymd: (lo,hi)}` 行区间 +
    (年,日) 缓存——大标的集且逐日变化场景由 ~100 万行 dict/查询（占 40% 耗时）→ ~10ms/日；
    **小标的集保留 (年, 集合) 缓存双路混合**（单路按日切片曾致 B2 15.25s>15s 回归，
    混合后 14.55s）；
  - `Portfolio.snapshot` 单遍融合（total_value/market_value/weights 三次持仓遍历 → 一次）。
- 基准 `test_b3_full_decade_scan` 解除 skip → **常跑硬门槛**
  （2431 日 / 360,410 成交 / 50,998 拒单 / 32.9s，余量 1.82x）。

### 变更（Changed）

- 版本 0.2.0 → **0.5.0**；`btf/_version.py` 版本单一真源（manifest 不经包根取版本——
  import-linter 假链治理）；`btf.api` 入口层 / `btf.optimize` 编排层入 import-linter
  layers 契约（六契约全 KEPT）。
- **B2 测试策略 PIT 订正**：Hs300TopN 决策快照由「当月快照」（前视）改为「上月末快照」
  （V5-4 同口径）；拒单码全集六→八（+VOLUME_CAP/RISK_REJECTED）；CLI 七命令（+optimize）。
- 核心 9 文件变更 + 新模块 6 个 → 插件 SHA256 基线刷新（55 文件）+ 注册点唯一性复验通过。

## [0.2.0] — 2026-09-27

**发布主题：M3 第一验收场景 —— v7.7 规则引擎回归 + 奇点战法复跑（双验收通过，MVP 完成）**

### 新增

- **规则加载（6.1）**：`strategy/rules.py::MirrorJsonRulesProvider`（指纹 `v7.7@sha256:`、取参、
  aliases 文档键桥接、units 单位显式、未登记显式报错、overrides 留痕）；`tools/export_rules_mirror.py`
  （幂等载荷 + 内容敏感指纹）；`tools/check_rules_four_way.py`（源↔镜像↔消费绑定↔btf 缓存**四方机检**）。
  registry `RULES_PROVIDER` 注册 `mirror_json`；runtime 装配（`rules.path` 缺失 → `ConfigError`）。
- **v7.7 规则策略（6.2）**：`data/fundamentals.py::screen_l2`（L2 硬筛 PE/PB/ROE_WAA/股息率，
  **真 PIT**：`ann_date ≤ 回测日` 且仅年报）；`data/liq.py`（L0-LIQ 状态机 NORMAL/WATCH/CRISIS +
  RECOVERY 序列判定 + 仓位上限）；`strategy/v77.py` 策略类；`tools/align_v77.py` 对账 CLI。
- **奇点战法信号层（6.3）**：`strategy/qidian.py` —— 真源 `代码/strategies/qidian.py` **运行时加载**
  （不复制清单，`BTF_QIDIAN_REF` 可覆盖）+ 纯 Python 复刻（W-FRI 周线自聚合 / 标准 KDJ(9,3,3) /
  CCI(20) / 阈值与买入优先 / ETF×指数双重确认）+ `pool_groups()` 真源注释分组解析 +
  `QidianStrategy`（周五决策、逐周截断无前视、`bars_provider` 可注入）。
- **ETF 身份推断**：`domain.infer_instrument` / `infer_etf_subclass`（名称优先 + 代码段兜底），
  runtime 宇宙解析对 ETF 正确推断子类 → T+N 由**资产 × 子类**双维驱动（终审 E2）。
- **验收报告**：`回测产物/场景A验收报告-20260927.md`、`回测产物/场景B验收报告-20260927.md`。

### 验收证据

- **场景 A**（09 §14.7）：A1 选股与 md_core 同日 **901=901** 零差异；A2 LIQ 状态 **NORMAL=NORMAL**；
  A3 override 为空参数逐项等于镜像 JSON + 四方机检 PASS；A4 单参数升级（pe_max 30→25）名单为子集、
  被剔除标的 `pe_ttm` 全落 [25,30)、边界外零变化。
- **场景 B**（09 §14.7）：B1 与真源实现同真实周线逐项一致 —— **42 对 ETF/指数、84 条序列零差异**
  （周线聚合 1e-12 / KDJ、CCI 1e-9 / 信号逐序列相等）；B2 「双回测一致优秀」**23 只**分组机检 + 数据可用
  **23/23**；B3 池内 **511180/511380 债券 ETF 同日往返成交**（T+0），股票型 ETF 510300 对照仍拒 `t_plus_1`。
- 全量回归 **588 项**绿；`bt check` **6/6**；插件边界（注册点唯一 + 核心 53 文件 SHA256 基线）通过。

### 变更

- 版本号 `0.1.0.dev0` → `0.2.0`（`pyproject.toml` / `btf/__init__.py`，smoke 断言同步）。
- `runtime._resolve_instruments`：非 `stock_basic` 标的由「默认 main/lot100 占位」改为
  `infer_instrument` ETF 身份推断（日志级别 warning → info）。
- `config/paths.py` 新增 `QIDIAN_REF`（外部信号源单一真源，`BTF_QIDIAN_REF` 可覆盖）。

### 修复

- `tools/align_v77.py` 与集成对账：`md_core` 顶层不导出 `screen_stocks_v75` / `get_l0_liq_state`
  （实为 `md_core/screening.py`、`md_core/market_state.py`）——改子模块导入并显式传镜像参数
  （原调用为坏链）。
- 清除中断残留的 4 处 ruff 未清欠（`bt check` 5/6 → **6/6**）。

### 已知未达标 / 回退披露

- **场景 B 引擎级全区间净值复跑未执行**（E3 回退披露）：引擎截面与 `ctx.history` 当前仅 `daily`（股票）
  通路；ETF（`fund_daily`）/指数（`index_daily`）日线通路未接入引擎 —— 归 **v0.3** 数据层（Feed 混合表
  装配）后补跑 ≥2019-06-26 全区间。
- **B3 全市场扫描十年尺度**（M2 遗留）：2 年尺度 17.5s，线性外推十年 ≈88s > 60s 预算；10 年尺度内存
  不可承载（`data/state._limits` 按年全量载入 `stk_limit`）——已 skip 留痕，触发 CP1 性能专项（v0.5）。
- **黄金集 G2–G10**：按 05 §20.1 三层数据集体系规划，G1（公司行动）已建，其余归 v0.5。
- 部分指数源端仅 `close` 无 OHLC（如 H30315.CSI）——奇点信号对账按 OHLC 齐全过滤并留痕。

## [0.1.0.dev0] — 2026-09-26

**里程碑：阶段 0 工程骨架 → PoC-1/2/3 → M1 契约与内核固化 → M2 可信回测闭环**

- 阶段 0：ruff / import-linter 六铁律契约 / pytest 分层标记 / `bt check` 五项 / 依赖预算 8/8。
- PoC-1 数据契约直读（B1 9.74s < 10s）；PoC-2 事件循环确定性（双跑 diff=0，B2 12.99s < 15s）；
  PoC-3 复权与公司行动（G1 五案例双层断言、G9 等价断言）。
- M1：`CONTRACT_VERSION="1.0"` + 主版本协商；registry 六扩展点名字表；四层配置合并 + JSON Schema +
  脱敏 + `bt config-check`；domain 白名单 + 插件边界基线。
- M2：分段费率 + 滑点；风控规则链 + 引擎⑧步接线；再平衡差额法 + 策略基类；NAV 四式 + 15 项指标；
  RunStore/manifest 四版本 + 原子写 + R1 复现；独立参考计算器 + 黄金集 G1 + L3 对账（1e-10 零漂移）；
  viz 六图 + 自包含 HTML 报告（假设章节强制）；CLI 六命令族；B3/B5 基准 + 示例轮动策略。
