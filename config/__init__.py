# -*- coding: utf-8 -*-
r"""
config/ — A股三层轮动金字塔策略 · 统一配置中心（W3 起为包）
====================================================
所有路径、凭据、标的清单、计算参数 集中管理于此；一处修改，全局生效。

模块架构（2026-11-05 W3b 拆分后按实测目录重列）:
  config/
    __init__.py          ← 你在这里（纯 re-export + 版本真源 __version__）
    paths.py             ← 路径单一真源（盘符锚点派生 + 环境变量覆盖）
    tokens.py            ← API 凭据（_secret：环境变量 > .env）+ PushPlus 推送
    universe.py          ← 标的清单：赤潮目录 / 大盘指数 / 宽基ETF / 基金白名单 / 期货期权
    params.py            ← 计算参数 + IV/波动率监控
    download.py          ← 全量下载：限频表 / 分页 / 并发 / 下载阶段
    backfill.py          ← 补数执行配置：起点 / 指数清单 / 分页 / 批量落盘
    backfill_targets.py  ← 补数目标清单（单一字面量，豁免 ≤500 行）
    tables.py            ← 表→分区列 / 源端起点基线 / 清理目标 / 体检主键
    daily.py             ← 尾盘增量 / 基金净值 / 日更分组与豁免 / 周期更新
    permissions.py       ← 权限漏网登记 + 日更空转豁免
    monitor.py           ← 盘中监控：标的 / 周期 / 东财 / 新浪 / 差额补全
  indicators.py          ← 技术指标库 (统一BBI短/长周期 + MACD DataFrame版)
  main.py                ← 统一CLI入口（子命令注册表 + db 命令族）
  pyproject.toml         ← 包安装配置 + ruff / pytest 配置

  common/                ← 跨模块公共层（路径/IO/日历/代码 的唯一真源）
    paths.py             路径解析（禁止再用 __file__ + ".." 自行推算）
    reader.py            按年分区的统一读取入口（布局无关）
    jsonio.py            JSON 读写统一封装
    parquet_store.py     Parquet 存储层：
                           merge_append / upsert_grouped  按标的增量合并去重
                           upsert_by_year                  按年分区大表
                           YearBatchWriter                 批量落盘（减少读改写）
    calendar.py          交易日历唯一读取入口
    codes.py             证券代码转换（ts_code / secid / 新浪 symbol）
    dtypes.py            全库 dtype 规约
    db_version.py        库版本（单一真源）
    logging_setup.py     日志初始化（由 main() 显式调用，消除 import 副作用）

  fetch/                 ← 拉取类任务
    base.py              共享基建: RateLimiter / Checkpoint / ts_call / 并发框架
    daily_update.py      ★ 每日增量更新统一入口（按日并发 + YearBatchWriter
                           批量落盘 + 断点三态 + 复核窗口）
    periodic_update.py   周期任务更新
    full_download.py     全量历史下载 (个股/ETF/LOF/财务)
    cb_download.py       可转债下载 (CBCheckpoint继承base.Checkpoint)
    fund_nav_update.py   基金净值增量更新
    redtide_supply.py    赤潮数据补全（market_state 派生）
    backfill_spec.py     ┐ 通用补数·任务规格层（该拉哪些数据，纯计算）
    backfill_io.py       │ 通用补数·IO 层（分页拉取 / 三种落盘布局 / 前置表）
    backfill.py          ┘ 通用补数·执行编排 + CLI
    _legacy/             ⛔ 旧按 ts_code 逐文件落盘链路（gap_update.py /
                           intraday_update.py），会打乱按年分区，仅供代码考古

  push/                  ← 推送
    pushplus.py          PushPlus微信推送
    __init__.py          延迟导入；已归档入口（run_scan / run_report）抛
                           AttributeError 并指向归档目录

  tools/                 ← 工具脚本（四层，W4 归位）
    gate.py              提交前门禁（ruff + CLI + config-check + smoke）
    regress_pipeline.py  回归测试（盘后流水线 + 存储布局约束，改数据层后必跑）
    doctor.py / config_check.py / check_coverage.py / consumer_coverage.py
    consumer_pool.py     消费方池常量冻结（QIDIAN_POOL / XAU_WATCHLIST /
                           WIND_CB_THERMOMETER 的唯一真源）
    ckpt_tool.py / clean_scratch.py / data_backup.py / quicklook.py /
    receipt_reconcile.py
    db/                  audit.py / schema.py / schema_align.py / migrate.py /
                           clean.py / gen_report.py / fingerprint.py
    research/            factor_library.py / manager_profile.py /
                           fund_pool_builder.py / fund_stale.py /
                           build_micro_index.py / etf_flow.py / leverage_monitor.py
    export/              list_lof.py / export_lof.py / fund_classify.py

  alt/                   ← alt 库（物理隔离于主库；akshare 源）
    config.py            路径与写保护区（assert_writable / AltPathViolationError）
    audit.py / update.py / backfill.py / list_backfill.py / oversize_fetch.py /
    us_backfill.py / reader.py / io.py / rate.py / urls.py / spec.py /
    verify_spec.py / compat.py
    tests/verify_os_r1r.py

  wind/                  ← Wind 库
    config.py            路径与写保护区（assert_writable / WindPathViolationError）
    catalog.py / wind_gate.py

  tests/                 ← 主工程 smoke（pytest testpaths 指向此处）
    test_config_api.py / test_paths.py / test_cli.py

模块拆分原则（2026-09-17 确立）:
  单个 .py 超过约 500 行, 或承担两类以上职责时拆分;
  拆出的兄弟模块在原文件 re-export, 保持既有 import 路径不变。
  config/backfill_targets.py 属**单一字面量**（无逻辑），豁免「≤500 行」标准：
  834 行的 BACKFILL_TARGETS 若按行拆会改变 OrderedDict 构造方式，引入键冲突
  与顺序风险，不值得。

存储布局（2026-09-17 全库统一）:
  全库已统一为 **按年分区**（by_year）: {表}/{YYYY}.parquet，仅 1 张表例外
  （stock_company —— 一标的一行的静态元数据，无日期列，按年分区无意义）。
  下游读取一律走 common/reader.py（布局无关），不要再按 {ts_code}.parquet
  硬编码拼路径：by_year 下文件名是年份，旧写法会把 "2026" 当成代码。
  各表分区列见本文件 TABLE_DATE_COL。

【历史】已归档 / 已迁移项（仅留痕，细节见各归档目录 README）
  旧单文件 config.py          → 拆为 config/ 包（2026-11-05 W3）
  iv_tracker.py / pipeline.py / fix_and_resonance.py / convergence.py /
  divergence_scanner.py / divergence_trigger.py / intraday_monitor.py /
  report_generator.py         → _archive/背离扫描器-20260924/、
                                _archive/日报链路-20260924/、
                                _archive/legacy链-20260925/（2026-09 归档）
  strategies/qidian.py 等 5 文件 → _archive/strategies-奇点战法-20261021/
                                （2026-10-21 停止裁定，池常量冻结于 tools/consumer_pool.py）
  db_audit.py / db_clean.py / db_migrate.py / db_schema.py / gen_db_report.py
                              → 迁入 tools/db/（2026-11-05 W4）
  gap_update.py / intraday_update.py → 迁入 fetch/_legacy/（2026-11-05 W4）
  utils.py                    → 拆为 tools/export/fund_classify.py
"""
# 版本单一真源（2026-09-30 CLI 审查 P1-2，对标 btf P3-4）：
# pyproject.toml 经 setuptools attr 读取本值，main.py --version 亦读本值
# —— 全项目不得另写版本号。纯字面量赋值，setuptools 可静态解析。
__version__ = "7.0.0"

# 2026-11-05 W3b：各章常量下沉至 config/ 子模块（单一真源）；
# 此处只做 re-export，保持既有 `from config import X` 的公开 API 不变。
from config.backfill import *  # noqa: F403
from config.backfill_targets import *  # noqa: F403
from config.daily import *  # noqa: F403
from config.download import *  # noqa: F403
from config.monitor import *  # noqa: F403
from config.params import *  # noqa: F403
from config.paths import *  # noqa: F403
from config.paths import _BASE_DIR, _DATA_DRIVE  # noqa: F401
from config.permissions import *  # noqa: F403
from config.tables import *  # noqa: F403
from config.tokens import *  # noqa: F403
from config.universe import *  # noqa: F403
