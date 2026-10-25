> 事实锚点：2026-10-25
> - 覆盖范围：btf CLI 手册
> - 事实真源：btf CLI 注册
> - 关键计数：1219（非 L5 1197 ＋ L5 22）＝工作区 2026-10-25

# btf 回测平台 · CLI 功能手册（**v0.4（btf 2.0.0 基线，2026-10-25）**；策略出仓＋性能/披露治理收口——KK/LL/MM 全批）

> **定位**：`bt` 命令行的**完整功能参考**——逐命令给出用途、语法、参数表、行为分步、输出样例、退出码与限制。
> **只用工具**请看 [20-回测平台使用指引.md](20-回测平台使用指引.md)（5 分钟上手向）；本手册面向「查参数、排故障、写自动化脚本」。
> **口径来源**：实现 `代码/btf/btf/cli/main.py`（btf **2.0.0**；2.0.0 发版 2026-10-25）；全部命令帮助与只读命令输出于 **2026-09-30 在本机实测**复现，2026-10-06/07 增量命令（`freeze` / `--allow-short` / `--delisted-settlement`）随批实测，**2026-10-09 复核实测**；**2026-10-10 复核实测**（`bt --version` → `btf 1.2.4`；13 命令族含 `data`）；**2026-10-11 复核实测**（`bt --version` → `btf 1.3.1`——版本统一推进，无功能变更；**现版本见本手册头部，不在此行随批漂移**）。**2026-10-14 增量：`adhoc` 收编（HH-3，14 命令族；§4.14）**。**2026-10-25 增量（2.0.0）：策略出仓（`run.strategy_paths`，§4.2）／多基准（§7.2）／`bt dataset freeze` 清单契约与 MM-7 自锁修复（§4.8）／性能门槛 MM-8（§5.3）**。
> **维护**：CLI 改动必须同步本手册；相邻文档：装配/引擎语义见 04/06 号，产物契约见 08 号，门禁口径见 09 号。
> 2026-10-05 补 §0「输出解读纪律」（Q3 裁决 · 纪律类立即生效）。

---

## 0. 输出解读纪律（**首条 · 必读**）

> 本节约束的是**如何解读 `bt` 的输出**，不依赖代码交付 ⇒ **即刻生效**。
> 依据：19 号 §60 轮次十四；老大 **2026-10-05 Q3 裁决**。完整使用纪律见 **20 号首条**。

### 0.1 三条硬规矩（**2026-10-06 更新：RC-01..16 已全交付，独立复核见 19 号 §65**）

1. **`bt run` 的输出是"记账结果"，不是"策略有效证明"。**
   CLI 打印的年化 / 夏普 / 超额**只说明账算得对**，不说明策略可行。
   **成本敏感性复跑已完成**（v77 同区间：零摩擦 **+7.19% / IR 0.0504**｜交付默认 **+6.07% / 0.0305**｜0.3% 压力 **+4.28% / IR −0.0013**）⇒〔⚠ 偶然产物｜生成机制 = L2 消融 A1 臂（现行实现 current × 代码序）；无先验、不可迁移；详 L2 复算报告〕
   引用**必须标注成本口径**；未附框架外推断证据者仍加限定语「**零摩擦假设下的记账结果**」（[20 号纪律二](20-回测平台使用指引.md)）。

2. **`bt optimize` 的最优结果必须连同试验数 N 一起解读。**
   从 N 组里挑最好的那一个，**N 越大、最优值越不可信**（多重检验）。
   **RC-06/07/08 已交付**：`optimize` 现输出 `n_trials / psr / dsr` 与 `0.05/N` 阈值披露（`--perturb` 邻域退化可用）⇒
   引用仍须**连同 N 与校正值**；**只报最优值、不报 N** 的结论一律不采信。
   **CL-1 起**：样本 < **250 交易日**（≈1 完整交易年）**默认直接阻断**（退出码 3；`--allow-short` 显式放行
   且 override 记入产物）——"区间太短"不再只是告警（见 §4.7）。

3. **`bt report` / `bt show` 的"假设"段现读实际配置（RC-03 已交付）。**
   `app/services.py::assemble_assumptions` 取 `config_effective`（滑点 / 撮合同源渲染）——**旧硬编码 `"none（回测基线）"` 已移除**；
   抽查口径仍以 `manifest.json::config_effective.execution` 为准；**显式 `none` 会以「零摩擦（显式声明）」披露**。

### 0.2 参数默认值风险速查

| 项 | 当前默认（实测） | 风险 | 相关 RC |
|---|---|---|---|
| `execution.slippage` | **`none`** | 零滑点，系统性高估收益 | RC-01 |
| `execution.handler_params.volume_participation` | **未设（= 100% 成交）** | 无视流动性约束 | RC-02 |
| `report.benchmark` | **可不配** | 无基准裸奔，超额无锚（**MM-6**：亦可 1–3 个代码的数组，首个＝主基准——只有它进 `metrics.json`） | RC-09 |
| `cost_model` | `flat_rate`（印花税恒 0.05%） | 2023-08 前 7.5 年卖出印花税按半价计 | RC-05 / 19 号 §60 |

> ⚠ **默认值不等于推荐值。** 上表为**当前实测**的默认行为，不是最佳实践；
> 这些默认值将在 RC-01/02/05/09 交付后变更，**届时本表由执行方同步更新**（配置类随批次）。

---

## 1. 手册使用说明

### 1.1 覆盖范围

| 覆盖 | 不覆盖 |
|---|---|
| `bt` 全部 11 个子命令的语法 / 参数 / 默认值 / 退出码 | 策略编写（→ 20 号 §10） |
| 每个命令的行为分步与输出格式 | 引擎撮合/成本/风控内部语义（→ 04/06 号） |
| 环境变量、产物目录契约、配置速查 | 架构决策与演进历史（→ 01-19 号） |
| 典型工作流、故障排查 | Python API（`btf.api`）用法 |

### 1.2 阅读约定

- 命令一律写作 `bt <子命令>`；`$ ` 开头的行为可复制命令；
- 样例输出中 `[ok]/[info]/[warn]/[error]/[fail]/[dry-run]/[note]` 是**真实输出前缀**，见 §5.2；
- 参数表「默认」列是**实际生效值**（含内置默认层）；
- 标注「**改产物**」的命令会写磁盘，其余只读。

---

## 2. 全局概览

### 2.1 命令族地图（14 命令，按场景分组）

```
① 核心回测链路   config-check → run → runs → show → report → verify
② 研究调优       optimize
③ 数据资产       dataset / data（专项扫描 scan-consolidation；R34′）
④ 工程门禁       check / test / cold-backup
⑤ 推断校验       validate（walk-forward | mc；RC-15/16）
⑥ 外部脚本       adhoc（一次性/外部脚本产物契约封装；HH-3）
```

### 2.2 命令总表

| 命令 | 作用 | 关键参数 | 改产物 | 退出码 |
|---|---|---|---|---|
| `config-check` | 四层合并 + JSON Schema 校验 + 摘要 | `<config.yaml>` `--dump` `--resolve-strategy` | 否 | 0/1/2 |
| `run` | 执行回测并落盘（`--dry-run` 只装配） | `--config` `--set` `--dry-run` `--out` `--verbose` `--delisted-settlement` | **是**（建 run 目录） | 0/1/2 |
| `runs` | 列 RunStore 下的 run 摘要（只读） | `--latest N` `--out-dir` | 否 | 0/2 |
| `show` | 单 run 的 manifest + 指标摘要（只读，不重算） | `--run` `--out-dir` | 否 | 0/1/2 |
| `report` | 由产物再生成 HTML 报告（幂等） | `--run` `--out` `--out-dir` | 写 `report.html` | 0/1/2 |
| `verify` | R1 复核：产物重算指标 vs `manifest.metrics_digest` | `--run` `--out-dir` | 否 | 0/1/2 |
| `optimize` | 参数网格搜索（`grid_search` 进程池） | `--config` `--grid` `--objective` `--workers` `--top` `--out` `--allow-short` | **是**（多次 run） | 0/1/2/**3** |
| `dataset` | 黄金集：构建 / 主库对账 / 内嵌重算 / **数据冻结清单** | `build-golden\|verify\|refcalc\|freeze` `--cases` `--list` `--force` `--config` `--mode` `--out` | 视动作（`freeze` 写只读清单） | 0/1/2 |
| `data` | 数据专项扫描（`scan-consolidation`：ETF/LOF 份额折算**双证据互校** ＋ fail-closed；R34′ 已交付） | `scan-consolidation` `--config` `--out` `--tolerance` | 写结果 JSON（`--out`） | 0/1/2 |
| `adhoc` | 一次性/外部脚本**产物契约封装**（执行差分登记 / register-only 存量登记 / 登记台） | `--name` `--script` `--params` `--tables` `--as-of` `--watch-dir` `--out` `--register-only` `--list` `-- <脚本参数>` | 写 adhoc 登记台（`回测产物\adhoc\`） | 0/1 |
| `test` | 运行测试层（pytest 执行器） | `--layer` `--pattern` `--path` | 否 | pytest 码 |
| `check` | 十项架构门禁（提交前必跑，无参数） | — | 否 | 0/1 |
| `validate` | 滚动验证 / 蒙特卡洛（`walk-forward` / `mc`） | `walk-forward\\|mc` `--config` `--window` `--test` `--step` `--mode` `--purge` `--embargo` `--no-persist` `--run` `--n` `--seed` `--out` `--out-dir` `--set` | `mc` 否；`walk-forward` **是**（每折落盘） | 0/1/2 |
| `cold-backup` | 跨版本源码冷备 / 校验既有冷备 | `--reason` `--out` `--verify DIR` | **是**（写备份） | 0/1/2 |

> **易混对**：`check` ≠ `test`（门禁 vs 回归测试层）；`show` ≠ `verify`（读事实 vs 重算对账）；
> `run --dry-run` ≠ `config-check`（装配期硬门 vs Schema 校验）。

### 2.3 标准调用序列

```mermaid
flowchart LR
    A["config-check<br/>静态校验"] --> B["run --dry-run<br/>装配预演"]
    B --> C["run<br/>执行+落盘"]
    C --> D["runs / show<br/>查看"]
    C --> E["report<br/>HTML 报告"]
    C --> F["verify<br/>R1 复核"]
    G["optimize<br/>调参"] -.多组合调用 run.-> C
```

---

## 3. 运行环境与入口

### 3.1 解释器与命令入口

| 项 | 值 |
|---|---|
| 解释器 | `D:\量化策略\代码\_venv\Scripts\python.exe` |
| 命令入口 | `bt`（console script，已安装；`bt --version` → `btf 2.0.0`） |
| 等价调用 | `python -m btf.cli.main <命令>`（脚本化推荐，不依赖 console script） |
| 仓库根 | `D:\量化策略\代码\btf`（含 `btf/` 包、`tests/`、`tools/`、`schemas/`、`examples/`） |

### 3.2 全局选项

| 选项 | 说明 |
|---|---|
| `-h`, `--help` | 打印帮助（顶层或任意子命令；退出码 0） |
| `--version` | 打印 `btf 2.0.0`（版本单一真源 `btf/_version.py`） |

```bash
$ bt --version
btf 2.0.0

$ bt --help
usage: bt [-h] [--version]
          {adhoc,check,cold-backup,config-check,data,dataset,optimize,report,run,runs,show,test,validate,verify} ...
```

> 不给子命令、或给未知子命令 → argparse 报错并**退出码 2**（脚本中可据此区分「用法错误」与「业务失败」）。

### 3.3 工作目录与路径解析

- `bt` 可在**任意目录**运行（内部锚定仓库根，委托脚本用绝对路径调用解释器）；
- 配置里的**相对路径**（`--config`、`rules.path`、`run.strategy` 模块导入等）以**执行时的工作目录**为基准；
  脚本化时请先 `cd D:\量化策略\代码\btf` 或全程用绝对路径；
- `bt test` 与 `bt check` 固定以**仓库根**为子进程 cwd（与调用目录无关）。

### 3.4 环境变量

| 变量 | 作用 | 默认值 |
|---|---|---|
| `BTF_OUTPUT_DIR` | 回测产物根（其下建 `runs/`） | `D:\量化策略\回测产物` |
| `BTF_DATA_DIR` | 主库根（只读） | `D:\全量数据\market_data` |
| `BTF_DATASETS_DIR` | 黄金/示例数据集根 | `D:\量化策略\代码\btf\btf_datasets` |
| `BTF_ALT_DATA_DIR` | akshare 备库根（v0.5 接入预留） | `D:\全量数据\alt_data` |
| `BTFTOOL_CHECK_PYTEST_BASETEMP` | 门禁第 3 项 pytest basetemp **根**（N4① / **N6 分根**） | `D:\量化策略\代码\_scratch\pytest_tmp\check` |
| `BTFTOOL_TEST_PYTEST_BASETEMP` | `bt test` basetemp **根**（N5-② / **N6 分根**；其内再建**每次唯一**子目录） | `D:\量化策略\代码\_scratch\pytest_tmp\bt_test` |
| `BTFTOOL_CHECK_LOG_DIR` | `bt check` 逐项输出日志目录（N4①） | `D:\量化策略\代码\_scratch\bt_check_logs` |
| `BTF_<SECTION>__<KEY>` | **L2 配置层覆盖**（`__` 表嵌套；值自动推断 bool/int/float/str） | — |

> **⚠ 命名警示（N7，2026-10-06）**：工具变量一律 **`BTFTOOL_` 前缀**；**切勿使用 `BTF_` 前缀的自定义
> 变量**——`BTF_<SECTION>__<KEY>` 是**配置覆盖命名空间**（`loader.from_env` 注入），`BTF_` 开头且不在
> 白名单（仅 `BTF_DATA_DIR` / `BTF_OUTPUT_DIR`）的变量会被当作**配置键**注入 ⇒ **配置校验失败**
> （`Additional properties are not allowed ('check_pytest_basetemp' was unexpected)`），`bt run` 等
> 一切加载配置的路径全废（19 号 §69.4 实测；历史变量 `BTF_CHECK_*` 已因此改名）。

```powershell
# 例：把产物根改到临时盘；打开风控链逃生开关（仅调试）
$env:BTF_OUTPUT_DIR = "E:\bt_runs"
$env:BTF_RISK__ALLOW_EMPTY_CHAIN = "true"
```

> `BTF_DATA_DIR` / `BTF_OUTPUT_DIR` 属**路径层专用**，不进入配置合并；其余 `BTF_*` 按 L2 覆盖处理（空值视为未设置）。

### 3.5 产物落点与跨区间口径约定（**R50′**，2026-10-08；N50/N54）

- **产物落点**：一切**批次/验证 run 统一落 `回测产物/runs/`**（`BTF_OUTPUT_DIR` 下；`bt runs` 可列）；
  `_scratch/` 仅作**过程件**（不承诺保留、不作溯源依赖）——引用 run 一律给 `回测产物/runs/<run_id>`。
- **跨区间口径禁混用（N50）**：任何指标引用**必须标注区间**（起止＋交易日数）；**禁止跨区间比较或
  混算**。实例：43 池 **09-23 档**（2019-06-26~2026-09-23，1761 日）`total_return=0.2379` 与审查方
  **09-30 档**（~2026-09-30，1765 日）`0.21924` —— 差异＝**区间口径**（+4 交易日净值回落 1.87pct），
  **不是非确定性**（两档各自逐位可复现）。差异表/报告/对外引用**逐对带 `interval` 字段**（R46′）。

---

## 4. 命令参考

> 统一小节结构：**用途 → 语法 → 参数表 → 行为 → 输出 → 退出码 → 示例 → 注意**。实现锚点见附录 B。

### 4.1 `bt config-check` —— 配置静态校验

**用途**：不跑回测，先回答「这份配置能不能用」：四层合并 → JSON Schema 校验 → 脱敏摘要。

**语法**

```bash
bt config-check <config.yaml> [--dump] [--resolve-strategy]
```

**参数**

| 参数 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `config`（位置） | ✅ | — | 用户配置 YAML 路径 |
| `--dump` | | 关 | 打印合并后**全量配置**；键名含 `token/secret/password/api_key/access_key/credential` 的值替换为 `***` |
| `--resolve-strategy` | | 关 | 额外 `import` `run.strategy` 指向的类并校验其为 `StrategyBase` 子类（类名拼写错误在最低成本环节暴露） |

**行为**

1. L1-L4 四层合并：内置默认 → `BTF_*` 环境变量 → 用户 YAML（本命令无 L4 CLI 覆盖层）；
2. Schema 校验：按 `schema_version` 路由 `schemas/backtest.v1.json`（Draft 2020-12；错误**一次性全量列出**，含 JSON 路径与违规值）；
3. 打印摘要（策略/期间/数据/执行；规则段非空时加一行）；
4. 可选 `--resolve-strategy`：模块可 import + 类属性存在 + 是 `StrategyBase` 子类；
5. 可选 `--dump`：输出脱敏全量配置。

**输出（实测）**

```bash
$ bt config-check examples/config_rotation.yaml
[ok] backtest.v1 校验通过：examples/config_rotation.yaml
     策略: examples.rotation:MonthlyMomentumRotation
     期间: 2020-01-01 → 2022-12-31（初始资金 1000000）
     数据: feed=tushare_parquet | 降级开关 allow_degraded_limit=False
     执行: handler=next_open cost=tiered_v1 slippage=none rebalancer=full
```

校验失败时（stderr）：

```
[error] 配置校验失败（2 处）：
  [1] run.period.start: '2020/01/01' does not match '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'（值 '2020/01/01'）
  [2] data.feed: 'xxx' is not valid under any of the given schemas（值 'xxx'）
```

**退出码**

| 码 | 含义 |
|---|---|
| 0 | 校验通过 |
| 1 | 配置源读取失败（不可读/YAML 语法/顶层非映射）、Schema 违规、`--resolve-strategy` 不可加载 |
| 2 | 用法错误（缺位置参数等，argparse） |

**示例**

```bash
$ bt config-check examples/config_rotation.yaml                  # 日常校验
$ bt config-check config.yaml --resolve-strategy                 # 连策略类一起查（改完策略必跑）
$ bt config-check config.yaml --dump > merged.yaml               # 留档合并后配置（脱敏）
```

**注意**

- 本命令**只做合并 + Schema**，不做装配期硬门：空风控链、区间覆盖度、基准存在性、策略契约版本
  等到 `bt run`（或 `--dry-run`）才暴露——**改配置后建议 `config-check` + `run --dry-run` 各跑一次**；
- `--resolve-strategy` 默认关闭是**向后兼容**承诺：加开只多一道门，不改校验口径。

---

### 4.2 `bt run` —— 执行回测并落盘

**用途**：主链路 `load_config → build（装配）→ run（引擎 + 落盘）`；`--dry-run` 只装配预演。

**语法**

```bash
bt run --config <config.yaml> [--set K=V]... [--dry-run] [--out DIR] [--verbose]
       [--delisted-settlement carry|writeoff]
```

**参数**

| 参数 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `--config` | ✅ | — | 回测配置 YAML |
| `--set K=V` | | — | L4 CLI 覆盖，**可重复**；`a__b=1` 双下划线表嵌套；值按 int → float → str 推断 |
| `--dry-run` | | 关 | 只装配：**不建 run 目录、不执行引擎、不落盘**，打印装配计划 |
| `--out` | | `BTF_OUTPUT_DIR\runs` | 产物根目录（本次 run 目录在其下） |
| `--verbose` | | 关 | 额外打印分步耗时 / 数据质检结论 / 数据指纹 |
| `--delisted-settlement` | | `carry` | **CL-3**：退市持仓残值口径——`carry` 残值按区间内末次可用收盘价**携带**（＝现状，零回归）；`writeoff` **按 0 核销**（压力口径：仅末日快照清零并下调 NAV）；进 `config_effective` 可追溯（等价 `--set run__delisted_settlement=writeoff`） |

**行为**

1. `--set` 解析为嵌套覆盖 dict（语法错误 → 退出码 2，不进入装配）；
2. 四层合并 + Schema 校验（同 `config-check`）；
3. `build()` 装配期硬门（见 §7.4），任一不过 → `[error] 装配失败：...` 且**不建 run 目录**；
4. `--dry-run`：打印装配计划后结束；正式执行：`create_run`（写 `manifest.json` 骨架，状态 `RUNNING`）
   → 引擎日循环 → 快照/成交/拒单/事件落盘 → 指标计算 → `metrics.json` + manifest 补全（`COMPLETED`）；
5. 退市清算口径（**CL-3**，19 号 §72.4）：`--delisted-settlement writeoff` ⇒ 覆盖 `run.delisted_settlement`
   （进 `config_effective`）；区间末按 0 核销退市持仓残值（末日快照市值/权重/数量清零、NAV 下调核销额，
   四式重算保一致；核销基准 = **区间内末次可用收盘价**，不取区间外价——防前视）；披露文案随口径变化
   （三要素保留：标的 / 末次交易日 / 残值口径）。默认 `carry` ＝ 现状（数值零回归）；
6. 打印 `run_id`、产物路径、5 项关键指标；`--verbose` 追加诊断行。

**输出**

正常结束（字段格式取自实现；指标打印 `_fmt_metric` = **与落盘一致的完整精度**，非四舍五入）：

```
[ok] run_id: 20260927_163148_7ecdb4
     产物: D:\量化策略\回测产物\runs\20260927_163148_7ecdb4
     total_return: 0.2804647999999972
     annualized_return: 0.0360318206252046
     sharpe_ratio: 0.532071147084943
     max_drawdown: -0.12982320767202893
     n_fills: 4493.0
```

`--dry-run` 输出（实测）：

```
[dry-run] 装配完成（未执行引擎、未落盘）
     策略: MonthlyMomentumRotation | 标的: 10 只
     期间: 2020-01-01 → 2022-12-31
     组件: handler=NextOpenHandler cost=AShareTieredFeeModel slip=NoSlippage rebalancer=FullRebalancer
     风控: 3 条 | 规则版本: none
```

`--verbose` 追加：

```
     [verbose] 分步耗时: <分步明细>
     [verbose] 数据质检: <结论>（标的 <checked_symbols>，抽样=<sampled_symbols>）
     [verbose] 数据指纹: sha256:<content_hash>（档位 <mode>，水位 <anchor_date>）
```

极短区间告警（**不阻断**）：

```
[warn] 区间仅 3 个交易日（< 20）：部分指标数学有定义但业务无意义，勿用于 optimize --objective 排序（19 号 OBS-1/BB-4）
```

**退出码**

| 码 | 含义 |
|---|---|
| 0 | 成功（含 `--dry-run` 装配成功） |
| 1 | 装配失败（配置校验/契约版本/覆盖度/空风控链/策略加载/数据等） |
| 2 | `--set` 语法错误（如缺 `=`） |

**示例**

```bash
$ bt run --config config.yaml --dry-run                          # 新配置先预演
$ bt run --config config.yaml                                    # 正式跑
$ bt run --config config.yaml --set run.params.top_k=10          # 临时改参数（不改 YAML）
$ bt run --config config.yaml --set run.price_view=nominal       # 旧行为对照档（默认 adjusted）
$ bt run --config config.yaml --out E:\bt_runs --verbose         # 换产物根 + 诊断输出
```

> **`run.price_view`（R62′/N64，2026-10-10；19 号 §118）**：`adjusted`（**默认**）＝喂给策略的
> bar 走 **qfq 后复权**（`ref＝period.start`，PIT 合规；**禁 hfq**）；`nominal` ＝名义价（旧行为，
> 供对照）。**ADR-6 记账口径不变**——撮合/成本/费用/NAV/快照恒名义价。跨视图的 run 数字不可直接比较。

> **`run.strategy_paths`（LL-2，2.0.0 新增配置字段）**：策略实现已出仓（`cc_strategies`），
> `bt run` 须按下述写法装载——`run.strategy` 仍只接受 `module:Class` 点路径（Schema 正则禁止路径符），
> 搜索路径由本字段声明：
>
> ```yaml
> run:
>   strategy: "cc_strategies.qidian_replica:QidianStrategy"
>   strategy_paths: ["D:/量化策略/赤潮/研究"]      # 包的【父目录】，不是包目录
> ```
>
> - 每项须为**绝对路径**（相对路径 ⇒ `ConfigError: run.strategy_paths: 须为绝对路径`）；
> - 指向**策略包的父目录**（`…/赤潮/研究`，其下才是 `cc_strategies/`）；写包目录会 `ModuleNotFoundError`；
> - 路径不存在或不是目录 ⇒ `ConfigError`（fail-closed）；
> - 装载前**幂等注入 `sys.path`（去重、只加不删）**；
> - 顶层包已被别处缓存装载 ⇒ **包名冲突 `ConfigError`**（见 §5.3）；
> - 旧写法 `btf.strategy.<具体策略>` 已删 ⇒ 一律 `ImportError`。

**注意**

- **类型推断不做布尔化**：`--set data.quality.strict=true` 会以**字符串** `"true"` 进入合并 →
  Schema 类型校验**报错**。布尔开关请走环境变量（`BTF_DATA__QUALITY__STRICT=true`）或直接改 YAML；
- **退市清算口径（CL-3；19 号 §72.4 Q-1(a)/Q-2(b)）**：默认 `carry`（残值按区间内末次可用收盘价携带、
  未清算——数值与改动前逐位一致）；`writeoff` 为**压力档**：只在**末日快照**按 0 核销（市值/权重/数量清零、
  NAV 下调核销额；核销基准不取区间外价——防前视）。二者差异**只出现在 writeoff**；披露文案随口径变化；
- 装配期失败**不留半成品 run 目录**；引擎期失败会留下 `manifest.json`（状态 `FAILED` + error），可用 `bt show` 复盘；
- 长回测观测耗时分布用 `--verbose`；失败后先 `bt check` 排除环境问题，再 `--dry-run` 定位装配期硬门。

---

### 4.3 `bt runs` —— 历史 run 清单（只读）

**用途**：一行一个 run 给出 `run_id / 状态 / 时间 / 5 项指标`，免 `ls` 翻目录。

**语法**

```bash
bt runs [--latest N] [--out-dir DIR]
```

**参数**

| 参数 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `--latest N` | | 全部 | 只列最近 N 个（按 run 目录时间倒序） |
| `--out-dir` | | `BTF_OUTPUT_DIR\runs` | RunStore 根目录 |

**行为**

1. 扫描 RunStore 根下一级子目录（无目录 → 打印 `[info]` 提示并返回 0）；
2. 逐行读 `manifest.json`（状态）+ `metrics.json`（5 项关键指标）；
3. 排序：run 目录 mtime + run_id 倒序；
4. **坏产物不静默**：manifest 不可读的行照列，状态退化为 `?`，下一行 `[warn]` 写明原因（fail-visible）。

**输出（实测节选）**

```
run_id                    状态         时间                   指标
20260927_163148_7ecdb4    COMPLETED  2026-09-28 17:30:11  total_return=0.280465  annualized_return=0.0360318  sharpe_ratio=0.532071  max_drawdown=-0.129823  n_fills=4493
20260927_162659_7ecdb4    COMPLETED  2026-09-28 17:30:10  total_return=0.280465  annualized_return=0.0360318  sharpe_ratio=0.532071  max_drawdown=-0.129823  n_fills=4493
```

无产物时：

```
[info] 无 run 产物（先 `bt run --config <yaml>`，或用 --out-dir 指定产物根）
```

**退出码**：0（正常，含无产物）；2（用法错误）。

**示例**

```bash
$ bt runs --latest 5                       # 最近 5 个
$ bt runs --out-dir E:\bt_runs             # 查非默认产物根
```

**注意**：指标为**紧凑显示**（6 位有效数字；`n_fills` 显示为 `4493` 而非 `4493.0`）；需要全精度用 `bt show`。

---

### 4.4 `bt show` —— 单 run 详情（只读，不重算）

**用途**：看一眼这个 run 是什么——manifest 关键字段 + 5 项指标全精度。**不触发指标重算**。

**语法**

```bash
bt show --run <run_id> [--out-dir DIR]
```

**参数**

| 参数 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `--run` | ✅ | — | run_id |
| `--out-dir` | | `BTF_OUTPUT_DIR\runs` | RunStore 根目录 |

**行为**：只读 `manifest.json` + `metrics.json`（不读 snapshots/fills——展示命令不付全量产物读取代价）。

**输出（实测）**

```
run_id      : 20260927_163148_7ecdb4（COMPLETED）
产物目录    : D:\量化策略\回测产物\runs\20260927_163148_7ecdb4
期间        : 2019-06-26 → 2026-09-23（初始资金 1000000.0）
策略        : cc_strategies.qidian_replica:QidianStrategy（仓外；LL-1 已落地）
数据        : feed=mixed 指纹=sha256:unknown
规则版本    : none（overrides 0 条）
配置哈希    : sha256:7ecdb47c2890c1e7d479d69936da6273fe0bcd115efb1b414b7299ce8910cba6
重算锚      : sha256:d34e32749d397aa7ce5ee333ef48d33c137f565b1596970a03f5c55ee7190a16（manifest.metrics_digest；`bt verify` 的对账锚点）
契约版本    : runresult.v1 | 契约基线=1.0
代码版本    : btf==0.2.0（commit unknown）
指标:
     total_return: 0.2804647999999972
     annualized_return: 0.0360318206252046
     sharpe_ratio: 0.532071147084943
     max_drawdown: -0.12982320767202893
     n_fills: 4493.0
```

补充行为：manifest 有 `assembly_notes` 时逐行 `[note] ...`；状态非 `COMPLETED` 时 stderr 提示
`[warn] run 状态 FAILED（非 COMPLETED）：<error>（指标可能不完整）`；不可算指标（NaN/null）打印 `null`（与落盘一致）。

> **样例口径（LL 批已落地，2026-10-24）**：上例策略行按 LL-1 后
> **仓外点路径**改写；历史产物中的旧点路径（LL-1 迁移前的仓内路径）**不改写**，仍按
> 当时实际记录展示。`run.strategy_paths` 用法见 [20 §10.5](20-回测平台使用指引.md)。

> 样例解读：`代码版本` 是**该产物生成时**的版本（此 run 产生于 btf 0.2.0 时代，与当前 `--version`
> 不一致属正常）；`指纹=sha256:unknown` 表示该产物未记录数据内容哈希（无记录时如实显示 unknown，
> 不冒充有值）；`配置哈希` 前 6 位 = run_id 末段 `7ecdb4`。

**退出码**：0；1（run 不存在 / manifest 不可读 / 契约不兼容）；2（用法错误）。

**示例**

```bash
$ bt show --run 20260927_163148_7ecdb4
$ bt show --run 20260927_163148_7ecdb4 --out-dir E:\bt_runs
```

**注意**：`show` 只打印**落盘事实**；要验证「事实没被篡改/口径没漂移」用 `bt verify`（慢，重算）。

---

### 4.5 `bt report` —— 生成 HTML 报告（幂等）

**用途**：由 run 产物再生成 HTML 报告，默认落盘 `run 目录/report.html`。

**语法**

```bash
bt report --run <run_id> [--out <HTML路径>] [--out-dir DIR]
```

**参数**

| 参数 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `--run` | ✅ | — | run_id |
| `--out` | | `<run目录>\report.html` | 输出 HTML 路径（父目录自动创建） |
| `--out-dir` | | `BTF_OUTPUT_DIR\runs` | RunStore 根目录 |

**行为**

1. 读 run 产物（snapshots / fills / manifest / metrics）；
2. 装配「假设与披露」（撮合口径、滑点口径、费率分段、规则版本与覆盖、事件日志降级事件）；
3. 渲染 HTML 写盘；打印路径与字节数。

**输出**

```
[ok] 报告: D:\量化策略\回测产物\runs\20260927_163148_7ecdb4\report.html（123,456 字节）
```

**退出码**：0；1（run 不存在，`FileNotFoundError`）；2（用法错误）。

**示例**

```bash
$ bt report --run 20260927_163148_7ecdb4                       # 默认位置
$ bt report --run 20260927_163148_7ecdb4 --out E:\report.html  # 指定路径
```

**注意**

- **幂等**：同一份产物重复生成内容一致，可安全重跑（清缓存/换机器后重建报告）；
- 报告章节由 `report.sections`（配置层）控制；**「假设与披露」「复现说明」是强制章节**不可关闭——
  报告的版本/指纹/种子披露必然存在；
- 报告**不参与** `bt verify` 对账（对账锚是 manifest 摘要）；删除报告可随时重建。

---

### 4.6 `bt verify` —— R1 复核（重算对账）

**用途**：从落盘产物**重算**全部指标 → 与 `manifest.metrics_digest` 比对，抓「产物被篡改 / 口径漂移」。

**语法**

```bash
bt verify --run <run_id> [--out-dir DIR]
```

**参数**

| 参数 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `--run` | ✅ | — | run_id |
| `--out-dir` | | `BTF_OUTPUT_DIR\runs` | RunStore 根目录 |

**行为**：`snapshots.jsonl + fills.jsonl` → 重算指标 → 计算摘要 → 与 manifest 摘要、落盘 `metrics.json`
摘要**双向比对**（复核路径与生成路径分离，报告/归档后仍可自校验）。

> **复核路径的折算来源（R61′/N63，2026-10-10）**：逐笔口径（`trades`/`win_rate_trades`）配对需感知份额折算，
> 复核侧**从 `events.jsonl` 的已应用 `corporate_action` 还原**（与落盘路径**同源**——`result.consolidations` 同集）；
> **采样降级**日志（超预算 stride 抽 1＋尾部 1000 全保）可能缺事件 ⇒ 该场景会打 **WARN** 且摘要可能不符
> （已知边界，登记在案；**禁静默**）。
>
> **价格视图与复核（R62′/N64，2026-10-10）**：视图是**指标口径**的一部分（进 `manifest.config_effective` ⇒ 进
> config_hash）；复核按产物配置重算，**跨 `price_view` 的摘要不可比**（`nominal` 档用于回归比对）。

**输出（实测）**

```
run_id      : 20260927_163148_7ecdb4（COMPLETED）
快照 / 成交 : 1761 / 4493
重算 digest : sha256:d34e32749d397aa7ce5ee333ef48d33c137f565b1596970a03f5c55ee7190a16（一致）
落盘 digest : sha256:d34e32749d397aa7ce5ee333ef48d33c137f565b1596970a03f5c55ee7190a16（一致）
manifest    : sha256:d34e32749d397aa7ce5ee333ef48d33c137f565b1596970a03f5c55ee7190a16
[ok] R1 复核通过：重算指标与落盘指标均与 manifest 摘要一致
```

（上例三行 digest 相同 = 重算 / 落盘 / manifest 三方一致；该值与 `bt show` 的「重算锚」一致。）

不一致时（stderr）：

```
[fail] R1 复核失败：摘要不一致（产物被篡改或口径漂移）
```

**退出码**：0（一致）；1（不一致 / run 不存在）；2（用法错误）。

**示例**

```bash
$ bt verify --run 20260927_163148_7ecdb4        # 审计/归档前复核
```

**注意**：比 `show` 慢（读全量 snapshots/fills 并重算）；建议**周期性抽查**。复核失败先查：
产物是否被手改、`btf` 在 run 之后是否改过口径（对照 `manifest.code_version`）。

---

### 4.7 `bt optimize` —— 参数网格搜索

**用途**：对配置中若干参数位做笛卡尔积扫描，每个组合一次回测，按目标指标排名。

**语法**

```bash
bt optimize --config <config.yaml> --grid "<spec>" [--objective X] [--workers N]
            [--top N] [--n-trials N] [--perturb D1,D2] [--allow-short] [--out FILE]
```

**参数**

| 参数 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `--config` | ✅ | — | 回测配置 YAML（基准配置） |
| `--grid` | ✅ | — | 网格 spec：`path=v1,v2;path2=v1,v2`（见下） |
| `--objective` | | `sharpe_ratio` | 排名指标名（不做白名单：任何出现在指标集里的名字都接受；配 `report.benchmark` 后**相对指标** `information_ratio` / `annualized_excess` 可一起排序——**RC-08**，经 `extras`） |
| `--workers` | | `8` | 进程数；`0` = 内联顺序执行（调试 / 注入型 feed 用） |
| `--top` | | `10` | 打印前 N 名 |
| `--n-trials` | | `0` | 历史累计试验数（**RC-06**）：与本次组合数相加为 N，用于 DSR 多重检验校正 |
| `--perturb` | | 不扫描 | 参数邻域扫描（**RC-07**）：对 top1 **数值型**参数做 `p×(1±δ)`；如 `0.1,0.2`（δ ∈ (0,1)，可多个）；相对退化 > 50% 打印"疑似过拟合"提示 |
| `--allow-short` | | 关（阻断） | **CL-1**：显式放行「样本 < 250 交易日」的排序（默认阻断、退出码 3）；放行把 `n_days/阈值/override` 记入结果 JSON 的 `short_period_override`（**禁「绕过即静默」**） |
| `--out` | | 不写 | 结果 JSON 输出路径（父目录自动创建） |

**`--grid` spec 语法**

- 分号 `;` 分隔多个参数位；每个参数位 `点分配置路径=值1,值2,...`（如 `run.params.top_k=3,5,10`）；
- 取值类型按 **int → float → str** 推断（与 `--set` 同源）；
- 空 spec / 空取值列表 → 报错退出码 2。

**行为**

1. 解析 grid（语法错 → 退出码 2）＋ 解析 `--perturb`（δ 越界 / 空 ⇒ 退出码 2）；读取基准配置（读失败 → 退出码 1）；
2. 经注册表取 `grid_search` 优化器，进程池并行跑全部组合（每组合 `persist=False`，**不落 run 产物**）；
3. **短区间门禁（CL-1 / 报告 D 篇 P2-5）**：实算样本 `n_trading_days < 250`
   （`MIN_OPTIMIZE_TRADING_DAYS`，≈1 个完整交易年）⇒ **默认阻断**：打印阈值 / 当前 `n_days` / 去路，
   **退出码 3**，且**不打印任何排序结论**（短样本下指标"数学有定义、业务无意义"）；
   显式 `--allow-short` 放行（打印 `[warn]`，并把 `short_period_override = {n_days, threshold,
   override: true}` 记入结果 JSON）；`n_days ≥ 250` ⇒ 行为与改动前一致（**零回归**）；
4. 按 objective 降序排名（指标缺失/NaN 按 `-inf` 垫底，不剔除记录）；每组合给出 `psr` / `dsr`，
   并打印 **N 与 `0.05/N` 阈值**的校正说明（**RC-06**："试得越多越不可信"）；
5. 传 `--perturb` ⇒ 对 **top1** 的数值型参数做邻域扫描（极差 / σ / 相对退化率；退化 > 50% ⇒
   **warn"疑似过拟合"，只提示、不替使用者判定**——**RC-07**）；
6. 打印 `n_combos × workers` 概览 + 前 `--top` 名；`--out` 写全量结果 JSON。

**输出（示意；数值随配置而变）**

```
[ok] grid_search：9 组 × 8 进程 | objective=sharpe_ratio
[warn] N=9 未校正 —— 阈值应 ≤ 0.05/N = 0.005556（PSR=单策略显著性；DSR=校正「挑出来」之后的显著性）
rank       value     psr     dsr  params
   1      1.2345   0.981   0.874  run.params.lookback=20, run.params.top_k=5
   2      1.1021   0.932   0.781  run.params.lookback=40, run.params.top_k=5
[info] perturb：14 点 | 极差=0.3521 | σ=0.0987 | 相对退化=28.5%
     结果: E:\grid_result.json
```

**退出码**：0；1（配置读取失败）；2（`--grid` 解析失败 / 用法错误）；
**3（短区间默认阻断——CL-1；`--allow-short` 显式放行）**。

**示例**

```bash
$ bt optimize --config config.yaml \
              --grid "run.params.top_k=3,5,10;run.params.lookback=10,20,40" \
              --objective sharpe_ratio --workers 8 --top 10 \
              --out E:\grid_result.json

$ bt optimize --config config.yaml --grid "run.params.top_k=5" --workers 0   # 单组合内联调试
```

**注意**

- 结果 JSON 结构：`objective / n_combos / param_dim / n_trading_days / n_trials_declared / n_trials /
  adjustment_note / workers / perturbation（传 `--perturb` 时）/ results[]（每组合 params+metrics+rank+psr+dsr）/ top[]`。
  `--out` 写**全量记录**；`--top` 只影响屏幕打印；
- `--perturb` 的 δ 为**相对比例**（`p×(1±δ)`），仅作用于 top1 的**数值型**参数；δ 越界（≤0 或 ≥1）⇒ 退出码 2；
- `--n-trials` 把**历史**试错次数并入 N（DSR 校正）；不写 = 只按本次组合数；
- **极短区间污染排名**：区间太短时部分指标「数学有定义、业务无意义」，排名不可信——**CL-1 起
  `< 250 交易日` 默认直接阻断（退出码 3）**；例外放行走 `--allow-short`，且 override 只记「**实际放行**」
  （长区间加开关不写标记——不得恒现）；
- 组合为**独立 run**（不共享随机状态），可复现性由 `seed.master` 保证；
- `--workers 0` 用于 `memory` feed 或调试；负数进程数非法。

---

### 4.8 `bt dataset` —— 黄金集（构建 / 对账 / 重算）

**用途**：回归测试的黄金数据集三条路——构建写盘、与主库对账、只用案例内嵌数据独立重算；外加**数据冻结清单**（`freeze`，CL-4 / MM-7）：对回测所用区间落一份只读、不可变的「表清单＋content_hash＋anchor_date」快照。

**语法**

```bash
bt dataset [build-golden|verify|refcalc] [--cases g1|all|id,id] [--list] [--verify] [--force] [--out DIR]
bt dataset freeze --config <config.yaml> [--mode meta|fast|full] [--out DIR]     # CL-4；产物 <anchor_date>.json（只读）
```

**参数**

| 参数 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `action`（位置） | | `build-golden` | 子动作（choices）：`build-golden` / `verify` / `refcalc` / `freeze`（CL-4） |
| `--cases` | | `g1` | 案例选择：单组（`g1`）、`all`、逗号分隔 case_id |
| `--list` | | 关 | 列出案例与待补组（**不写盘**） |
| `--verify` | | 关 | 只校验不写（等价 `action=verify`） |
| `--force` | | 关 | 显式覆盖已构建案例（留痕） |
| `--config` | | — | **freeze**：回测配置 YAML（冻结口径随它：区间 / 表集 / 档位）；缺 ⇒ 退出码 2 |
| `--mode` | | 随 `data.fingerprint_mode`（`meta`） | **freeze**：指纹档位 `meta|fast|full` |
| `--out` | | `BTF_DATASETS_DIR\golden_v1\cases`（`freeze`：`BTFTOOL_FREEZE_DIR`） | 输出目录；`freeze` 在该目录落 `<anchor_date>.json` |

**动作语义**

| 动作 | 读主库 | 写盘 | 判据 |
|---|---|---|---|
| `build-golden` | ✅ | ✅ | 从主库构建/更新案例文件；断言漂移 → FAIL |
| `verify` | ✅ | ❌ | 现有案例与主库对账（主库变了/案例陈旧即暴露） |
| `refcalc` | ❌ | ❌ | 只用**案例文件内嵌数据**独立重算断言（黄金集自洽性） |
| `freeze`（**CL-4**） | ✅（只读指纹） | ✅（**只读清单** `<anchor_date>.json`） | 落「表清单（名＋行数＋日期域）＋ content_hash ＋ anchor_date」不可变清单（S-2；19 号 §72.5）；`content_hash` 与 `manifest.data_version` / RC-14 门禁**同源**（BB-1 实算）；同 `anchor_date` 重复冻结 ⇒ **幂等**（哈希一致**不覆写**，返回 `action=idempotent`）/ **显式冲突**（哈希不同 ⇒ `FileExistsError`，退出 1，不覆写不追加版本）；内存 Feed 拒绝冻结（退出 1，不产假清单）；MM-7：冻结时**进程内**临时放开 `data.dataset.{expected_hash,version}`（不落盘、不改配置），已钉旧指纹不再反噬 |

**`freeze` 产物（清单契约 `freeze.v1`）**

| 字段 | 含义 |
|---|---|
| `anchor_date` | 数据水位（表内最大日期，未裁剪）——**文件名即 `<anchor_date>.json`** |
| `tables[]` | 每表 `name` / `rows` / `date_min` / `date_max`（表集与 RC-14 门禁**同源**） |
| `content_hash` | 内容哈希（档位＝`--mode` / `data.fingerprint_mode`），与 `manifest.data_version` / RC-14 同源 |
| `mode` | 指纹档位 `meta` / `fast` / `full` |
| `created_at` / `period` / `root` / `btf_version` / `schema` | 生成时刻 / 配置区间 / 主库根 / 版本 / 契约 `freeze.v1` |
| `validity` | S-2 有效边界注（原始库回填/重算后旧清单不可自证） |

落点：`--out` 指定目录，缺省 `BTFTOOL_FREEZE_DIR`（`代码\_freeze\`）；写入后**置只读**（`S_IREAD`），不随回测覆写。

**输出（`freeze`，字段格式取自实现；数值为结构示例）**

```
[ok] 冻结清单已落盘（只读）：D:\量化策略\代码\_freeze\<anchor_date>.json
     anchor_date=<YYYY-MM-DD> | mode=fast | tables=<N> | content_hash=sha256:<...>
```

幂等（同 anchor 同哈希，不覆写）：

```
[info] 冻结清单已存在且 content_hash 一致（幂等，不覆写）：<path>
```

冲突（同 anchor 异哈希，stderr）：

```
[error] 冻结冲突：<anchor_date>.json 已存在且 content_hash 不一致——旧 <hash> / 新 <hash>；S-2 不静默覆写（R3：显式冲突报错），请先归档旧清单
```


**输出（`--list` 实测节选）**

```
g10_vpp_partial_fill     G10  built    VPP 部分成交：601599.SH 20191108 vol=30000 × 5% = 1500 股 → 10 万股委托部分成交，余量当日取消
g1_cash_div_ex_ne_pay    G1  built    派息 ex≠pay（600000.SH 2012）：ex 日缺口 / pay 日到账，窗口合并 Δ=0
g1_cash_div_same_day     G1  built    派息且 pay≡ex（2020+ 常态）：单日 Δ=0，avg_cost 摊薄
...（其余 13 个案例，按 case_id 字典序，略）
-- pending（未实现，显式的）--
```

**案例清单（16 个，`--list` 为准）**

| 组 | case_id | 场景 |
|---|---|---|
| G1 | `g1_cash_div_same_day` / `g1_cash_div_ex_ne_pay` / `g1_stk_div_mixed` | 派息 pay≡ex / ex≠pay / 送转+派息混合 |
| G2 | `g2_limit_up_buy_rejected` / `g2_limit_down_sell_rejected` | 涨/跌停拒单 |
| G3 | `g3_suspension_buy_rejected` | 停牌期拒单、复牌可成交 |
| G4 | `g4_t1_same_day_sell_rejected` / `g4_t0_etf_same_day_roundtrip` | T+1 拒单 / T+0 ETF 同日回转 |
| G5 | `g5_st_interval` | ST 状态区间面板 |
| G6 | `g6_delist_last_day` | 退市末日出清 |
| G7 | `g7_lot_and_min_commission` / `g7_fee_segments_pre2015` | 整手/最低佣金 / 费率分段 |
| G8 | `g8_calendar_spring_festival` | 春节日历缺口 |
| G9 | `g9_dividend_chain_hfq` | 复权链守恒 |
| G10 | `g10_vpp_partial_fill` / `g10_vpp_volume_cap_rejected` | 部分成交 / 量上限拒单 |

**退出码**

| 码 | 含义 |
|---|---|
| 0 | 动作完成且断言全部通过（含 `--list`、`freeze` 新建/幂等） |
| 1 | 断言漂移（`[FAIL] ...`）；**`freeze` 冲突**（同 anchor 异哈希，`FileExistsError`）/ 内存 Feed 拒冻 / 配置装配或主库不可用 |
| 2 | 未知动作 / 主库不可用（路径不存在）/ 工具脚本缺失 / **`freeze` 缺 `--config`** |

**示例**

```bash
$ bt dataset --list                              # 看案例与状态
$ bt dataset                                     # 构建 g1（默认）
$ bt dataset build-golden --cases all            # 全量构建
$ bt dataset verify --cases g1                   # 与主库对账（不写）
$ bt dataset refcalc --cases all                 # 内嵌数据重算（不读主库）
$ bt dataset build-golden --cases g1 --force     # 显式覆盖（留痕）
$ bt dataset freeze --config config.yaml         # CL-4：冻结点（默认 meta 档）
$ bt dataset freeze --config config.yaml --mode fast --out E:\freeze   # 显式档位/目录
```

**注意**

- `--list` 与 `--out` 同时给出时 `--out` 被忽略（列清单不写盘）；
- 构建产物是**版本化数据集**（`golden_v1`，契约 `golden.v1`）；覆盖必须显式 `--force`，防误伤；
- `refcalc` 不依赖主库——数据盘不可用时仍可做**黄金集自洽性**检查；
- **`freeze`（CL-4）**：产出的清单文件**只读**（`BTFTOOL_FREEZE_DIR`，默认 `代码\_freeze\<anchor_date>.json`），
  **不得**被后续回测覆写；⚠ **S-2 有效边界**：只冻「清单＋哈希」，**依赖原始库不被改写**——原始库回填/重算后
  旧清单不可自证（登记要求见 08 号与 [20 号纪律](20-回测平台使用指引.md)）。
- **`freeze` 幂等/冲突**：同 `anchor_date` 且 `content_hash` 一致 ⇒ 幂等（**不覆写**、退出 0）；同 anchor 异哈希
  ⇒ **显式冲突**（退出 1），**不静默覆写、不追加版本**——需先归档旧清单。
- **MM-7 自锁修复（2.0.0）**：配置里已钉 `data.dataset.expected_hash` / `version` 时，冻结入口在**进程内**
  临时放开这两个键（只在内存、不落盘、不改配置、不影响 `bt run`），故换档（meta→fast）或重冻结可用。
- **研究批次**：仓外 `scripts/freeze_batch.py --batch <batch_id> [--apply]` 逐份冻结并生成
  `freezes/<batch>/index.json`；`--apply` **只改** `data.fingerprint_mode` 与 `data.dataset.expected_hash`
  两处，任一失败整批退非零、不写 index（流程见 [20 §10.6](20-回测平台使用指引.md)）。

---

### 4.9 `bt test` —— 运行测试层（pytest 执行器）

**用途**：按测试层标记跑 pytest 回归。**提交前门禁请用 `bt check`**（两者分工见下）。

**语法**

```bash
bt test [--layer l1|l2|l3|l4|l5|all] [--pattern EXPR] [--path PATH]
```

**参数**

| 参数 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `--layer` | | `l1` | 测试层标记（见下表）；`all` = 不加层过滤（全量） |
| `--pattern` | | — | pytest `-k` 表达式（用例名过滤） |
| `--path` | | — | 测试路径（默认全量 `tests/`） |

**测试层定义**

| 标记 | 含义 |
|---|---|
| `l1` | 单元测试（秒级，提交门；**默认**） |
| `l2` | 集成测试（MemoryFeed 组合行为） |
| `l3` | 系统测试（端到端对账） |
| `l4` | 回归测试（黄金集 + 双跑 diff） |
| `l5` | 性能基准（B1-B5） |
| `smoke` | 冒烟（中文路径/环境自检；`bt check` 第 3 项） |

**行为**：以**子进程**跑 `python -m pytest -q -p no:cacheprovider --basetemp <项目内临时目录>`；
basetemp 默认建在 **`代码/_scratch/pytest_tmp/bt_test/`** 下的**每次唯一**子目录（**N6 分根**：与门禁第 3 项
`…/pytest_tmp/check` 各用独立子根；根可经 `BTFTOOL_TEST_PYTEST_BASETEMP` 覆盖）——**不走 `%TEMP%`**
（**N5-②**，2026-10-06：系统临时区的 pytest 垃圾轮转可能被环境批量删除守卫拦截 ⇒ "用例全过但 exit≠0"假红）；
cwd 固定为仓库根；剥离 `PYTEST_CURRENT_TEST` 环境变量；结束后**分小批**回收自建临时目录（被守卫拦则打
`[note]` 保留现场，**退出码不变**）。命令回显 `[run] ...`。

**退出码**：**原样透传 pytest 码**——`0` 全通过；`1` 有失败；`2` 中断；`5` 无用例。

**示例**

```bash
$ bt test                                        # l1（秒级，日常）
$ bt test --layer l4                             # 黄金集回归
$ bt test --layer all                            # 全量（约 64 分钟，慎用）
$ bt test --pattern test_manifest --path tests/  # -k 过滤 + 指定路径
$ bt test --layer all --pattern smoke            # 只跑冒烟集
```

**注意**

- 默认 `l1` 是**刻意保守**：全量回归耗时约 1 小时，需显式 `--layer all`；
- basetemp 落**项目内独立子根** `_scratch/pytest_tmp/bt_test/`（**N5-②/N6**，**每次唯一子目录**——复用已有大目录
  会被守卫拦成假 ERROR）；**直接手跑 `pytest tests` 时请自加 `--basetemp <项目内路径>`（目录每轮新起、另起子目录）**
  （**N5-①/③** 纪律，见 [09 §14.1](09-测试验证与基准.md)）；
- 回收被守卫拦时打 **`[note] basetemp 未完全回收（环境批量删除守卫）`**（**N9**；19 号 §71.3）——属**已知
  fail-safe、非用例失败**（退出码仍 ＝ pytest 码），但**须清理**（**判据 D**，见 [09 §14.1](09-测试验证与基准.md)）；
- 与 `bt check` 分工：`check` = 提交前十项门禁（含 lint / 架构契约 / 数据不变量，一体跑完）；
  `test` = 按层跑 pytest 回归。**日常改完代码：`bt check` → `bt test`**。

---

### 4.10 `bt check` —— 十项架构门禁

**用途**：提交前必跑的一体化门禁；与 `python tools/check.py` **同一脚本、同一退出码**。

**语法**

```bash
bt check
```

**参数**：**无**（刻意如此——加开关等于允许「跑得不一样」，口径唯一优先）。

**十项清单**

| # | 项 | 守护的纪律 |
|---|---|---|
| 1/10 | ruff lint | 代码规范 + 随机源禁令 |
| 2/10 | import-linter 架构铁律 | 六铁律分层依赖 |
| 3/10 | 冒烟测试（中文路径/环境） | 部署环境自检 |
| 4/10 | 依赖预算 | 外部依赖 ≤ 8 |
| 5/10 | 包可导入 | 骨架完整性（`import btf`） |
| 6/10 | domain 白名单 | `btf/domain` 仅标准库 |
| 7/10 | 报告渲染级结构 | 渲染/序列化产物断言 |
| 8/10 | 数据不变量校验 | 主库正样本 + 负向对照 |
| 9/10 | IO 单一入口（+变异性自检） | 磁盘访问收敛点唯一 |
| 10/10 | 插件边界基线 | 核心包 diff 为零（GG-6② 并入门禁；刷新须显式 `--update --reason` 留痕） |

**输出**：每项 `=== N/10 ... ===` / `--- PASS|FAIL ---`；末尾汇总。

```
bt check: 10/10 通过
```

**退出码**：0（10/10）；1（有失败项）；2（工具脚本缺失）。

**诊断（N4 加固，2026-10-06）**

- 每项输出**自动落盘**：`代码/_scratch/bt_check_logs/<时间戳>/<项>.attemptN.log`（`BTFTOOL_CHECK_LOG_DIR` 可覆盖）——
  偶发假红自此**可归因**（此前两次冷启动假红均因无失败日志无法定论）；
- **第 3 项（冒烟）冷启动偶发假红**（19 号 §67.3 N4 / §67.9：**两次独立复现**）：**自动复跑一次**并在输出中显式标注
  「**首次失败 / 复跑通过——N4 冷启动偶发，已留痕**」，运行末汇总再打印一次；**其余项失败一律按真失败**（不复跑）。

**示例**

```bash
$ bt check          # 提交前 / 定版前
```

**注意**：任何一项 FAIL 都要修到全绿再提交；该项判定逻辑不复制到 CLI（单一真源在 `tools/check.py`）。

---

### 4.11 `bt cold-backup` —— 跨版本源码冷备

**用途**：btf 非 git 仓库，定版/重要节点用冷备留痕：源码树 + 逐文件 sha256 + 还原说明。

**语法**

```bash
bt cold-backup [--reason <依据>] [--out <备份根>] [--verify <冷备目录>]
```

**参数**

| 参数 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `--reason` | | `手动冷备` | 备份依据（写入 MANIFEST；定版请写明确，如 `v1.1.0 定版`） |
| `--out` | | `D:\量化策略\备份` | 备份根目录 |
| `--verify DIR` | | — | **校验既有冷备**（逐文件 sha256 复算，与新建互斥） |

**行为**

- 新建：拷 `btf / tests / tools / schemas / examples / pyproject.toml / CHANGELOG.md / README.md / docs`（排除
  `__pycache__ / .pytest_cache / *.pyc / runs / dist / build` 等派生目录）→ 写 `MANIFEST.json`（版本/时间/依据/逐文件哈希/计数）
  + `SHA256SUMS.txt`（外部可 `sha256sum -c`）+ `COLD_BACKUP.md`（含还原步骤）→ **自校验**一遍；
- 目录名：`<备份根>\<YYYYMMDD-HHMM>-btf-<version>-cold\`；目标已存在 → 拒绝（防覆盖）；
- `--verify`：复算全部哈希与 MANIFEST 比对（防介质损坏）。

**输出**

```
[ok] 冷备完成：D:\量化策略\备份\20260930-1509-btf-1.1.0-cold（213 文件 / 2031 KB）
[ok] 自校验通过（逐文件 sha256 一致）
```

**退出码**：0；1（哈希不一致 / 自校验失败）；2（工具脚本缺失 / 参数用法错误）。

**示例**

```bash
$ bt cold-backup                                      # 快速留痕（默认依据「手动冷备」）
$ bt cold-backup --reason "v1.1.0 定版"               # 定版冷备
$ bt cold-backup --out E:\backup --reason "迁移前"    # 指定备份根
$ bt cold-backup --verify "D:\量化策略\备份\20260930-1509-btf-1.1.0-cold"   # 校验既有冷备
```

**注意**：fail-closed——任一文件哈希失败 / 源缺失即退出非零，**不产出半份备份**；`--verify` 与新建互斥
（给了 `--verify` 时 `--reason`/`--out` 被忽略）。

---

### 4.12 `bt validate` —— 滚动验证 / 蒙特卡洛（RC-15/16）

**用途**：L4 推断层。`walk-forward` 做**滚动 / 锚定**样本外验证（含 purge / embargo 隔离）；
`mc` **读回既有 run 产物**（不重跑引擎）做 bootstrap 置信区间 / 置换检验 / 噪声基线。

**语法**

```bash
bt validate walk-forward --config <yaml> --window N [--test N] [--step N] [--mode rolling|anchored] \
                         [--purge N] [--embargo N] [--no-persist] [--set K=V] [--out F] [--out-dir DIR]
bt validate mc --run <run_id> [--n N] [--seed N] [--out F] [--out-dir DIR]
```

**参数**

| 参数 | 适用 | 说明 | 缺省 |
|---|---|---|---|
| `action`（位置） | 两者 | `walk-forward` \| `mc` | 必填 |
| `--config` | wf | 回测配置 YAML | 必填 |
| `--window` / `--test` / `--step` | wf | 训练窗长 / 测试窗长 / 滚动步长（交易日）；`--test` 缺省 = `--step`，`--step` 缺省 = `--test` | — |
| `--mode` | wf | `rolling`（滚动）/ `anchored`（锚定加长） | `rolling` |
| `--purge` / `--embargo` | wf | 训练/测试间隔与测试前缓冲（交易日；合并计入 gap） | 0 |
| `--no-persist` | wf | 不落盘（缺省每折落盘，窗口信息入 manifest） | 落盘 |
| `--set K=V` | wf | 配置覆盖（与 `bt run` 同则） | — |
| `--run` | mc | run_id（读回产物，**不重跑引擎**） | 必填 |
| `--n` | mc | 重采样 / 置换次数 | 1000 |
| `--seed` | mc | 显式种子（缺省 = `manifest.seed.master` 单源） | manifest |
| `--out` / `--out-dir` | 两者 | 结果 JSON 路径 / RunStore 根 | — |

**输出（实测，2026-10-06；`python -m btf.cli.main validate mc --run 20261001_182705_3ae6f2 --n 200`）**

```
[ok] mc：run=20261001_182705_3ae6f2 样本 5039 日（年化 252）| seed=20260926（manifest）
bootstrap（200 次重采样）:
     sharpe: observed=0.6240193727017235 p05=0.2818512126386722 p50=0.6566383779678984 p95=1.0017285537415197 CI=[0.17460746598666502,1.0456754369812271] 跨0=False
     annualized_return: observed=0.08846160612268195 p05=0.031226509204007824 p50=0.09423947533679788 p95=0.16478184284444808 CI=[0.016506895809841327,0.17451985379043222] 跨0=False
置换检验[sign/sharpe] 双侧: observed=0.6240193727017235 p=0.01（null 200 次）
置换检验[order/max_drawdown] 双侧: observed=-0.4463542572131344 p=0.105（null 200 次）
[warn] 无基准（report.benchmark 未配）⇒ 随机择时对照不可得——null 而非伪造；配基准后可用（RC-16）
```

**退出码**：0；1（配置 / 产物不可读、样本不足等）；2（参数用法错误）。

**注意**

- `walk-forward` 每折**独立回测并落盘**（走 RunStore）；`--no-persist` 关闭落盘；
- `mc` **只读产物**：缺基准时**如实 warn**（"无基准对照"），**不伪造**随机择时对照（RC-16 纪律）；
- 推断结果是**解释层**，**不改变** `metrics.json` 任何指标，也不得直接回写为策略结论（20 号纪律一~三）。

---

### 4.13 `bt data` —— 数据专项扫描（R34′ 已交付，2026-10-08；DOC-12）

> ✅ **已交付**（CL-2-H 第二阶段/R34′，2026-10-08；19 号 §97.3/§97.5-1 实跑取证——本机
> `510310.SH 20240923 R=2.000625` 与 §92.3 文档锚点逐位吻合）。本条目**已计入** §2 命令总表
> 与「13 命令」计数；退出码 0/1/2 已实测（含 `--config` 缺失与未知动作的用法拦截）。

**用途**：数据层专项扫描（首个子命令 `scan-consolidation`）：对配置宇宙逐标的检出 **ETF/LOF 份额折算**事件，
**双证据互校**（证据 1＝`pre_close(t) ÷ close(t−1)`；证据 2＝`fund_adj` 因子跳变），容差 **0.5%**，
不符 ⇒ **fail-closed**（不得静默取其一）；供报告披露与装配期门禁消费（ADR-20 第 5/6/8 条）。

**语法**

```bash
bt data scan-consolidation --config <yaml> [--out <json>] [--tolerance 0.005]
```

**参数**

| 参数 | 说明 | 缺省 |
|---|---|---|
| `action`（位置） | `scan-consolidation`（数据专项扫描，后续可扩） | 必填 |
| `--config` | 配置（取 `run.universe` 与区间） | 必填 |
| `--out` | 结果 JSON 路径 | 产物目录 |
| `--tolerance` | 双证据偏差容差 | `0.005`（0.5%） |

**输出（字段）**：逐事件 `ts_code / 事件日 / R / 证据1 / 证据2 / 偏差 / 结论（一致｜fail-closed）`；
汇总含**未建模项清单**（供报告披露段消费）。

**退出码**：0（全部一致）；1（存在 fail-closed 事件——按门禁语义阻断）；
2（参数用法错误 ／ **前置降级**——如 `trade_cal` 缺失 ⇒ 告警＋退出码 2，**不得未捕获异常**；R47′）。

**注意**

- **判据为双证据＋容差 0.5%＋fail-closed**；**禁止使用 `fund_share` 判定折算**（ADR-20 第 6 条，仅辅助披露）；
- **分红/折算交叉判别（R43′，2026-10-08；N46）**：分红与折算在双证据上数学同构（除息日
  `f_t=f_{t−1}÷R`），故**候选日与 `fund_div` 除息日交叉 ⇒ 判为分红、不计折算**（语义优先于
  幅度阈值；全库可比分红 >12% 共 218 笔、最高 70.47%——误判将致份额与现金双错）；
- **扫描路径不依赖日历（R47′；N51）**：交叉判别只需 `(ts_code, ex_date)` 集合 ⇒ 本命令
  **不读 `trade_cal`**（缺日历仅令 `n_trading_days` 降级为 `null`）；`fund_div` **目录缺失/文件空**
  均走**禁静默告警**（R44′/R48′；N52——`peek_columns` 的 `ReaderError` 已捕获、等同表缺失）；
- **交叉防线 fail-safe（R51′；N55）**：`fund_div` 表**缺失/不可读** ⇒ 报告置
  `cross_disabled=true` 并进 `degraded` ⇒ **退出码 2**（与 `trade_cal` 同强度——折算候选
  **未经除息判别**不得按成功结果引用）；**「有行但过滤后为空」**（`div_proc`/`div_cash<=0`
  全滤）⇒ **三元计数披露**（原始行数／过滤跳过数／最终入账数），消除原零告警静默；
- 扫描器**进 CI**（R34′ 验收口径）；
- 输出直接供报告「假设与披露 · 公司行动覆盖段」消费（20 号纪律四）；
- 实测锚点（8 只折算标的，R 双证据偏差 < 0.1%，19 号 §92.3）：510310＝2.0006／512200＝2.7937／
  515880＝0.33323＋**0.50032**／515580＝0.51003（复现脚本 `_scratch/audit_etf_corp_action.py`；二次值经 DOC-13 订正——
  2026-10-07，N39：原引 0.50013 系早期估计，实测 `pre_close÷前收` ＝ **0.50032**，容差内但以实测为准）。

---

### 4.14 `bt adhoc` —— 一次性/外部脚本的产物契约封装（HH-3，2026-10-14）

**用途**：给**非 btf 引擎**的回测脚本（如 round2 外部方法论复现）套同一套产物契约
（`manifest.json` 与真入口 run 同结构：脚本指纹 / 参数哈希 / 数据锚 / 代码版本 /
产物清单 + sha256 / 重算命令）——**不要求改造脚本本身**（目录快照差分法）。
**HH-3（19 号 §128）**：`tools/adhoc_run.py` 收编为 CLI 子命令，入口统一、功能不变。

**语法**

```bash
bt adhoc --name <实验名> --script <脚本> [--params p.json] [--tables t1,t2]
         [--as-of YYYYMMDD] [--watch-dir <目录>] [--out <登记台根>]
         [--register-only | --list] -- <脚本自己的参数>
```

**行为**

- **执行登记**（默认）：快照监视目录（默认脚本所在目录）→ 子进程跑脚本 → 再做一次快照，
  差集即本次产物（逐文件 sha256）→ 写 `manifest.json` 并追加登记台 `registry.jsonl`；
- **`--register-only`**：不执行脚本，把监视目录里**现有**产物全部登记（存量脚本用）；
- **`--list`**：列出登记台；脚本参数经 `--` 分隔**原样透传**（bt 侧 REMAINDER 兼容）。

**语法演示（round2 一条脚本签名）**——脚本依赖原机路径、本机执行不可用，取登记路径演示：

```bash
$ bt adhoc --name qa-round2-register --register-only \
    --script "…\赤潮\_archive\…\round2\debug_lower_search.py" \
    --watch-dir "…\round2\probe" --tables daily,index_daily --as-of 20260923
[adhoc] COMPLETED | run_id=20261014_221628_4d4f9e
   产物 17 个（首 5）…  manifest → …\adhoc\qa-round2-register\<run_id>\manifest.json
$ bt adhoc --list
 20261014_221556_820fee  COMPLETED qa-rrg-demo              产物   1  …
 20261014_221628_4d4f9e  COMPLETED qa-round2-register       产物  17  …
```

**退出码**：0；1（脚本非零退出 / 登记失败）；2（工具脚本缺失 / 参数用法错误）。

**注意**：委托 `tools/adhoc_run.py`（单一真源；CLI 只转发不做第二实现）；
登记台根默认 `回测产物\adhoc\`。

---

## 5. 退出码与错误处理约定

### 5.1 退出码总表

| 码 | 通用含义 | 典型命令 |
|---|---|---|
| `0` | 成功 / 检查通过（含「无数据可列」这类正常空结果） | 全部 |
| `1` | **业务失败**：校验失败 / 装配失败 / 复核不一致 / 对账漂移 / 哈希失败 / run 不存在 | `config-check` `run` `report` `verify` `dataset` `check` `cold-backup` `adhoc` |
| `2` | **用法与前置错误**：argparse 用法错误、`--set`/`--grid` 语法错、未知 dataset 动作、工具脚本缺失 | 全部 |
| `3` | **策略性阻断**：`optimize` 短区间（样本 < 250 交易日）默认拒绝给出排序结论（**CL-1**；`--allow-short` 可显式放行） | `optimize` |
| pytest 码 | 原样透传（0/1/2/5） | `test` |

> 脚本化建议：把 `1` 视为「看 stderr 的业务原因」，`2` 视为「调用方写错了命令行」——前者重试/修配置，
> 后者修脚本。

### 5.2 输出前缀约定

| 前缀 | 流向 | 含义 |
|---|---|---|
| `[ok]` | stdout | 操作成功 |
| `[info]` | stdout | 提示（如无 run 产物，非错误） |
| `[warn]` | stdout / stderr | 告警**不阻断**（短区间、坏 manifest、非 COMPLETED） |
| `[error]` | stderr | 失败原因（配置/装配/文件不存在） |
| `[fail]` | stderr | 复核/校验判定失败（`verify` / 冷备校验） |
| `[dry-run]` | stdout | 装配预演结果 |
| `[run]` | stdout | `bt test` 回显实际执行的 pytest 命令 |
| `[note]` | stdout | `bt show` 回显 manifest 装配说明 |

### 5.3 常见错误与处置

| 现象（stderr） | 原因 | 处置 |
|---|---|---|
| `[error] 配置源读取失败：...` | YAML 不可读 / 语法错 / 顶层非映射 | 检查路径与 YAML 缩进；顶层必须是 mapping |
| `[error] 配置校验失败（N 处）：...` | Schema 违规 | 按列出的 JSON 路径逐条修（错误一次性全量给出） |
| `[error] 策略不可加载：...`（`--resolve-strategy`） | 类名拼错 / 模块不在 sys.path | 修 `run.strategy`；项目外策略用 `run.strategy_paths`（推荐；LL-2 已交付）或 `PYTHONPATH` |
| `run.strategy: 包名冲突：顶层包 '<pkg>' 已从 [...] 缓存装载，与配置声明的 strategy_paths=[...] 不符；包名冲突：请使用唯一包名（拒载，不静默沿用缓存）`（装配期 `ConfigError`） | 同名顶层包已从**别处**缓存装载（如真源 `代码/strategies`）；`sys.path` 注入不清洗 `sys.modules` ⇒ 导入会静默沿用缓存 | 用**唯一包名**（`cc_strategies`）；核对 `run.strategy_paths` 指**父目录**；**禁止** `del sys.modules` 后重导（同进程两套模块状态） |
| `数据版本门禁未通过（RC-14）：声明 <hash> ≠ 实算 <hash>（档位 fast）——数据快照与声明不一致；如确认换数，请更新 data.dataset.version/expected_hash`（装配期 `ConfigError`） | 配置钉的 `expected_hash` 与本次实算（档位＝`data.fingerprint_mode`）不符：换数 / 换档 / 日更后指纹变化 | 确认换数 ⇒ 更新 `data.dataset.version` / `expected_hash`（研究批次按 `freeze_batch --apply` 重冻）；确认未换数 ⇒ 查档位与数据盘；**不得静默放行** |
| `[PERF] B4 normalized 1196.28s > 1800s（raw 1459.52s / k 1.22）——回查 worker 数据路径（谓词下推 / 年表缓存 / 进程数适配）` | 归一化墙钟超预算（`k ≤ 3.0`，机器状态可判） | 按提示回查数据路径（谓词下推 / 年表缓存 / 进程数适配）；**不得改 budget** |
| `[ENV] 标定系数 k=<k>>3.0，本机不具备判定条件——请空载重跑；不得据此判代码回归、不得改 budget` | 本机 CPU/IO 探针偏慢（k 越界） | **空载重跑**；不得据此判代码回归、不得改 budget、不得放宽断言 |
| `[error] 装配失败：ContractVersionError: ...` | 策略/插件未声明或主版本不符 `contract_version` | 类里声明 `contract_version = CONTRACT_VERSION` |
| `[error] 装配失败：... risk.rules 为空 ...` | 空风控链 fail-closed | 写 `risk.rules`；调试才加 `allow_empty_chain: true` |
| `[error] 装配失败：... 覆盖度 ...` | 区间早于数据约束起点（如涨跌停） | 收窄区间；或显式 `data.allow_degraded_limit: true` |
| `[error] --set 需形如 a.b=1，得 '...'` | `--set` 缺 `=` | 修正写法（嵌套用 `__`） |
| `[error] --grid 需形如 path=v1,v2，得 '...'` | grid spec 语法错 | 检查分号/逗号/等号 |
| `[error] run 不存在或无 manifest：<id>` | run_id 写错或产物根不对 | `bt runs` 找回；必要时 `--out-dir` |
| `[fail] R1 复核失败：摘要不一致` | 产物被改 / 口径漂移 | 对照 `manifest.code_version`、检查是否手改产物 |
| `[error] 未知 dataset 动作：'...'` | 动作拼错 | 用 `build-golden\|verify\|refcalc\|freeze` |
| `[error] 主库不可访问: ...` | 数据盘未挂载 / 路径不对 | 检查 `BTF_DATA_DIR` |

> **性能门槛判定口径（MM-8，2.0.0）**：`normalized = raw ÷ k ≤ budget`；**先判达标，再判环境**——未达标且
> `k > 3.0` ⇒ `[ENV]`，否则 `[PERF]`；判定取 **min-of-2**（每个基准最多跑 2 次，任一次达标即通过）；
> `k` 只放大不缩小（clamp 上限 3.0）。预算 **20 / 60 / 1800 秒未动**。
> **2026-10-25 空载实测**：B2 **14.5s** · B3 **39.2s** · B4 外推 **1552s**。

---

## 6. 产物目录规范（CLI 的读写对象）

### 6.1 run 目录契约

```
<BTF_OUTPUT_DIR>/runs/<run_id>/
├── manifest.json      元数据（启动写骨架 RUNNING，结束补全 COMPLETED / FAILED）
├── snapshots.jsonl    逐日组合快照（PortfolioSnapshot 序列）
├── trades.jsonl       成交聚合（FIFO 开平）
├── fills.jsonl        成交明细（含费用分项）
├── rejections.jsonl   拒单（order + code/message）
├── events.jsonl       事件溯源日志（引擎直写；可采样降级）
├── metrics.json       全部绩效指标（15 项）
└── report.html        HTML 报告（`bt report` 生成，可删可重建）
```

`run_id` 格式：`YYYYMMDD_HHMMSS_<config_hash 前 6 位>`（如 `20260927_163148_7ecdb4`）——同一配置重跑，
时间戳不同、短哈希相同。

### 6.2 manifest 关键字段（`bt show` 消费）

| 字段 | 含义 |
|---|---|
| `run_id` / `status` / `error` | 标识与 `RUNNING/COMPLETED/FAILED` 状态 |
| `config_hash` / `config_effective` | 生效配置的哈希与脱敏回显 |
| `data_version` | 数据指纹（`dataset` / `content_hash` / 档位 / 水位） |
| `code_version` | 包版本 / git commit / dirty 标记 |
| `rules_version` / `rules_overrides` | 规则源版本与显式覆盖条目（R66′ 起**实际生效**，条目为规范化名） |
| `contract_version` / `btf_contract_version` | 产物契约版本 / 契约基线 |
| `metrics_digest` | **R1 对账锚**（`bt verify` 比对对象） |
| `assembly_notes` | 装配期说明（如质检抽样、降级放行） |
| `benchmarks` | 基准对照明细（**MM-6**）：每个基准一套相对指标；**首个＝主基准**（其数值亦进 `metrics.json`），其余为**对照基准**仅披露（报告「基准对照（多基准）」章节 **≥2 个才渲染**） |

### 6.3 只读命令对异常产物的行为（fail-visible）

| 场景 | `runs` | `show` | `report` / `verify` |
|---|---|---|---|
| manifest 缺失/坏 JSON | 行照列，状态 `?` + `[warn]` 原因 | 退出码 1 + `[error]` | 退出码 1 |
| 状态 `FAILED` | 正常列出 | 正常显示 + stderr `[warn]` | `report` 可用；`verify` 视摘要缺失报错 |
| metrics 缺失 | 指标列空 + `[warn]` | 指标打印 `null` | 退出码 1 |
| 契约版本不兼容 | 行照列 + `[warn]` | 退出码 1 | 退出码 1 |

> 原则：**「列不出来」与「列出来但读不了」是两件事**——后者必须可见（不静默吞掉坏产物）。

---

## 7. 配置速查（CLI 的输入）

> 配置的完整讲解见 [20-回测平台使用指引.md](20-回测平台使用指引.md) §4-§9；本章只放 CLI 视角的速查。

### 7.1 四层合并与优先级

```
L1 内置默认  →  L2 环境变量 BTF_*  →  L3 用户 YAML  →  L4 CLI --set
（DEFAULTS）     （BTF_<SECTION>__<KEY>）              （bt run --set）
```

- 深合并：嵌套 dict 递归，非 dict 后值整替；合并后统一经 JSON Schema 校验（错误全量列出）；
- `bt config-check` 覆盖 L1-L3；`bt run --set` 追加 L4；
- 布尔值只能从 L2（环境变量）/ L3（YAML）进入，**`--set` 不做布尔化**（见 §4.2 注意）。

### 7.2 关键段速查

| 段 | 必填 | 要点 |
|---|---|---|
| `schema_version` | ✅ | 固定 `"backtest.v1"` |
| `run.strategy` | ✅ | `module:Class` 点分路径 |
| **`run.strategy_paths`** | | **2.0.0 新增**：外部策略搜索路径（字符串数组，**绝对路径**、指向**包的父目录**）；装载前幂等注入 `sys.path`（去重、只加不删），路径不存在即 `ConfigError`；同名包冲突拒载（§4.2 / §5.3） |
| `run.period` | ✅ | `start`/`end`（`YYYY-MM-DD`） |
| `run.universe` | | `explicit`（symbols）/ `all` / `index`（`hs300` 别名或 `000300.SH`，PIT 快照） |
| `run.initial_cash` | | 默认 1,000,000 |
| `data.feed` | ✅ | `tushare_parquet` / `mixed` / `memory` |
| `data.quality` | | 默认 `{enabled: true, strict: false, max_symbols: 200}` |
| `data.allow_degraded_limit` | | 默认 false；区间早于涨跌停数据起点即 fail |
| `execution.*` | | 默认 `next_open + tiered_v1 + none + full`（回测基线组合） |
| `risk.rules` | | 空链 fail-closed（除非显式 `allow_empty_chain: true`） |
| `rules.source` | | `static` / `mirror_json`（配 `path`） |
| `seed.master` | | 随机种子单一真源 |
| `report.sections` | | 章节名表；`assumptions` / `reproduce` 强制 |
| `analysis.metrics` | | 指标名字表（自定义 Analyzer 生效点） |

### 7.3 插件注册名速查（配置里可写的值）

| 扩展点（配置键） | 注册名 |
|---|---|
| `data.feed` | `tushare_parquet` / `mixed` / `memory` |
| `execution.handler` | `next_open` |
| `execution.cost_model` | `tiered_v1` / `zero` / `flat_rate` |
| `execution.slippage.model` | `none` / `fixed` / `pct` |
| `execution.rebalancer` | `full` / `threshold` |
| `risk.rules[].name` | `max_weight` / `cash_check` / `tradability` |
| `rules.source` | `static` / `mirror_json` |
| optimizer 名（内部） | `grid_search` |

> 注册表在 `btf/registry/catalog.py`：未知名字报错并列出可选项；所有插件经契约版本协商（`negotiate`）后才装配。

### 7.4 装配期硬门（`config-check` 通过但 `run` 可能拒绝）

| 硬门 | 触发条件 | 表现 |
|---|---|---|
| 契约版本协商 | 插件/策略未声明或主版本不符 `contract_version` | `ContractVersionError` |
| 区间覆盖度 | 区间早于数据约束起点（如 `stk_limit`） | `ConfigError`（除非 `allow_degraded_limit: true`） |
| 空风控链 | `risk.rules` 为空且 `allow_empty_chain=false` | `ConfigError`（fail-closed） |
| 基准守卫 | `report.benchmark` 格式不符 / 区间无覆盖 | `ConfigError` |
| 策略加载 | `module:Class` 不可 import / 非 `StrategyBase` | 装配失败 |

---

## 8. 典型工作流（命令组合）

### 8.1 策略迭代上线（日常五步）

```bash
$ cd D:\量化策略\代码\btf
$ bt config-check config.yaml --resolve-strategy     # ① 配置 + 策略类一并校验
$ bt run --config config.yaml --dry-run              # ② 装配期硬门预演（秒级）
$ bt run --config config.yaml --verbose              # ③ 正式跑（观测耗时分布）
$ bt report --run <run_id>                           # ④ 出报告
$ bt verify --run <run_id>                           # ⑤ 抽样复核
```

### 8.2 参数调优

```bash
$ bt optimize --config config.yaml \
              --grid "run.params.top_k=3,5,10;run.params.lookback=10,20,40" \
              --objective sharpe_ratio --workers 8 --out E:\grid.json
$ bt runs --latest 3                                 # 需要留档的组合再单独 run
$ bt run --config config.yaml --set run.params.top_k=5
```

> 调参区间必须足够长（≥ 20 交易日、建议 ≥ 1 年）；极短区间的排名会被无意义指标污染。

### 8.3 审计与复核

```bash
$ bt runs --out-dir <产物根> --latest 10             # ① 圈定复核范围
$ bt show --run <run_id>                             # ② 读事实（含代码版本/指纹/规则版本）
$ bt verify --run <run_id>                           # ③ 重算对账
$ bt report --run <run_id>                           # ④ 出可交付报告
```

### 8.4 提交前与定版

```bash
$ bt check                                           # ① 十项门禁全绿（10/10）
$ bt test                                            # ② l1 回归（秒级）
$ bt test --layer all                                # ③ 全量回归（约 1 小时，节点前跑）
$ bt cold-backup --reason "vX.Y.Z 定版"              # ④ 定版冷备（含 sha256 自校验）
$ bt cold-backup --verify <冷备目录>                  # ⑤ 异地校验（可选）
```

---

## 9. 症状导向排障

> 本节面向「**没有报错但结果不对**」的症状；带 stderr 报错的对照 §5.3。

| 症状 | 排查路径 |
|---|---|
| `runs` 看不到刚跑的产物 | 是否用了 `--out` 换过产物根？（`bt runs --out-dir <该根>`）；注意 `--out` 是**根目录**而非 run 目录 |
| `--set` 设了参数但行为没变 | 键路径写错（应写配置的真实键路径，嵌套用 `__`）；布尔值不生效见 §4.2 注意 |
| `optimize` 排名怪异 | 区间太短（< 20 交易日）；objective 名不存在（静默按 `-inf` 垫底，不报错） |
| 报告里缺章节 | `report.sections` 被改；`assumptions`/`reproduce` 强制不可关，其余按配置渲染 |
| 复现不了历史 run | 对照 `manifest`：数据指纹 / 代码版本 / 配置哈希 / 规则版本四项是否一致（`bt show` 可见） |
| 忘了 run_id | `bt runs --latest N` 找回（按时间倒序） |
| 想知道一次 run 慢在哪 | `bt run --verbose`（装配 vs 日循环分步耗时） |
| 产物疑似被改 | `bt verify --run <id>`（重算对账） |
| 主库换盘后全报错 | `BTF_DATA_DIR` 未设置 / 指向旧盘 |
| 中文路径乱码 | 终端编码需 UTF-8（CLI 已强制 stdout UTF-8；PowerShell 建议 `chcp 65001`） |

---

## 10. 附录

### 附录 A：速查卡（一屏带走）

```
bt --version                                          # btf 2.0.0
bt config-check <yaml> [--dump] [--resolve-strategy]  # 静态校验
bt run --config <yaml> [--set K=V]... [--dry-run] [--out DIR] [--verbose]
bt runs [--latest N] [--out-dir DIR]                  # 只读清单
bt show --run <id> [--out-dir DIR]                    # 只读详情（不重算）
bt report --run <id> [--out HTML] [--out-dir DIR]     # 幂等出报告
bt verify --run <id> [--out-dir DIR]                  # 重算对账
bt optimize --config <yaml> --grid "p=v1,v2;p2=v1,v2" [--objective X] [--workers N] [--top N] [--out F]
bt dataset [build-golden|verify|refcalc] [--cases g1|all] [--list] [--force] [--out DIR]
bt dataset freeze --config <yaml> [--mode meta|fast|full] [--out DIR]   # 数据冻结清单（<anchor_date>.json，只读）
bt test [--layer l1|l2|l3|l4|l5|all] [--pattern EXPR] [--path P]
bt validate walk-forward --config <yaml> --window N [--test N] [--step N] [--mode rolling|anchored] [--purge N] [--embargo N] [--no-persist]
bt validate mc --run <id> [--n N] [--seed N] [--out F]  # 读回产物，不重跑引擎
bt check                                              # 十项门禁（无参数）
bt cold-backup [--reason R] [--out DIR] [--verify DIR]
```

| 退出码 | 含义 |
|---|---|
| 0 / 1 / 2 | 成功 / 业务失败 / 用法与前置错误（`test` 透传 pytest 码） |

| 默认路径 | 值 |
|---|---|
| 产物根 | `D:\量化策略\回测产物\runs\<run_id>\` |
| 数据集根 | `D:\量化策略\代码\btf\btf_datasets\golden_v1\cases\` |
| 冷备根 | `D:\量化策略\备份\<YYYYMMDD-HHMM>-btf-<ver>-cold\` |
| 主库（只读） | `D:\全量数据\market_data` |

### 附录 B：命令 ↔ 实现锚点（`代码/btf/btf/cli/main.py`）

| 命令 | 处理器（行号） | 参数注册（行号） |
|---|---|---|
| `config-check` | `_cmd_config_check` 83-134 | `_args_config_check` 164-175 |
| `run` | `_cmd_run` 183-255 | `_args_run` 258-271 |
| `report` | `_cmd_report` 272-285 | `_args_report` 288-296 |
| `verify` | `_cmd_verify` 297-319 | `_args_verify` 322-329 |
| `test` | `_cmd_test` 330-364 | `_args_test` 367-376 |
| `dataset` | `_cmd_dataset` 377-397 | `_args_dataset` 400-415 |
| `optimize` | `_cmd_optimize` 472-535 | `_args_optimize` 538-558（`_parse_grid` 416-436） |
| `validate` | `_cmd_validate` 693-786 | `_args_validate` 789-819（`_VALIDATE_ACTIONS` 690） |
| `check` | `_cmd_check` 559-568 | `_args_check` 571-572（无参数） |
| `cold-backup` | `_cmd_cold_backup` 575-583 | `_args_cold_backup` 586-589 |
| `runs` | `_cmd_runs` 608-628 | `_args_runs` 631-634 |
| `show` | `_cmd_show` 637-681 | `_args_show` 684-690 |
| 注册表 / 分发 | `COMMANDS` 820-846 / `main()` 849-864 | — |

> **行号为 2026-10-06 实测**（原表为 1.1.0 收编时快照，本批全员复测后刷新——RC 批次与 CLI 收编使多数行号漂移；
> 维护时按附录 C-1 重新实测，**不得沿用**）。

> 编排实现委托 `btf/app/services.py`（CLI 与 API 同源）：`run_backtest` / `build_report_file` /
> `build_dataset` / `run_gate_check` / `cold_backup` / `list_runs` / `run_detail`。

### 附录 C：维护约定

1. 本手册与 CLI **同口径**：`btf/cli/main.py` 的参数/默认值/退出码/输出前缀变更时，同步修订对应小节与附录 B；
2. 命令新增时必须：更新 §2.2 总表、新增 §4.x 小节、更新附录 A/B；
3. 样例输出中标注「实测」的段落需在文档修订时用 `python -m btf.cli.main <命令>` 原样复现；
4. 与 20 号使用指引分工：**20 号讲「怎么用」（面向结果），本手册讲「有哪些开关、输出什么、失败怎么办」（面向细节）**。

---

*本手册覆盖 `bt` 全部 13 命令；任意命令加 `--help` 可获得与本文一致的即时参数说明。*
