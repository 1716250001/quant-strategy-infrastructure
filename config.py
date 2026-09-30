# -*- coding: utf-8 -*-
"""
config.py — A股三层轮动金字塔策略 · 统一配置中心
====================================================
所有路径、Token、标的清单、计算参数 集中管理于此。
一处修改，全局生效。

模块架构 (v5.0 模块化重构):
  config.py            ← 你在这里（全局唯一配置中心）
  indicators.py        ← 技术指标库 (统一BBI短/长周期 + MACD DataFrame版)
  iv_tracker.py        ← IV/HV 计算
  pipeline.py          ← 数据采集流程编排 (run_fetch)
  main.py              ← 统一CLI入口（子命令注册表，新增命令只需注册一个函数）
  qidian_daily_scan.py ← 奇点战法扫描编排（标的池/取数/信号判断见 strategies/qidian.py）
  pyproject.toml       ← 包安装配置 (pip install -e . 可消除sys.path.insert)

  common/              ← 跨模块公共工具层（路径/IO/日历/代码 的唯一真源）
    paths.py             路径解析（禁止再用 __file__ + ".." 自行推算）
    jsonio.py            JSON 读写统一封装
    parquet_store.py     Parquet 存储层：
                           merge_append / upsert_grouped  按标的增量合并去重
                           upsert_by_year                  按年分区大表
                           YearBatchWriter                 批量落盘（减少读改写）
                           list_parquet_files 等           目录扫描工具
    calendar.py          交易日历唯一读取入口
    codes.py             证券代码转换（ts_code / secid / 新浪 symbol）
    logging_setup.py     日志初始化（由入口显式调用，消除 import 副作用）

  strategies/          ← 策略信号层
    qidian.py            奇点战法标的池 / 取数 / 指标 / 信号判断

  fetch/               ← 拉取类任务
    base.py              共享基建: RateLimiter / Checkpoint / ts_call / 并发框架
    market_beta.py       大盘β采集 (8只指数K线)
    leverage.py          杠杆监控 (两融+期货+期权) + 日报兼容层
    iv_fetch.py          IV隐含波动率采集
    wide_etf.py          宽基ETF采集 (复用 indicators.calc_bbi_value)
    persistence.py       数据持久化 (save_all)
    full_download.py     全量历史下载 (个股/ETF/LOF/财务)
    cb_download.py       可转债下载 (CBCheckpoint继承base.Checkpoint)
    daily_update.py      ★ 每日增量更新统一入口（按日并发 + YearBatchWriter
                           批量落盘 + 断点三态 + 复核窗口），被 main.py 的
                           gap-update / intraday-update 调用
    intraday_update.py   ⛔ 已废弃：旧按 ts_code 逐文件落盘（会打乱按年分区）
    gap_update.py        ⛔ 已废弃：同上；仅供代码考古
    fund_nav_update.py   基金净值增量更新
    redtide_supply.py    赤潮数据补全（market_state 派生；adj_factor/stk_limit
                           已改道至 daily_update）
    wind_index_enum.py   Wind 自编指数枚举（隔离：产出目录与 tushare 库分离）
    backfill_spec.py     ┐ 通用补数·任务规格层（该拉哪些数据，纯计算）
    backfill_io.py       │ 通用补数·IO 层（分页拉取 / 三种落盘布局 / 前置表）
    backfill.py          ┘ 通用补数·执行编排 + CLI

  push/                ← 推送与报告
    pushplus.py          PushPlus微信推送
    # [ARCHIVED 2026-09-25 F4] kline_source.py / html_theme.py / html_components.py
    #   ——旧宽基ETF轮动采集+报告链（pipeline.py 及 fetch 采集件一并归档：
    #     _archive/legacy链-20260925/）
    # [ARCHIVED 2026-09-24/25] intraday_monitor.py + convergence.py（背离链）、
    #   report_generator.py（日报链）、fix_and_resonance.py（D2 拆分）

  tools/               ← 工具脚本
    utils.py             公共函数 (classify_fund)
    fix_and_resonance.py 四因子补算 + ETF份额 + 多层共振
    gen_split_v3_reports.py  HTML日报生成脚本
    # [ARCHIVED 2026-09-25] divergence_scanner.py / divergence_trigger.py —— 背离扫描器，实证负增量归档（_archive/背离扫描器-20260924/）
    check_coverage.py    数据覆盖率检查（可转债 + ETF/LOF）
    list_lof.py / export_lof.py  LOF清单统计与导出
    db_clean.py          存量数据清理（版本冗余压缩，默认干跑 + 自动备份）
    db_audit.py          全量数据库体检（断点/重复/缺口/规模）
    db_schema.py         数据库结构扫描（输出机器可读 schema JSON）
    db_migrate.py        存储布局迁移（by_code → by_year，行数双向校验）
    gen_db_report.py     结构报告生成（从 schema JSON 出 Markdown，避免手工失真）
    regress_pipeline.py  回归测试（盘后流水线 + 存储布局约束，改数据层后必跑）
    build_micro_index.py 本地自建微盘指数（替代 Wind 8841431.WI）
    factor_library.py    因子库（全收益基准 + 风格因子）
    fund_pool_builder.py 公募主动权益基金池构建
    manager_profile.py   基金经理风格画像（风格/跟踪指数/β）

模块拆分原则（2026-09-17 确立）:
  单个 .py 超过约 500 行, 或承担两类以上职责时拆分;
  拆出的兄弟模块在原文件 re-export, 保持既有 import 路径不变。

存储布局（2026-09-17 全库统一）:
  全库已统一为 **按年分区**（by_year）: {表}/{YYYY}.parquet，仅 1 张表例外
  （stock_company —— 一标的一行的静态元数据，无日期列，按年分区无意义）。
  下游读取一律走 common/reader.py（布局无关），不要再按 {ts_code}.parquet
  硬编码拼路径：by_year 下文件名是年份，旧写法会把 "2026" 当成代码。
  各表分区列见本文件 TABLE_DATE_COL。
"""

import os

# 版本单一真源（2026-09-30 CLI 审查 P1-2，对标 btf P3-4）：
# pyproject.toml 经 setuptools attr 读取本值，main.py --version 亦读本值
# —— 全项目不得另写版本号。纯字面量赋值，setuptools 可静态解析。
__version__ = "5.0.0"

# ============================================================
# 0. 路径配置 — 全局唯一定义
# ============================================================
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CODE_DIR = _BASE_DIR                                              # 代码目录（包根），公开名
PROJECT_DIR = os.path.dirname(_BASE_DIR)                          # A股三层轮动金字塔策略/
DATA_DIR = os.path.join(PROJECT_DIR, "数据", "daily_data")        # 日报数据 (JSON/CSV)
MARKET_DATA_DIR = r"D:\全量数据\market_data"                       # 全量市场数据 (Parquet) — 2026-08-21 移至 D盘
DOC_DIR = os.path.join(PROJECT_DIR, "文档")                        # 文档输出
TEMP_DIR = os.path.join(PROJECT_DIR, ".temp")                       # 临时文件（2026-09-25 C5 修正：原"项目外一级"在 D 盘部署后落到 D:\.temp，收进项目内）
LOG_DIR = os.path.join(PROJECT_DIR, "数据", "logs")               # 盘中监控日志
SIGNALS_DIR = os.path.join(PROJECT_DIR, "数据", "signals")        # 盘中信号持久化

# 全量市场数据子目录 (market_data 下)
META_DIR = os.path.join(MARKET_DATA_DIR, "metadata")       # 元数据 (stock_basic/fund_basic/trade_cal/cb_basic)
STOCK_DAILY_DIR = os.path.join(MARKET_DATA_DIR, "daily")           # 个股日线
STOCK_DAILY_BASIC_DIR = os.path.join(MARKET_DATA_DIR, "daily_basic") # 个股每日指标
FUND_DAILY_DIR = os.path.join(MARKET_DATA_DIR, "fund_daily")       # ETF/LOF日线
FUND_NAV_DIR = os.path.join(MARKET_DATA_DIR, "fund_nav")           # 基金净值 (场外.OF + 场内ETF/LOF)
CB_DAILY_DIR = os.path.join(MARKET_DATA_DIR, "cb_daily")           # 可转债日线
# [ARCHIVED 2026-09-25 G1] 死配置：全库 grep（83 文件，含字符串/getattr 形态）+工作区/skills 交叉核实均无引用。原用途：财务三表目录（读取走赤潮 market_data.py 独立定义，同值）。保留定义保行号稳定，勿新增引用。
INCOME_DIR = os.path.join(MARKET_DATA_DIR, "income")               # 利润表
# [ARCHIVED 2026-09-25 G1] 死配置：全库 grep（83 文件，含字符串/getattr 形态）+工作区/skills 交叉核实均无引用。原用途：财务三表目录（同上）。保留定义保行号稳定，勿新增引用。
BALANCESHEET_DIR = os.path.join(MARKET_DATA_DIR, "balancesheet")    # 资产负债表
# [ARCHIVED 2026-09-25 G1] 死配置：全库 grep（83 文件，含字符串/getattr 形态）+工作区/skills 交叉核实均无引用。原用途：财务三表目录（同上）。保留定义保行号稳定，勿新增引用。
CASHFLOW_DIR = os.path.join(MARKET_DATA_DIR, "cashflow")           # 现金流量表
# [ARCHIVED 2026-09-25 G1] 死配置：全库 grep（83 文件，含字符串/getattr 形态）+工作区/skills 交叉核实均无引用。原用途：财务三表目录（同上）。保留定义保行号稳定，勿新增引用。
FINA_INDICATOR_DIR = os.path.join(MARKET_DATA_DIR, "fina_indicator") # 财务指标

# ============================================================
# 1a. 赤潮数据对接 — 新增目录 (2026-08-21)
# ============================================================
ADJ_FACTOR_DIR = os.path.join(MARKET_DATA_DIR, "adj_factor")       # 复权因子 (赤潮)
INDEX_DAILY_DIR = os.path.join(MARKET_DATA_DIR, "index_daily")     # 指数日线 (赤潮)
STK_LIMIT_DIR = os.path.join(MARKET_DATA_DIR, "stk_limit")         # 涨跌停价 (赤潮 market_state 数据源)
MARKET_STATE_DIR = os.path.join(MARKET_DATA_DIR, "market_state")   # 市场状态 (赤潮)
MARKET_STATE_DERIVED_DIR = os.path.join(MARKET_STATE_DIR, "derived")  # 派生跌停状态 (按年分文件)

# 赤潮必需指数 (index_daily)
# [ARCHIVED 2026-09-25 G1] 死配置：全库 grep（83 文件，含字符串/getattr 形态）+工作区/skills 交叉核实均无引用。原用途：v7.4 择时残留（L0 牛熊状态机已废弃）。保留定义保行号稳定，勿新增引用。
REDTIDE_INDEX_CODES = ["000300.SH", "000001.SH", "399006.SZ"]   # 沪深300(必须) + 上证综指 + 创业板指(建议)
# [ARCHIVED 2026-09-25 G1] 死配置：全库 grep（83 文件，含字符串/getattr 形态）+工作区/skills 交叉核实均无引用。原用途：v7.4 择时残留（同上）。保留定义保行号稳定，勿新增引用。
REDTIDE_INDEX_START = "20050101"   # 沪深300基期起 (覆盖2008回测起点)

# 赤潮跌停状态 board 前缀映射 (按ts_code前3位)
REDTIDE_BOARD_PREFIX = {
    ("000", "001", "002", "003", "600", "601", "603", "605"): "主板",
    ("300", "301"): "创业板",
    ("688", "689"): "科创板",
    ("920",): "北交所",
}
# market_state 年份范围 (2008起, 与规格书一致)
REDTIDE_MARKET_STATE_START_YEAR = 2008

# ============================================================
# 1. API Token — 全局唯一（G2 · 2026-09-25 环境变量化）
#    读取优先级：环境变量 > 代码目录 .env 文件 > 空串。
#    明文已从代码移除；本地值存放于 D:\量化策略\代码\.env（勿外发/勿同步云端）。
#    重装恢复法：.env 随 D 盘走，或按 .env.example 手动重设三枚系统环境变量。
# ============================================================
def _secret(name: str) -> str:
    """凭据读取：优先环境变量，其次代码目录 .env（KEY=VALUE 每行一条，# 注释）。"""
    val = os.environ.get(name, "").strip()
    if val:
        return val
    try:
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == name:
                    return v.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


TS_TOKEN = _secret("TS_TOKEN")          # Tushare（2000 积分档；2026-09-24 老大提供重填，09-25 G2 移入 .env）
PUSHPLUS_TOKEN = _secret("PUSHPLUS_TOKEN")  # PushPlus 微信推送（同上）
WIND_API_KEY = _secret("WIND_API_KEY")  # Wind AIFin（ak_ 前缀；同上）。⚠ 2026-09-25 Wind 研究暂停、代码剥离至 _scratch\wind-研究暂存\——本 key 仅保留供暂存脚本环境变量续用（暂存脚本均直读环境变量，不 import 本常量）

# ============================================================
# 1b. PushPlus 微信推送配置
# ============================================================
PUSHPLUS_CHANNEL = "wechat"       # 默认渠道: wechat(微信公众号)
PUSHPLUS_TEMPLATE = "markdown"    # 默认模板: markdown

# ============================================================
# 2. 大盘监控指数 (8只)
# ============================================================
# 格式: {名称: (Tushare代码, AKShare代码)}
INDEX_MAP = {
    "上证50":   ("000016.SH", "sh000016"),
    "沪深300":  ("000300.SH", "sh000300"),
    "中证500":  ("000905.SH", "sh000905"),
    "中证1000": ("000852.SH", "sh000852"),
    "中证2000": ("932000.CSI", "sh000932"),
    "科创50":   ("000688.SH", "sh000688"),
    "创业板指": ("399006.SZ", "sz399006"),
    "北证50":   ("899050.BJ", "bj899050"),
}

# 报告用宽基指数 (简化版，用于日报/监控)
# [ARCHIVED 2026-09-25 G1] 死配置：全库 grep（83 文件，含字符串/getattr 形态）+工作区/skills 交叉核实均无引用。原用途：日报链路残留（日报已归档 2026-09-24）。保留定义保行号稳定，勿新增引用。
BROAD_INDICES_FOR_REPORT = {
    "上证指数": "sh000001",
    "沪深300":  "sh000300",
    "中证500":  "sh000905",
    "中证1000": "sh000852",
    "中证2000": "sh000932",
    "创业板指": "sz399006",
    "科创50":   "sh000688",
}

# ============================================================
# 3. 宽基ETF (7只) — 轮动池
# ============================================================
# 格式: {名称: (代码, 跟踪指数Tushare码, 风格描述)}
WIDE_ETF = {
    "上证50ETF":    ("510050", "000016.SH", "大市值价值"),
    "沪深300ETF":   ("510300", "000300.SH", "跨市场核心基准"),
    "中证500ETF":   ("510500", "000905.SH", "中盘成长"),
    "中证1000ETF":  ("512100", "000852.SH", "小盘风格"),
    "中证2000ETF":  ("563300", "000932.SH", "微盘风格"),
    "创业板ETF":    ("159915", "399006.SZ", "成长风格"),
    "科创50ETF":    ("588000", "000688.SH", "硬科技方向"),
}

# ETF-宽基指数映射 (用于份额追踪)
# [ARCHIVED 2026-09-25 G1] 死配置：全库 grep（83 文件，含字符串/getattr 形态）+工作区/skills 交叉核实均无引用。原用途：日报链路残留（同上）。保留定义保行号稳定，勿新增引用。
WIDE_ETF_BY_INDEX = {
    "000016.SH": ["510050", "510710", "510850", "550510", "510030", "510680", "510100", "533050"],
    "000300.SH": ["510300", "510310", "510330", "510350", "510360", "510390", "515330", "159300", "159310", "159330", "515360", "510370", "563520", "159335"],
    "000905.SH": ["510500", "510510", "510550", "510560", "510580", "159337", "512500", "159352", "563550", "510530", "159935", "563580"],
    "000852.SH": ["512100", "159263", "589360", "560010", "510160", "159845"],
    "000932.SH": ["563300", "159531", "159532", "159535", "563200", "560220", "159378", "159363"],
    "000688.SH": ["588000", "588050", "588060", "588080", "588160", "588180", "588200", "589850", "588830", "588380"],
    "399006.SZ": ["159915", "159908", "159766", "159357", "159350", "159393", "159392", "159346", "159493", "159275", "159261", "159256", "159369", "159380", "159257", "159258", "563090", "159364", "159365", "159398", "159399", "159910", "159927", "159977"],
}

# ============================================================
# 4. 期货 & 期权配置 (杠杆监控)
# ============================================================
FUTURES_TARGETS = {
    "IF": {"name": "沪深300期货", "multiplier": 300, "weight": 0.40},
    "IC": {"name": "中证500期货", "multiplier": 200, "weight": 0.30},
    "IH": {"name": "上证50期货",  "multiplier": 300, "weight": 0.15},
    "IM": {"name": "中证1000期货","multiplier": 200, "weight": 0.15},
}

OPTION_UNDERLYINGS = {
    "510050": {"name": "上证50ETF期权",   "weight": 0.25},
    "510300": {"name": "沪深300ETF期权",  "weight": 0.50},
    "510500": {"name": "中证500ETF期权",  "weight": 0.25},
}

# ============================================================
# 5. 计算参数常量
# ============================================================
LOOKBACK = 25          # K线回溯窗口（交易日）
# [ARCHIVED 2026-09-25 G1] 死配置：全库 grep（83 文件，含字符串/getattr 形态）+工作区/skills 交叉核实均无引用。原用途：均线窗口（L3 均线方向已废弃）。保留定义保行号稳定，勿新增引用。
WINDOW_SHORT = 5       # 短线窗口
# [ARCHIVED 2026-09-25 G1] 死配置：全库 grep（83 文件，含字符串/getattr 形态）+工作区/skills 交叉核实均无引用。原用途：均线窗口（同上）。保留定义保行号稳定，勿新增引用。
WINDOW_LONG = 20       # 长线窗口
# [ARCHIVED 2026-09-25 G1] 死配置：全库 grep（83 文件，含字符串/getattr 形态）+工作区/skills 交叉核实均无引用。原用途：均线权重（同上）。保留定义保行号稳定，勿新增引用。
WEIGHT_SHORT = 0.6     # 短线权重
# [ARCHIVED 2026-09-25 G1] 死配置：全库 grep（83 文件，含字符串/getattr 形态）+工作区/skills 交叉核实均无引用。原用途：均线权重（同上）。保留定义保行号稳定，勿新增引用。
WEIGHT_LONG = 0.4      # 长线权重

# ============================================================
# 6. IV/波动率监控配置
# ============================================================
IV_OPTION_ETFS = {
    '510050': {'name': '上证50ETF',  'exchange': 'SSE', 'tushare_code': '510050.SH'},
    '510300': {'name': '沪深300ETF', 'exchange': 'SSE', 'tushare_code': '510300.SH'},
    '510500': {'name': '中证500ETF', 'exchange': 'SSE', 'tushare_code': '510500.SH'},
    '588000': {'name': '科创50ETF',  'exchange': 'SSE', 'tushare_code': '588000.SH'},
    '159915': {'name': '创业板ETF',  'exchange': 'SZSE', 'tushare_code': '159915.SZ'},
}
IV_HV_PROXY_ETFS = {
    '159992': {'name': '创新药ETF',   'tushare_code': '159992.SZ'},
    '512760': {'name': '芯片ETF',     'tushare_code': '512760.SH'},
}
IV_STABLE_BASE = 20.0
IV_DISPERSION_THRESHOLD = 33.0
IV_MA_WINDOWS = [5, 20, 60]
IV_HV_LOOKBACK = 20
RISK_FREE_RATE = 0.015

# ============================================================
# 7. 全量下载配置 (Tushare频率限制)
# ============================================================
TS_RATE_LIMIT_PER_MIN = 200          # 默认限频 (2000积分档 → 200次/分钟)
# [ARCHIVED 2026-09-25 G1] 死配置：全库 grep（83 文件，含字符串/getattr 形态）+工作区/skills 交叉核实均无引用。原用途：旧限频参数（现行限频走 RATE_MAP）。保留定义保行号稳定，勿新增引用。
TS_RATE_INTERVAL_SEC = 60.0 / TS_RATE_LIMIT_PER_MIN
TS_DAILY_LIMIT = 90000               # 每日总量上限 (留10%安全余量)
TS_MAX_RETRIES = 5
TS_RETRY_DELAYS = [1, 2, 5, 10, 20]  # 普通错误退避(秒) — 2026-09-15 由[30,60,120,300,600]改短

# ------------------------------------------------------------
# 按接口限频表 (2026-09-15 实测服务端限额 × 0.85 安全系数)
# ------------------------------------------------------------
# 实测方法: 多线程持续跑满 + 读服务端报错原文
#   报错原文形如: 您访问接口(daily)频率超限(300次/分钟)
# 实测值:
#   daily          → 300次/分
#   dividend       → 200次/分
#   moneyflow      → 200次/分
#   margin_detail  → 200次/分
#   其余一般接口    → 200次/分 (默认)
# 未在表中列出的接口一律走默认 TS_RATE_LIMIT_PER_MIN × 0.85
#
# ---- 「1次/小时」族的统一取值（2026-09-21 新增，防再次手写 0.02）------
# 语义: 本表的值是**次/分钟**，限频器按 `间隔 = 60 / rate` 换算。
# 真实限频 1次/小时 = 3600 秒/次 ⇒ 要求 `60 / rate ≥ 3600`。
# ❌ 历史 bug: 手写 0.02 → 间隔 3000 秒（50 分钟）< 3600 →
#    第 1 条之后每一条调用必被服务端拒绝，且 verbose=False 时**静默无日志**。
#    实证 shibor_quote 按年切 11 个请求只成 1 个（2016），曾被误判为权限边界。
# ✅ 60/3660 → 间隔 3660 秒（61 分钟），留 60 秒余量防边界抖动。
RATE_1_PER_HOUR = 60 / 3660          # ≈0.016393 次/分

API_RATE_LIMIT = {
    "daily":         250,   # 实测服务端 300 —— 唯一确认高于200的常用接口, 提速点
    "daily_basic":   170,
    "fund_daily":    170,
    "fund_nav":      170,
    "index_daily":   170,
    "cb_daily":      170,
    "margin":        170,
    "margin_detail": 170,
    "moneyflow":     170,
    "dividend":      170,
    "stk_limit":     170,
    "adj_factor":    170,
    # ⛔ 极低限频接口（2026-09-19 实测）—— 不要"优化"成 170：会触发限频重试
    #    反而更慢（实测连续12次退避全部失败）
    #
    # ⚠⚠ 2026-09-21 修正「1次/小时族」的取值（重要，此前一直配错）:
    #   本值语义 = **次/分钟**，限频器按 `间隔 = 60 / rate` 换算。
    #   真实限频是 **1次/小时 = 3600 秒/次**，故必须有 `60/rate ≥ 3600`。
    #   ❌ 原写 0.02 → 间隔 60/0.02 = **3000 秒（50 分钟）< 3600** →
    #      **第 1 条之后每一条调用都必然被服务端拒绝**，
    #      表现为"只成 1 个、其余全失败"，且在 verbose=False 下**静默无任何日志**。
    #      实证：shibor_quote 按年切 11 个请求，只有 2016 成功
    #      （错误地被误判为"权限只能取到 2016 年"，实为本取值缺陷）。
    #   ✅ 修正为 60/3660 → 间隔 3660 秒（61 分钟），留 60 秒余量防边界抖动。
    #   ⚠ 凡"1次/小时"接口一律用 RATE_1_PER_HOUR 常量，勿再手写 0.02。
    "shibor_quote":    RATE_1_PER_HOUR,
    "hibor":           RATE_1_PER_HOUR,
    # 2026-09-20 实测复核：与 shibor_quote 同族，真实限频 **1次/小时**
    # （原 config 注明"1次/分钟"有误；实证按年切分 11 个请求只成 1 个
    #   —— 根因同 shibor_quote：原 0.02→3000s 不足 3600s，已一并修正）
    "shibor_lpr":      RATE_1_PER_HOUR,
    # 实测 1次/小时（第 1 次调用报“1次/分钟”，65 秒后才是真值，差 60 倍）。
    # 含义：历史回补不可行（9041 交易日≈377天），但日更可行（每天 1~2 次）。
    # ⚠ 同上修正 0.02→RATE_1_PER_HOUR；原值导致 us_daily 的**连续日回补必然失败**
    #   （只有第 1 天能成，这也是它长期 0 文件的直接原因之一）。
    "us_daily":        RATE_1_PER_HOUR,
    # index_global 实测 10次/分钟（远低于默认170）。
    # ⚠ 另有 **100次/天** 日限 —— 这是比分钟限频更紧的约束，极易被忽略。
    "index_global":      8,      # 10 × 0.8 安全系数
}
API_RATE_LIMIT_DEFAULT = 170         # 兜底: 200 × 0.85

# ------------------------------------------------------------
# 「接受截断」的接口清单 — 2026-09-22 新增
# ------------------------------------------------------------
# 用途: 这些接口**命中单次上限属预期**，ts_fetch 检测到截断后**不再往前续拉**。
#
# 为什么需要（实证踩坑）:
#   ts_fetch 的截断检测会在"返回行数命中上限"时用 end_date **逐段往前续拉**
#   （TRUNC_MAX_ROUNDS=40 轮）。这对"应该拿到全历史"的表是对的，
#   但对**只需最新数据**的表会**灾难性放大**：
#
#   实证 index_global（2026-09-22）：
#     · 该接口单次硬顶 4000 行，**总是返回「最新 4000 行」**（实测覆盖约 16 年）
#     · 原配置 note 假设"限定2016起各约2700行不截断"——**该假设是错的**：
#       实测 2010-10~2026-09 恰好 4000 行，且 `end_date=20101025` 续拉**仍返 4000 行**
#       （说明源端确有更早数据 → 属真截断）
#     · ⇒ 触发续拉 → 每轮等限频 7.5 秒（8次/分）× 40 轮 × 22 指数
#        ≈ **110 分钟**，且 880 请求 >> 该接口 **100次/天** 配额 → 必然烧穿
#       （实测：跑 10 分钟仍未完成 index_global，被迫中止）
#
# 判据: 只要该表"日更只需最新数据、更早历史对下游无价值"，就应登记在此。
ACCEPT_TRUNCATED_APIS = {
    "index_global": "单次硬顶 4000 行 = 最新 4000 行(实测约 16 年)，"
                    "对「国际指数行情」用途足够；续拉更早历史无价值且会撞 100次/天 配额",
}


# 口径说明:
#   - 除 daily 外一律 170 (= 旧全局值 171 的等效值), 与改造前行为一致, 零回归风险
#   - daily 提到 250 是本次唯一提速点, 实测服务端 300, 留 17% 安全系数
#   - 实测确认 dividend/moneyflow/margin_detail 服务端也是 200, 但单次返回重、
#     延迟高, 170 已跑不满限额, 无需上调

# 限频(429)专用短退避(秒): 限频是"分钟窗口"，等 1~3 秒即可重试，
# 不需要走 TS_RETRY_DELAYS 的分钟级等待。
TS_RATE_BACKOFF = [1.0, 1.5, 2.0, 3.0, 5.0]
TS_RATE_MAX_RETRIES = 12             # 限频场景允许的重试次数(远高于普通错误)

# 并发拉取: 每接口线程数 (实测 4 线程打满、8 线程崩)
FETCH_MAX_WORKERS = 4

# 补数专用: 同时跑几个接口
# 2026-09-16 由 8 降到 4 —— 8 接口并行时磁盘 I/O 争抢严重, 反而拖慢
BACKFILL_PARALLEL_TARGETS = 4

# 全量下载阶段定义
from collections import OrderedDict
DOWNLOAD_PHASES = OrderedDict([
    ("meta", {
        "desc": "元数据",
        "apis": [],
    }),
    ("daily", {
        "desc": "日线行情 + 每日指标",
        "apis": [
            {"api": "daily",           "desc": "个股日线",   "max_rows": 5000},
            {"api": "daily_basic",     "desc": "每日指标",   "max_rows": 6000},
            {"api": "fund_daily",      "desc": "基金日线",   "max_rows": 5000},
        ],
    }),
    ("fina", {
        "desc": "财务数据",
        "apis": [
            {"api": "income",          "desc": "利润表",     "max_rows": 500},
            {"api": "balancesheet",    "desc": "资产负债表", "max_rows": 500},
            {"api": "cashflow",        "desc": "现金流量表", "max_rows": 500},
            {"api": "fina_indicator",  "desc": "财务指标",   "max_rows": 500},
        ],
    }),
])

# 可转债下载配置
CB_DAILY_MAX_ROWS = 2000

# ============================================================
# 7b. 补数目标清单 (backfill) — 2026-09-15 新增
# ============================================================
# 用途: 把「有权限但未入库」的接口批量补进全量库。
# 引擎: fetch/backfill.py
#
# 字段说明:
#   api       Tushare 接口名
#   desc      中文说明
#   mode      取数模式
#               by_code  = 参数 ts_code=<code>, 代码来自 codes 列表
#               by_date  = 参数 trade_date=<交易日>, 一次拉全市场, 按 ts_code 分组落盘
#               by_month = 参数 <code_param>=<code> + trade_date=<月末>, 代码来自 codes 列表
#               once     = 一次调用, 整体保存为单文件
#   codes     代码来源: stock / fund_etf / index / fx / cb / cb_listed / none
#   code_param by_month 模式的代码参数名 (默认 ts_code)
#   max_rows  单次返回上限(用于截断检测)。
#             >0 = 声明的上限；0 = 未声明 —— ⚠ 仍会按「服务端常见上限特征值」检测
#             （2026-09-19 起，见 fetch/base.SUSPECT_CAPS），不再等于"不检测"
#   tier      分档: A=策略直接咬合(先拉) / B=扩展 / C=不建议
#   start     起始日期 (by_date / by_month 模式)
#   note      备注/风险提示
# [ARCHIVED 2026-09-25 G1] 死配置：全库 grep（83 文件，含字符串/getattr 形态）+工作区/skills 交叉核实均无引用。原用途：旧补数默认起点（现行 backfill 引擎自带断点起点）。保留定义保行号稳定，勿新增引用。
BACKFILL_START_DEFAULT = "20160101"   # 近10年

BACKFILL_TARGETS = OrderedDict([
    # ---------------- A档: 与现有策略体系直接咬合 ----------------
    ("dividend", {
        "api": "dividend", "desc": "分红送股", "mode": "by_code",
        "codes": "stock_all", "max_rows": 5000, "tier": "A",
        "note": "红利低波/奇点战法的股息率原始输入",
    }),
    ("index_dailybasic", {
        "api": "index_dailybasic", "desc": "指数每日指标(PE/PB/股息率)", "mode": "by_date",
        "start": "20040102", "max_rows": 0, "tier": "A",
        "note": "奇点估值锚点: 沪深300 10倍PE 可直接自算。"
                "★2026-09-23 纳入日更(extend组): mode 由 by_code 改 by_date —— "
                "实测接口支持 trade_date 按日拉全市场(单日约15只指数, 1请求/天)。"
                "弃用 by_code 日更的原因: 单标的全历史约5400行 > 服务端实际上限3000, "
                "每天全量重拉会触发截断续拉(约30+请求/天)。"
                "⚠日更每天返15只指数, 其中7只为库内原有8只之外的新序列(历史从当日起), 属预期。"
                "start 与 SOURCE_START_BASELINE 保持一致(20040102)。",
    }),
    ("index_weight", {
        "api": "index_weight", "desc": "指数成分与权重", "mode": "by_month",
        "codes": "index_major", "code_param": "index_code",
        "start": "20160101", "max_rows": 5000, "tier": "A",
        "subset": "*",
        "note": "必须用 trade_date 参数; 单月多行, 需全列判重",
    }),
    ("margin", {
        "api": "margin", "desc": "融资融券汇总", "mode": "by_date",
        "max_rows": 0, "tier": "A", "start": "20120101",
        "subset": "*",
        "note": "β四因子的杠杆热度; 无ts_code, 单日2行(交易所)",
    }),
    ("margin_detail", {
        "api": "margin_detail", "desc": "融资融券明细", "mode": "by_date",
        "max_rows": 0, "tier": "A", "start": "20120101",
        "note": "单日约4400行",
    }),
    ("margin_secs", {
        "api": "margin_secs", "desc": "两融标的清单", "mode": "by_date",
        "max_rows": 0, "tier": "B", "start": "20120101",
        "note": "单日约4450行",
    }),
    ("moneyflow", {
        "api": "moneyflow", "desc": "个股资金流向", "mode": "by_date",
        "max_rows": 0, "tier": "A", "start": "20080101",
        "note": "单日约5540行; β四因子的资金流",
    }),
    ("fund_adj", {
        "api": "fund_adj", "desc": "基金复权因子", "mode": "by_code",
        "codes": "fund_etf", "max_rows": 5000, "tier": "A",
        "note": "宽基ETF轮动必须用复权价",
    }),
    ("fund_share", {
        "api": "fund_share", "desc": "基金份额规模", "mode": "by_code",
        "codes": "fund_etf", "max_rows": 5000, "tier": "A",
        "note": "汇金ETF化趋势判断口径",
    }),
    ("fx_daily", {
        "api": "fx_daily", "desc": "外汇日线", "mode": "by_date",
        "start": "20070521", "max_rows": 0, "tier": "A",
        "note": "巴西ETF证伪条件 BRLCNY 破1.25连5日。"
                "★2026-09-23 纳入日更(extend组): mode 由 by_code 改 by_date —— "
                "实测接口支持 trade_date 按日拉全市场(单日约101品种, 1请求/天)。"
                "⚠库内 1938~2007 稀疏历史(XAUUSD伦敦金等)系当年 by_code 建库产物, "
                "日更不受影响; 若整库重建需临时改回 by_code(69请求/标的拉全历史)。"
                "⚠缺口按 SSE 日历计算: A股长假(国庆/春节)期间外汇照常交易但不会被自动补, "
                "如需覆盖手动 --lookback 扩窗。start 与 SOURCE_START_BASELINE 一致(20070521)。",
    }),
    ("forecast", {
        "api": "forecast", "desc": "业绩预告", "mode": "by_code",
        "codes": "stock_all", "max_rows": 5000, "tier": "A",
        "note": "涛涛战法业绩雷规避",
    }),
    ("disclosure_date", {
        "api": "disclosure_date", "desc": "财报披露日期表", "mode": "by_period",
        "period_param": "end_date", "date_col": "end_date",
        "start": "20160101", "max_rows": 0, "tier": "A",
        "subset": "*", "cap": 6000,
        "note": ("⚠ 无 trade_date 参数(传了被静默忽略, 返回全表前6000行=1990年代数据); "
                 "end_date 是【精确匹配报告期】非区间; 按季报报告期逐期拉, 按 end_date 分年"),
    }),
    ("hk_basic", {
        "api": "hk_basic", "desc": "港股基础信息", "mode": "once",
        "max_rows": 0, "tier": "A", "note": "三件套里的港股行情前置",
    }),

    # ---------------- A档补充: 事件面 ----------------
    ("top_list", {
        "api": "top_list", "desc": "龙虎榜每日统计", "mode": "by_date",
        "max_rows": 0, "tier": "B", "start": "20080101",
        "subset": "*", "note": "游资/机构介入; 同股同日可多行(多上榜原因)",
    }),
    ("top_inst", {
        "api": "top_inst", "desc": "龙虎榜机构席位", "mode": "by_date",
        "max_rows": 0, "tier": "B", "start": "20120101",
        "subset": "*", "note": "同股同日多营业部, 必须全列判重",
    }),
    ("block_trade", {
        "api": "block_trade", "desc": "大宗交易", "mode": "by_date",
        "max_rows": 0, "tier": "B", "start": "20050101",
        "subset": "*", "note": "折价率/接盘方; 同股同日同价可多笔",
    }),
    ("hsgt_top10", {
        "api": "hsgt_top10", "desc": "陆股通十大成交股", "mode": "by_date",
        "max_rows": 0, "tier": "B", "start": "20170101", "note": "外资当日偏好",
    }),
    ("ggt_top10", {
        "api": "ggt_top10", "desc": "港股通十大成交股", "mode": "by_date",
        "max_rows": 0, "tier": "B", "start": "20170101",
        "subset": "*", "note": "同股可多行(市场类型不同)",
    }),
    ("hk_hold", {
        "api": "hk_hold", "desc": "沪深股通持股明细", "mode": "by_date",
        "max_rows": 0, "tier": "B", "start": "20170101", "note": "单日约960行",
    }),
    ("stk_holdernumber", {
        "api": "stk_holdernumber", "desc": "股东户数", "mode": "by_code",
        "codes": "stock_all", "max_rows": 5000, "tier": "B", "note": "筹码集中度代理",
    }),
    ("stk_holdertrade", {
        "api": "stk_holdertrade", "desc": "股东增减持", "mode": "by_code",
        "codes": "stock_all", "max_rows": 5000, "tier": "B", "note": "",
    }),
    ("pledge_stat", {
        "api": "pledge_stat", "desc": "股权质押统计", "mode": "by_code",
        "codes": "stock_all", "max_rows": 5000, "tier": "B", "note": "爆仓风险",
    }),
    ("share_float", {
        "api": "share_float", "desc": "限售股解禁", "mode": "by_code",
        "codes": "stock_all", "max_rows": 5000, "tier": "B", "note": "解禁压力预判",
    }),
    ("repurchase", {
        "api": "repurchase", "desc": "股票回购", "mode": "by_code",
        "codes": "stock_all", "max_rows": 5000, "tier": "B", "note": "",
    }),
    ("broker_recommend", {
        "api": "broker_recommend", "desc": "券商月度金股", "mode": "by_month",
        "codes": "none", "code_param": "month", "start": "20160101",
        "max_rows": 0, "tier": "B", "subset": "*",
        "note": "按 month 参数拉, 非 trade_date; 无ts_code",
    }),
    ("suspend_d", {
        "api": "suspend_d", "desc": "每日停复牌", "mode": "by_date",
        "max_rows": 0, "tier": "B", "start": "20000101", "note": "接口名是 suspend_d",
    }),
    ("namechange", {
        "api": "namechange", "desc": "股票曾用名", "mode": "by_code",
        "codes": "stock_all", "max_rows": 0, "tier": "B", "note": "ST/借壳识别",
    }),
    ("new_share", {
        "api": "new_share", "desc": "IPO新股上市", "mode": "by_range",
        "start_param": "start_date", "end_param": "end_date",
        "start": "20160101", "max_rows": 0, "tier": "B",
        "subset": ["ts_code"],
        "note": "⚠无trade_date列(只有ipo_date); trade_date参数被静默忽略并返回截断的2000行, 必须用区间参数",
    }),

    # ---------------- 财务补齐 ----------------
    ("fina_mainbz", {
        "api": "fina_mainbz", "desc": "主营业务构成", "mode": "by_code",
        "codes": "stock_all", "max_rows": 5000, "tier": "B", "note": "分部收入",
    }),
    ("fina_audit", {
        "api": "fina_audit", "desc": "财务审计意见", "mode": "by_code",
        "codes": "stock_all", "max_rows": 5000, "tier": "B", "note": "非标意见=红旗",
    }),
    ("stock_company", {
        "api": "stock_company", "desc": "公司基本信息", "mode": "by_code",
        "codes": "stock_all", "max_rows": 0, "tier": "B", "note": "",
    }),
    ("stk_managers", {
        "api": "stk_managers", "desc": "上市公司管理层", "mode": "by_code",
        "codes": "stock_all", "max_rows": 5000, "tier": "B",
        "note": "公司治理维度; 按 ann_date 分区; 实测 600519.SH 返147行",
    }),
    ("express", {
        "api": "express", "desc": "业绩快报", "mode": "by_code",
        "codes": "stock_all", "max_rows": 5000, "tier": "B",
        "note": "业绩雷规避(披露早于正式财报); ⚠end_date参数不可靠"
                "(实测返2000行且混入20241231/20250331等其他报告期), 故按标的拉",
    }),
    ("pledge_detail", {
        "api": "pledge_detail", "desc": "股权质押明细", "mode": "by_code",
        "codes": "stock_all", "max_rows": 5000, "tier": "B",
        "note": "pledge_stat 的明细版(含质权人/解押状态); "
                "⚠无参数与区间参数都被忽略(均返最近1500行), 只能按标的拉",
    }),

    # ---------------- 跨品种: 期货/期权/债券 ----------------
    ("fut_basic", {
        "api": "fut_basic", "desc": "期货合约基础信息", "mode": "by_param",
        "param_name": "exchange",
        "param_values": ["CFFEX", "CZCE", "DCE", "SHFE", "INE", "GFEX"],
        "page_size": 2000, "max_rows": 0, "tier": "A", "subset": ["ts_code"],
        "note": "⚠无参数单次返10000会静默截断(实测按交易所合计11275); 按交易所+offset分页",
    }),
    ("fut_mapping", {
        "api": "fut_mapping", "desc": "期货主力连续映射", "mode": "by_date",
        "max_rows": 0, "tier": "A", "start": "20000101",
        "note": "IF/IC/IM 基差与升贴水; 按trade_date拉, 每日约150-200行",
    }),
    ("fut_settle", {
        "api": "fut_settle", "desc": "期货结算参数", "mode": "by_date",
        "max_rows": 0, "tier": "B", "start": "20120101",
        "note": "保证金率/手续费/交割费; trade_date 有效(实测单日约870行); "
                "⚠无参数会报'参数校验失败, trade_date,ts_code不能都为空'",
    }),
    ("fut_trade_cal", {
        "api": "fut_trade_cal", "desc": "期货交易日历", "mode": "by_param",
        "param_name": "exchange",
        "param_values": ["CFFEX", "CZCE", "DCE", "SHFE", "INE", "GFEX", "SSE", "SZSE"],
        "page_size": 5000, "max_rows": 0, "tier": "B",
        "subset": ["exchange", "cal_date"],
        "note": "⚠无参数时**只返回 SSE**（实测 13527 行全是 SSE），必须按交易所逐个拉; "
                "单次可返 1.3 万行不截断（CZCE 13594 行），但为稳妥仍走 offset 分页",
    }),
    ("opt_basic", {
        "api": "opt_basic", "desc": "期权合约基础信息", "mode": "by_param",
        "param_name": "exchange",
        "param_values": ["SSE", "SZSE", "CFFEX"],
        "page_size": 2000, "max_rows": 0, "tier": "A", "subset": ["ts_code"],
        "note": "⚠trade_date参数被静默忽略, 只能按交易所+offset分页; "
                "仅取ETF期权(SSE/SZSE)+股指期权(CFFEX)共30730行, "
                "商品期权(DCE/CZCE/SHFE/INE/GFEX)各4万+历史合约与策略无关故不拉",
    }),
    ("fut_daily", {
        "api": "fut_daily", "desc": "期货日线", "mode": "by_date",
        "max_rows": 0, "tier": "B", "start": "20000101", "note": "IF/IC/IM 基差",
    }),
    ("fut_holding", {
        "api": "fut_holding", "desc": "期货持仓排名", "mode": "by_date",
        "max_rows": 0, "tier": "C", "start": "20050101", "note": "单日约4000行, 价值有限",
    }),
    ("fut_wsr", {
        "api": "fut_wsr", "desc": "期货仓单日报", "mode": "by_date",
        "max_rows": 0, "tier": "C", "start": "20100101", "note": "商品库存",
    }),
    ("opt_daily", {
        "api": "opt_daily", "desc": "期权日线", "mode": "by_date",
        "max_rows": 0, "tier": "C", "start": "20160101", "note": "单日约1.5万行, 量大",
    }),
    ("repo_daily", {
        "api": "repo_daily", "desc": "债券回购日行情", "mode": "by_date",
        "max_rows": 0, "tier": "C", "start": "20000101", "note": "资金面利率",
    }),
    ("cb_issue", {
        "api": "cb_issue", "desc": "可转债发行", "mode": "by_code",
        "codes": "cb", "max_rows": 0, "tier": "A", "note": "四维评估补充",
    }),
    ("cb_share", {
        "api": "cb_share", "desc": "可转债份额", "mode": "by_code",
        "codes": "cb", "max_rows": 0, "tier": "A", "note": "剩余规模",
    }),
    ("cb_rating", {
        "api": "cb_rating", "desc": "可转债评级", "mode": "by_code",
        "codes": "cb", "max_rows": 0, "tier": "A", "note": "四维评估的评级维",
    }),

    # ---------------- 宏观 ----------------
    ("cn_cpi", {"api": "cn_cpi", "desc": "CPI", "mode": "once", "max_rows": 0, "tier": "B"}),
    ("cn_ppi", {"api": "cn_ppi", "desc": "PPI", "mode": "once", "max_rows": 0, "tier": "B"}),
    ("cn_m",   {"api": "cn_m",   "desc": "货币供应", "mode": "once", "max_rows": 0, "tier": "B"}),
    ("cn_pmi", {"api": "cn_pmi", "desc": "PMI", "mode": "once", "max_rows": 0, "tier": "B"}),
    ("cn_gdp", {"api": "cn_gdp", "desc": "GDP", "mode": "once", "max_rows": 0, "tier": "B"}),
    ("sf_month", {"api": "sf_month", "desc": "社融", "mode": "once", "max_rows": 0, "tier": "B"}),
    ("shibor", {
        "api": "shibor", "desc": "Shibor", "mode": "by_range",
        "start_param": "start_date", "end_param": "end_date",
        "start": "20160101", "max_rows": 0, "tier": "B", "subset": "*",
        "note": "不支持 trade_date 参数, 必须用 start_date/end_date 区间",
    }),
    ("us_tycr", {
        "api": "us_tycr", "desc": "美国国债收益率", "mode": "by_range",
        "start_param": "start_date", "end_param": "end_date",
        "start": "20160101", "max_rows": 0, "tier": "B", "subset": "*",
        "note": "不支持 trade_date 参数, 必须用 start_date/end_date 区间",
    }),
    ("us_trycr", {
        "api": "us_trycr", "desc": "美债实际收益率曲线", "mode": "by_range",
        "start_param": "start_date", "end_param": "end_date",
        "start": "20160101", "max_rows": 0, "tier": "A", "subset": "*",
        "note": "5/7/10/20/30年期实际利率; 同 us_tycr 用法",
    }),
    ("us_tbr", {
        "api": "us_tbr", "desc": "美债短期收益率(周频)", "mode": "by_range",
        "start_param": "start_date", "end_param": "end_date",
        "start": "20160101", "max_rows": 0, "tier": "A", "subset": "*",
        "note": "4/8/13/17/26/52周短债收益率",
    }),
    ("us_tltr", {
        "api": "us_tltr", "desc": "美债长期收益率", "mode": "by_range",
        "start_param": "start_date", "end_param": "end_date",
        "start": "20160101", "max_rows": 0, "tier": "A", "subset": "*",
        "note": "ltc/cmt 长债利率",
    }),
    ("us_trltr", {
        "api": "us_trltr", "desc": "美债超长期实际收益率", "mode": "by_range",
        "start_param": "start_date", "end_param": "end_date",
        "start": "20160101", "max_rows": 0, "tier": "A", "subset": "*",
        "note": "ltr_avg 超长期实际利率",
    }),
    ("gz_index", {"api": "gz_index", "desc": "广州民间利率", "mode": "once",
                  "max_rows": 0, "tier": "C",
                  "note": "1181行(2013-04~2019-03, 已停更), 未触及单次上限"}),
    # ⚠ wz_index 不可用 once（2026-09-19 实证）：
    #   服务端单次硬上限 3000 行（传 limit=5000 仍只返 3000），而该表是**日度**
    #   数据（中位间隔 1 天）、总量 3160 行 —— once 模式会静默截断在 3000，
    #   丢失 2026-02~09 约 8 个月数据（实证 offset=3100 仍返数据且日期到 20260917）。
    #   改为 paged：page_size 必须 ≤ 服务端上限 3000，否则同样截断。
    ("wz_index", {
        "api": "wz_index", "desc": "温州民间利率", "mode": "paged",
        "page_size": 3000, "page_count": 2, "max_rows": 0, "tier": "C",
        "subset": "*",
        "note": "⚠单次硬上限3000(实测传limit=5000也只返3000); 日度数据总量3160行=2页",
    }),
    ("shibor_quote", {
        "api": "shibor_quote", "desc": "Shibor报价(各银行各期限)", "mode": "by_range",
        "start_param": "start_date", "end_param": "end_date",
        "start": "20160101", "max_rows": 0, "tier": "X", "enabled": False,
        "subset": "*",
        "note": "⏸⏸ 2026-09-23 老大决策: **搁置（SHELVED）**—— 并非放弃, 处理方案待定; "
                "在方案敲定前不补库、不日更、不删除, 维持 2016 现状。"
                "(表内容=每日18家报价行对 O/N~1Y 八期限的 bid/ask 原始报价, "
                "仅比 shibor 主表多『报价行』微观维度; shibor 主表已在日更覆盖到最新)"
                "⛔限频1次/小时。★2026-09-21 更正确认: 此前『11个年度请求只成1个(2016)』"
                "**不是权限边界, 而是本表限频取值缺陷** —— 原 API_RATE_LIMIT 写 0.02 "
                "→ 间隔3000秒(50分钟) < 真实要求3600秒 → 第1条之后每条调用必被拒 "
                "(且 verbose=False 时静默无日志, 故当时误判为『权限只能取到2016年』)。"
                "已改为 RATE_1_PER_HOUR(间隔3660秒)。**结论: 补库在修正后是可行的**, "
                "按年切11段需 11×61分钟 ≈ 11.2 小时(1次/小时硬约束)。"
                "⚠ 2016年落盘4000行=单次上限特征值, 该年可能被截断; "
                "且 shibor 已有日频汇总值(覆盖到2026), 本接口仅多出『报价行』维度。"
                "是否值得 11.2 小时请按需决定。",
    }),
    ("hibor", {
        "api": "hibor", "desc": "Hibor利率(香港银行同业)", "mode": "by_range",
        "start_param": "start_date", "end_param": "end_date",
        "start": "20160101", "max_rows": 0, "tier": "X", "enabled": False,
        "subset": "*",
        "note": "⛔限频1次/小时(同 shibor_quote)。★2026-09-21 更正确认: 此前『只有2016年有数据』"
                "**不是权限边界, 而是限频取值缺陷**(原 0.02→间隔3000秒 < 真实3600秒, "
                "第1条之后必被拒且静默无日志)。已改为 RATE_1_PER_HOUR(间隔3660秒)。"
                "补库修正后可行, 按年切11段需约 11.2 小时。"
                "港股资金面请优先用 HIBOR 官网或 akshare 替代。",
    }),
    ("hk_tradecal", {
        "api": "hk_tradecal", "desc": "港股交易日历", "mode": "by_range",
        "start_param": "start_date", "end_param": "end_date",
        "start": "20160101", "max_rows": 0, "tier": "B", "subset": "*",
        "note": "港股三件套必需; 区间有效(⚠无参数返2000顶上限)",
    }),

    # ---------------- 验算数据: 官方周线/月线（校验本地日线聚合）----------------
    # 用途: 与"本地由 daily/index_daily 聚合出的周线/月线"做交叉比对，
    #       验证聚合逻辑（除权处理、首尾日、停牌周等）是否正确。
    # 取数: 用 freq=week/month 只拉每周末/月末那一个交易日（避免逐日重复 5/20 倍）
    ("weekly", {
        "api": "weekly", "desc": "个股周线(验算用)", "mode": "by_date",
        "freq": "week", "max_rows": 0, "tier": "B", "start": "20160101",
        "subset": "*",
        "note": "验算数据: 与本地 daily 聚合对比; 每周末拉全市场(实测约5634行/周)",
    }),
    ("monthly", {
        "api": "monthly", "desc": "个股月线(验算用)", "mode": "by_date",
        "freq": "month", "max_rows": 0, "tier": "B", "start": "20160101",
        "subset": "*",
        "note": "验算数据: 与本地 daily 聚合对比",
    }),
    ("index_weekly", {
        "api": "index_weekly", "desc": "指数周线(验算用)", "mode": "by_code",
        "codes": "index", "max_rows": 1000, "tier": "B",
        "subset": ["ts_code", "trade_date"],
        "note": "⚠单次上限1000行, 靠 ts_fetch 的 end_date 续拉(已验证 end_date 有效); "
                "仅拉13个关注指数 —— 全市场10862个指数按周拉会爆请求数",
    }),
    ("index_monthly", {
        "api": "index_monthly", "desc": "指数月线(验算用)", "mode": "by_code",
        "codes": "index", "max_rows": 1000, "tier": "B",
        "subset": ["ts_code", "trade_date"],
        "note": "仅拉13个关注指数; 实测单指数296行不截断",
    }),

    # ---------------- A档补充: 国际指数 / 行业分类 / 沪深港通成分 ----------------
    ("index_global", {
        "api": "index_global", "desc": "国际指数行情", "mode": "by_code",
        "codes": "index_global", "max_rows": 4000, "tier": "A",
        "start": "20160101", "subset": ["ts_code", "trade_date"],
        # ⚠ 2026-09-22 新增: 标记为「每日全量重拉」类（daily_update 专用）。
        #   为什么必须标记: 本表 mode=by_code，而 daily_update 原只支持
        #   by_date/by_range → 直接加进 DAILY_UPDATE_GROUPS 会被**静默跳过**
        #   （即"假纳入"，R10 判据⑤能抓到）。
        #   为什么用"全量重拉"而非"算缺口": 该接口**只能按标的一次拉全历史**
        #   （不传日期参数；2016 起各约 2700 行，在 4000 硬顶内不截断），
        #   没有"某一天"的入参形态 → 全量重拉才是正确的日更形态，
        #   且落盘按 (ts_code, trade_date) 去重、天然幂等。
        #   ⚠ 成本 22 请求/天（该接口双重限频 10次/分 + 100次/天，占配额 22%）。
        #     **切勿加复核窗口**（会变 66 请求、占配额 66% 接近危险区）——
        #     全量重拉本身已是最彻底的复核。
        "daily_full_refresh": True,
        "note": "22个国际指数(SPX/IXIC/HSI/HKTECH/IBOVESPA等); "
                "⚠双重限频 10次/分 + **100次/天**; "
                "⚠单次硬顶 4000 行 = **「最新 4000 行」**(实测 2010-10~2026-09 约 16 年) "
                "—— 原注『限定2016起各约2700行不截断』**已证伪** "
                "(实测 end_date 续拉仍返 4000 行 → 属真截断); "
                "已登记 config.ACCEPT_TRUNCATED_APIS 接受该上限、**不续拉** "
                "(续拉会 22指数×40轮×7.5s≈110分钟 且撞 100次/天 配额); "
                "⚠含 IBOVESPA(持仓 520870/159100 的标的指数); "
                "每日全量重拉 22 请求(daily_full_refresh)",
    }),
    ("index_member_all", {
        "api": "index_member_all", "desc": "申万行业分类成分", "mode": "paged",
        "page_size": 3000, "page_count": 2, "max_rows": 0, "tier": "A",
        "subset": ["ts_code"],
        "note": "⚠单次上限3000(实测总5913行=2页); 含一/二/三级行业+个股映射; "
                "可替代 akshare 取申万分类",
    }),
    ("hs_const", {
        "api": "hs_const", "desc": "沪深港通成分股", "mode": "by_param",
        "param_name": "hs_type", "param_values": ["SH", "SZ"],
        "page_size": 2000, "max_rows": 0, "tier": "A",
        "subset": ["ts_code", "hs_type"],
        "note": "⚠必填 hs_type(不传直接报错); SH 581行 / SZ 242行; "
                "注意 hs_type='SH_SZ' 返空表（非合法值）",
    }),

    # ---------------- B档补充: 元数据 ----------------
    ("us_basic", {
        "api": "us_basic", "desc": "美股基础信息", "mode": "paged",
        "page_size": 6000, "page_count": 5, "max_rows": 0, "tier": "B",
        # ⚠ subset 必须全列（2026-09-19 修正）：
        #   原为 ["ts_code"]，但实测该键有 55 处不唯一（如 ABX 键下 2 行内容不同
        #   = 业务多行）。而 merge_append 在文件已存在时按 subset 去重 ——
        #   一旦重跑全量（清断点/断点损坏触发），会把旧+新按 ts_code 合并去重，
        #   静默删掉 54 行非冗余数据。改全列后只删“每列都相同”的行，安全。
        "subset": "*",
        "note": "⚠单次上限6000(实测总24256行=5页); 仅基础信息, "
                "美股行情 us_daily 无权限；ts_code 不唯一(55处)，故全列去重",
    }),
    ("us_tradecal", {
        "api": "us_tradecal", "desc": "美股交易日历", "mode": "by_range",
        "start_param": "start_date", "end_param": "end_date",
        "start": "20160101", "max_rows": 0, "tier": "B", "subset": "*",
        "note": "区间有效(实测2016起3914行)",
    }),
    ("fund_company", {
        "api": "fund_company", "desc": "基金公司信息", "mode": "once",
        "max_rows": 0, "tier": "B", "subset": "*",
        "note": "207行, 一次拉完",
    }),
    ("fund_manager", {
        "api": "fund_manager", "desc": "基金经理信息", "mode": "paged",
        "page_size": 5000, "page_count": 18, "max_rows": 0, "tier": "B",
        "subset": "*",
        "note": "⚠单次上限5000(实测总85636行=18页)",
    }),

    # ---------------- C档: 中低价值但可补 ----------------
    ("stk_rewards", {
        "api": "stk_rewards", "desc": "管理层薪酬持股", "mode": "by_code",
        "codes": "stock_all", "max_rows": 5000, "tier": "C",
        "note": "与 stk_managers 有重叠(前者薪酬/持股, 后者履历); 按标的拉",
    }),
    ("bc_otcqt", {
        "api": "bc_otcqt", "desc": "柜台债券报价", "mode": "by_date",
        "start": "20240320", "date_col": "TRADE_DATE", "max_rows": 0, "tier": "C",
        "subset": ["ID"],
        "note": "⚠返回字段是**大写 TRADE_DATE**(非 trade_date), 故 date_col 须显式指定; "
                "⚠单日约8160行需**5页分页**(见 BACKFILL_PAGE_CONF); "
                "⚠数据起点实测为 2024-03-20(20240319返0/20240320返1985), "
                "故 start 设 20240320 而非 20160101, 可省约 2400 请求",
    }),
    ("fund_div", {
        "api": "fund_div", "desc": "基金分红", "mode": "by_date",
        "start": "20080101", "date_param": "ann_date", "date_col": "ann_date",
        "max_rows": 0, "tier": "C",
        "subset": ["ts_code", "ann_date", "div_proc", "record_date"],
        "note": "⚠参数名是 **ann_date**(非 trade_date); "
                "⚠区间参数返 None(不可用), 只能按公告日拉; 每日约10-40行",
    }),
    ("sge_daily", {
        "api": "sge_daily", "desc": "上海黄金现货行情", "mode": "by_date",
        "start": "20050101", "max_rows": 0, "tier": "C",
        "subset": ["ts_code", "trade_date"],
        "note": "每日约41行(Au99.99/Ag99.99/iAu99.99等41个品种+远期); "
                "⚠等价地在区间模式下会被2000上限截断, 按日拉安全",
    }),
    ("daily_info", {
        "api": "daily_info", "desc": "沪深每日交易统计", "mode": "by_date",
        "start": "20000101", "max_rows": 0, "tier": "C",
        "subset": ["trade_date", "ts_code"],
        "note": "每日约11行(上海A股/上海B股/上海基金市场等); "
                "ts_code 是市场板块代码(如 SH_A), 非股票代码; "
                "baifenwei.com 有更直观版本, 此表用作原始口径",
    }),
    ("sz_daily_info", {
        "api": "sz_daily_info", "desc": "深市每日交易统计", "mode": "by_date",
        "start": "20080101", "max_rows": 0, "tier": "C",
        "subset": ["trade_date", "ts_code"],
        "note": "每日约14行(ABS/ETF/LOF等板块); ts_code 是板块代码",
    }),
    ("eco_cal", {
        "api": "eco_cal", "desc": "全球财经事件日历", "mode": "by_date",
        "start": "20100101", "date_param": "date", "date_col": "date",
        "max_rows": 0, "tier": "C", "subset": "*",
        "note": "⚠参数名是 **date**(非 trade_date); 每日约30-60行; "
                "⚠区间模式顶100行上限, 故按日拉; 同一天多事件需全列判重",
    }),

    # ---------------- D档: 大表快照 ----------------
    ("slb_len_mm", {
        "api": "slb_len_mm", "desc": "转融资余额", "mode": "paged",
        "page_size": 5000, "page_count": 12, "max_rows": 0, "tier": "D",
        "subset": "*",
        "note": "⚠单次上限5000(实测总55214行=12页); "
                "⚠无参数=全量快照, 传 start/end 区间反而返空",
    }),
    ("bse_mapping", {
        "api": "bse_mapping", "desc": "北交所代码映射(新老代码)", "mode": "once",
        "max_rows": 0, "tier": "D", "subset": ["o_code"],
        "note": "248行, 一次拉完; 北交所新老代码对照(如 839729.BJ→920729.BJ)",
    }),

    # ---------------- 死结接口 (限频极低/已停更, 默认关闭) ----------------
    ("fut_weekly_detail", {
        "api": "fut_weekly_detail", "desc": "期货品种交易周报", "mode": "paged",
        "page_size": 4000, "page_count": 6, "max_rows": 0, "tier": "D",
        "subset": "*",
        "note": "⚠ 2026-09-20 修正（原记「死结不补」不准确）:\n"
                "  ① 数据**确实止于 2020-04-24**（实测 week_date 唯一值 472 个，"
                "范围 20100226~20200424；2020 年只有 16 期；"
                "含 SHFE/DCE/CZCE/CFFEX/**INE** 五交易所，每期约 44 个品种）\n"
                "  ② 且 `trade_date` 参数**被完全忽略** —— 传 20200424 / 20190201 / "
                "20251231 返回**同一份**数据（前50行 md5 指纹相同）\n"
                "  ③ **但全量仅 20,690 行 = 6 个请求**，可低成本存档 10 年历史\n"
                "  → 故由 by_date 改 **paged 一次拉全**：原 by_date 会把同一份数据"
                "重复拉 2916 次（纯浪费）。已停更、无增量，不参与日更\n"
                "  ⚠ 数据质量: `week_date` 有 154 行缺失（均为 week=201553 的"
                "2015 年第 53 周，ISO 跨年周，源端未给日期）→ 判重键改用 `week`",
    }),
    ("bc_bestotcqt", {
        "api": "bc_bestotcqt", "desc": "柜台债券最优报价", "mode": "by_range",
        "start_param": "start_date", "end_param": "end_date",
        "start": "20160101", "max_rows": 0, "tier": "X", "enabled": False,
        "subset": "*",
        "note": "⛔ 2026-09-20 修正（原记「无日期列，无法成历史」不准确）:\n"
                "  ① 只有 best_buy_price / best_sell_price 两列，"
                "**返回中不含日期列、也不含债券代码**\n"
                "  ② 但**有日期维度、可按日拉**（实测 20260918→725 行、"
                "20260917→729 行、20250102→542 行、20240102→0 行；"
                "无参数总量约 102,000 行，数据约起于 2025 年初）\n"
                "  ③ 真正的问题不是「无日期」而是「**无标的代码**」—— "
                "能拿到当日有报价的约 700 只债券的买卖价，却无法对应到具体债券，"
                "只能看市场整体价差分布 → 建库价值有限，维持 enabled=False",
    }),
    ("stk_mins", {
        "api": "stk_mins", "desc": "分钟线", "mode": "by_code",
        "codes": "stock_all", "max_rows": 0, "tier": "X", "enabled": False,
        "note": "⛔限频1次/小时(实测): 全市场5882只需数十年, 实务不可行; "
                "分钟级数据改用东财/新浪实时通道或 akshare",
    }),
    ("hk_daily", {
        "api": "hk_daily", "desc": "港股日线", "mode": "by_code",
        "codes": "hk", "max_rows": 5000, "tier": "X", "enabled": False,
        "note": "⛔ 限频1次/分钟, 2785只需46小时, 批量入库不可行; 用 akshare/Wind 替代",
    }),
    ("report_rc", {
        "api": "report_rc", "desc": "券商研报", "mode": "by_code",
        "codes": "stock_all", "max_rows": 5000, "tier": "X", "enabled": False,
        "note": "⛔ 限频1次/分钟, 逐只不可行",
    }),

    # ================================================================
    # 2026-09-19 接口审查补充（13 项）
    # ================================================================
    # 背景: 独立第三方以「239 接口全库扫描」倒推，查出我方「从已登记清单出发」
    #       的盘点**看不到**的可用接口。逐个实测确认：全部存在 + 有权限。
    # 教训: 覆盖度盘点必须双向 —— 既查"登记的有没有建"，也查"还能拉哪些没登记"。
    # 详见 文档/数据与工程/接口审查报告复核意见_20260919.md
    ("fut_index_daily", {
        "api": "fut_index_daily", "desc": "南华期货指数日线", "mode": "by_code",
        "codes": "fut_index", "max_rows": 5000, "tier": "B",
        "subset": ["ts_code", "trade_date"],
        "note": "南华商品/农产品/金属/能化/工业品指数(2006起, 单指数约5000行); "
                "⚠必填 ts_code(不传报错); 传不同 code 返回不同数据(已实测)",
    }),
    ("etf_limit", {
        "api": "etf_limit", "desc": "ETF涨跌停价", "mode": "by_date",
        "max_rows": 0, "tier": "B", "start": "20190101",
        "subset": "*",
        "note": "全市场约2166行/日; 实测2019年起有数据(2018返空)",
    }),
    ("top10_holders", {
        "api": "top10_holders", "desc": "前十大股东", "mode": "by_period",
        "period_param": "period", "date_col": "end_date",
        "start": "20160101", "max_rows": 0, "tier": "B",
        "subset": "*", "cap": 6000,
        "note": ("⚠ period 是**报告期**(如20251231), 非日期区间; "
                 "支持**不传 ts_code 按报告期批量**(-全部股东), 单页6000行需 offset 分页; "
                 "若需精确到个股可另传 ts_code(单只约863行)"),
    }),
    ("top10_floatholders", {
        "api": "top10_floatholders", "desc": "前十大流通股东", "mode": "by_period",
        "period_param": "period", "date_col": "end_date",
        "start": "20160101", "max_rows": 0, "tier": "B",
        "subset": "*", "cap": 6000,
        "note": "同上; 单页6000行需 offset 分页",
    }),
    ("index_classify", {
        "api": "index_classify", "desc": "申万行业分类", "mode": "once",
        "max_rows": 0, "tier": "B",
        "note": "359行; 含 L1(28)/L2(104)/L3(227) 三级行业分类 + parent_code",
    }),
    ("stk_weekly_monthly", {
        "api": "stk_weekly_monthly", "desc": "股票周月线", "mode": "by_date",
        "freq": "week", "week_anchor": "friday", "max_rows": 0, "tier": "B",
        "start": "20160101",
        "subset": "*",
        "note": "⚠ freq 必须写小写 'week'/'month' —— 传 'W' 会**静默返0行**(实测); "
                "⚠ week_anchor=friday: 本接口 trade_date 恒为**该周周五**(非最后交易日), "
                "实证端午周=20160610/跨年周=20260102/春节周=20170127; 误用 last_trade "
                "会静默漏 31 个长假周; 单周约5557行; 与 local daily 聚合周线可交叉验算",
    }),
    ("stk_week_month_adj", {
        "api": "stk_week_month_adj", "desc": "股票周月线(复权)", "mode": "by_date",
        "freq": "week", "week_anchor": "friday", "max_rows": 0, "tier": "B",
        "start": "20160101",
        "subset": "*",
        "note": "⚠ 同上(freq=week, week_anchor=friday); 含复权因子字段",
    }),
    ("fut_weekly_monthly", {
        "api": "fut_weekly_monthly", "desc": "期货周月线", "mode": "by_date",
        "freq": "week", "week_anchor": "friday", "max_rows": 0, "tier": "B",
        "start": "20160101",
        "subset": "*",
        "note": "⚠ 同上(freq=week, week_anchor=friday); 单周约1139行",
    }),
    ("shibor_lpr", {
        "api": "shibor_lpr", "desc": "LPR贷款基础利率", "mode": "by_range",
        "start_param": "start_date", "end_param": "end_date",
        "single_range": True,
        "start": "20131001", "max_rows": 0, "tier": "B", "subset": "*",
        "note": "⚠⚠ 限频 **1次/小时**（2026-09-20 实测复核）—— 原记“1次/分钟”有误；"
                "按年切 11 段需 11 小时，不可行。改用 single_range 一次拉全"
                "（LPR 全历史约 1500 行 < 2000 上限），start 显式设 20131001。"
                "同类：shibor_quote/hibor",
    }),
    ("cn_schedule", {
        "api": "cn_schedule", "desc": "经济数据日程", "mode": "once",
        "max_rows": 0, "tier": "C",
        "note": "176行; 含 data_api 字段(可反查该事件对应哪个宏接口)",
    }),
    ("stk_seasoned", {
        "api": "stk_seasoned", "desc": "新股发行信息(IPO明细)", "mode": "paged",
        "page_size": 3000, "page_count": 12, "max_rows": 0, "tier": "C",
        "subset": "*",
        "note": "⚠⚠ 名称易误读: **实为 IPO 发行明细**(70列: 募资总额/中签率/"
                "机构超额倍数/发行价/保荐费用/募投项目), **不是「次新股清单」**; "
                "⚠ 2026-09-20 实证: 服务端单次上限 3000（首拉恰返3000 可疑），"
                "传 offset=3000 仍返 3000 行 → 总量≥6000, once 已被静默截断"
                "（第8种失效）→ 改 paged",
    }),
    ("stk_account", {
        "api": "stk_account", "desc": "股票开户数", "mode": "once",
        "max_rows": 0, "tier": "C",
        "note": "⚠ 已停更(止于2019-02-22), 192行; 仅作存档, 无日更价值",
    }),
])

# 指数清单: index_weight / index_dailybasic 用
BACKFILL_INDEX_CODES = [
    "000016.SH", "000300.SH", "000905.SH", "000852.SH", "932000.CSI",
    "000688.SH", "399006.SZ", "000001.SH", "899050.BJ",
    "399300.SZ", "000010.SH", "000009.SH", "000011.SH",
]

# 国际指数清单: index_global 用 — 2026-09-19 新增
# ============================================================
# index_global 的代码不是常规 tushare ts_code，而是 22 个国际指数简称。
# ⚠ 该接口是**双重限频**: 10次/分钟 + **100次/天**（后者极易被忽略）。
# ⚠ 单次硬顶 4000 行; 实测限定 2016 年起后各指数约 2700 行，不截断。
# 用途: 海外市场参照（与组合的美股/港股/巴西持仓直接咬合）
BACKFILL_GLOBAL_INDEX_CODES = [    # 美股
    "SPX", "IXIC", "DJI", "RUT",
    # 港股
    "HSI", "HKTECH", "HKAH",
    # 重点: 巴西（持仓 520870/159100 的标的指数）
    "IBOVESPA",
    # 其他主要市场
    "N225", "FTSE", "GDAXI", "FCHI", "SENSEX", "KS11", "TWII",
    "RTS", "AS51", "SPTSX", "XIN9", "CSX5P", "CKLSE", "HSHKCI",
]

# 南华期货指数清单: fut_index_daily 用 — 2026-09-19 新增
# ⚠ 该接口**必填 ts_code**（不传报错）；传不同 code 返回不同数据（已实测验证）。
# 代码格式 = <指数简称>.NH；单指数全历史约 5000 行（2006 起）。
BACKFILL_FUT_INDEX_CODES = [
    "NHCI.NH",    # 南华商品指数
    "NHAI.NH",    # 南华农产品指数
    "NHMI.NH",    # 南华金属指数
    "NHECI.NH",   # 南华能化指数
    "NHII.NH",    # 南华工业品指数
]

# 需分页的接口配置 — 2026-09-16 实测补全
# ============================================================
# 背景: 部分接口单日/单次返回量超过服务端单次上限, 不传 limit/offset
#       会静默截断(返回看似正常的行数, 实际丢数据)。
# 实测证据:
#   fut_holding 默认返 4000, 真实 10,650 → 丢 62%
#   opt_daily   默认返 15000, 真实 25,801 → 丢 42%
#   index_daily 单日全市场 10000+ 个指数, 超 5000 上限
#
# 字段: api -> (每页行数, 单日页数上限保护)
BACKFILL_PAGE_CONF = {
    "index_daily":  (4500,  20),
    "fut_holding":  (2000,  30),
    "opt_daily":    (2000,  60),
    "fut_wsr":      (2000,  10),
    "repo_daily":   (2000,   5),
    "moneyflow":    (5000,   5),
    "margin_detail":(5000,   5),
    "margin_secs":  (5000,   5),
    "top_inst":     (5000,   5),
    "block_trade":  (5000,   5),
    "hk_hold":      (5000,   5),
    "suspend_d":    (5000,   5),
    "fut_daily":    (5000,   5),
    # ⚠ bc_otcqt 单日约 8160 行，单次硬顶 2000 → 需 5 页（实测 offset 0/2000/4000/6000
    #   各 2000 行、offset 8000 为 160 行、offset 10000 空，拼接后 8160 行零重复）
    "bc_otcqt":     (2000,  10),
    # ⚠ top10_holders / top10_floatholders（2026-09-19）:
    #   按报告期批量拉全市场，单页硬顶 6000（实测 offset 0/6000/12000 各返 6000 行）
    #   —— 不配分页会静默丢数据（每期真实行数远超 6000）。
    #   分页参数名是 `period` 而非 `trade_date`（由 conf["period_param"] 传入）。
    "top10_holders":      (6000, 20),
    "top10_floatholders": (6000, 20),
}

# 兼容旧名(仅按 key 判断是否分页)
BACKFILL_PAGED = set(BACKFILL_PAGE_CONF.keys())

# ============================================================
# 7b-2. 各表分区列 (table → 日期列) — 2026-09-17 新增
# ============================================================
# 单一真源：凡需要"按年分区落盘"或"按年分区迁移"的模块都从这里取，
# 避免同一张表在不同模块里配成不同列（曾出现 fund_nav 在迁移工具里是
# nav_date、在更新脚本里被当成 trade_date 的风险）。
# 用途: tools/db_migrate.py / fetch/full_download.py /
#       fetch/cb_download.py / fetch/fund_nav_update.py
#
# 与另外两处"日期列清单"的关系（三者语义不同，并存是刻意的，勿合并）:
#   本表                    = **写入决策**（这张表按哪一列分区）—— 权威定义
#   db_migrate.DATE_COL     = 迁移时的**多候选回退**（个别表主列有缺失，
#                             如 namechange.end_date 不全，需逐列回退）
#   db_schema.DATE_COL_PREF = **探测**报告用（如实反映库里实际有什么，
#                             刻意不依赖本配置 —— 否则配置错了就查不出来）
TABLE_DATE_COL = {
    # 行情（按交易日）
    "daily": "trade_date", "daily_basic": "trade_date",
    "fund_daily": "trade_date", "cb_daily": "trade_date",
    "index_daily": "trade_date", "index_dailybasic": "trade_date",
    "index_global": "trade_date",
    "adj_factor": "trade_date", "stk_limit": "trade_date",
    "moneyflow": "trade_date", "margin": "trade_date",
    "margin_detail": "trade_date", "margin_secs": "trade_date",
    "hk_hold": "trade_date", "suspend_d": "trade_date",
    "fut_daily": "trade_date", "fut_mapping": "trade_date",
    "fut_settle": "trade_date",
    # ---- 2026-09-21 新增: 4 张 tier C 表首次建库后登记 ----
    # ⚠ 这 4 张 mode=by_date，此前从未建库、也未登记日期列 →
    #   table_storage_layout() 走"不在 TABLE_DATE_COL → by_code"分支，
    #   与实际落盘（by_date 分支 → by_year）冲突。补库建库后由 R9 抓到；
    #   若不登记，后续日更/续拉会按 by_code 写出 `{code}.parquet`，
    #   重新制造混合布局（第 5 种静默失效）。
    "fut_holding": "trade_date", "fut_wsr": "trade_date",
    "opt_daily": "trade_date", "repo_daily": "trade_date",
    "sge_daily": "trade_date", "daily_info": "trade_date",
    "sz_daily_info": "trade_date",
    "weekly": "trade_date", "monthly": "trade_date",
    "index_weekly": "trade_date", "index_monthly": "trade_date",
    "top_list": "trade_date",
    "top_inst": "trade_date", "block_trade": "trade_date",
    "hsgt_top10": "trade_date", "ggt_top10": "trade_date",
    # 基金
    "fund_nav": "nav_date", "fund_adj": "trade_date",
    "fund_share": "trade_date", "fx_daily": "trade_date",
    # 财务（报告期）
    "income": "end_date", "balancesheet": "end_date",
    "cashflow": "end_date", "fina_indicator": "end_date",
    "fina_audit": "end_date", "fina_mainbz": "end_date",
    # 事件面
    "dividend": "end_date", "forecast": "end_date",
    "stk_holdernumber": "end_date", "stk_holdertrade": "ann_date",
    "pledge_stat": "end_date", "share_float": "ann_date",
    "repurchase": "ann_date", "namechange": "start_date",
    "cb_issue": "ann_date", "cb_rating": "rating_date",
    "cb_share": "end_date",
    "stk_managers": "ann_date", "express": "ann_date",
    "pledge_detail": "ann_date",
    "stk_rewards": "ann_date", "fund_div": "ann_date",
    # 其他
    "disclosure_date": "end_date", "index_weight": "trade_date",
    "new_share": "ipo_date", "shibor": "date", "us_tycr": "date",
    "us_trycr": "date", "us_tbr": "date", "us_tltr": "date", "us_trltr": "date",
    "eco_cal": "date",
    # ⚠ bc_otcqt 返回的日期字段是**大写 TRADE_DATE**（tushare 里的异类）
    "bc_otcqt": "TRADE_DATE",
    # ---- 2026-09-19 接口审查补充 ----
    # ⚠ 仅列**按年分区**的表；once/by_range 类（index_classify/cn_schedule/
    #   stk_seasoned/stk_account/shibor_lpr）由 mode 决定落单文件，不列在此。
    "fut_index_daily": "trade_date",
    "etf_limit": "trade_date",
    "top10_holders": "end_date",          # period 是入参名, 返回列是 end_date
    "top10_floatholders": "end_date",
    "stk_weekly_monthly": "trade_date",
    "stk_week_month_adj": "trade_date",
    "fut_weekly_monthly": "trade_date",
    "us_daily": "trade_date",
}

# ------------------------------------------------------------
# 7b-3. by_code 表的「源端起点基线」— 2026-09-19 新增
# ------------------------------------------------------------
# 用途: 回归 R13 校验"库内数据起点未被截断"（库内最早不得晚于基线）。
#
# 背景（真实缺陷）:
#   by_code 模式只传 ts_code、不传日期参数。对**有默认行数上限**的接口，
#   服务端只返回最近 N 行 → 早期数据被静默丢弃。而原截断检测用
#   `len(df) == max_rows` 判定，当"服务端上限(3000) < 配置值(5000)"时
#   判据不成立 → 检测失效 → 静默漏数据。
#   实证: index_dailybasic 库内曾只有 2014-05 起，源端实有 2004-01 起（漏 10 年）。
#
# 值的含义 = **实测的源端最早日期**（下限，非精确值）。
#   库内起点早于它属正常（可能有更早数据源）；晚于它 = 可疑截断。
# ⚠ 这些是 2026-09-19 实测值，源端若新增更早数据不会导致本断言误报
#   （断言只要求"不晚于"）。
# ⚠ 值必须是**该表 partition 列（TABLE_DATE_COL）的实际最小值**，
#   不要用其他日期列。实证踩坑: cb_issue 曾误用 onl_date(19921119)，
#   而 partition 列 ann_date 的实测最小值是 19980730。
SOURCE_START_BASELINE = {
    "index_dailybasic": "20040102",
    "fund_adj":         "19991022",
    "fund_share":       "19980327",
    "fx_daily":         "20070521",
    "index_weekly":     "19910705",
    "index_monthly":    "19910731",
    "dividend":         "19901231",
    "stk_managers":     "19890201",
    "stk_holdernumber": "19000908",
    "stk_holdertrade":  "19940810",
    "stk_rewards":      "19920526",
    "pledge_stat":      "20140307",
    "pledge_detail":    "20030402",
    "share_float":      "20050809",
    "repurchase":       "20050625",
    "namechange":       "19901201",
    "fina_mainbz":      "20001231",
    "fina_audit":       "19971231",
    "express":          "20050108",
    "forecast":         "19981231",
    "cb_issue":         "19980730",
    "cb_rating":        "20010501",
    "cb_share":         "20000630",
}

# ============================================================
# 历史扩展待补声明 — 2026-09-20 新增
# ============================================================
# 用途: 把某表的 `start` 往前改（拉更早历史）属于**刻意为之**的真实大缺口，
#       而不是"口径漂移误报"。若不声明，R12 断言（缺口总数 < 500）会持续红灯
#       —— 断言的"狼来了"效应会让真正的漂移被忽视。
#
# 用法: 做历史扩展时，把涉及的表名加入本集合；**拉完后必须移出**。
#       留在里面会削弱断言（该表的缺口永远不被检查）。
#
# 证书依据（R12 本意）:
#   口径漂移误报的规模是"成千上万"（2026-09-19 实测 4,532 条），
#   而正常运行时的真实缺口是 0~3 条。两者必须能区分，
#   否则"改了 start 正在补数"会被误判为"引擎又漏读参数了"。
# 2026-09-25 订正（原登记有误，已消账）：fx_daily / index_dailybasic 曾于本日登记为
#   "历史未回补的真实缺口 4704/5518 天"——**误判**。两表 09-23 由 by_code 改 by_date
#   纳入日更，by_code 时代的全历史数据一直在库（idb 5522 日期全覆盖 / fx 8627 日期），
#   audit_gaps 报的差额是 **断点账本差**（by_date 断点只记 09-23 起的日更任务）。
#   已用 `main.py ckpt --table fx_daily,index_dailybasic --fix --execute` 按磁盘实际
#   回填断点 14,141 项（2026-09-25 22:04，备份 .bak_20260925_220428），账实对齐。
#   ⚠ 教训：by_date 表纳入日更组前，先 ckpt --fix 消账，否则 audit_gaps 会把
#     by_code 时代的数据全算成"缺口"，误导回补（当日实证：按日拉历史日期全空烧 3600 次）。
HISTORY_BACKFILL_PENDING = set()      # 例: {"margin", "moneyflow"}

# ============================================================
# 7c. 数据清理目标 (db-clean) — 2026-09-16 新增
# ============================================================
# 场景: 同一主键存有多个版本(历史脚本未去重直接追加所致), 需压成一行。
# 引擎: tools/db_clean.py
#
# 字段说明:
#   key       行主键(分组依据)。必须是"业务上唯一标识一期一行"的列组合。
#   date_col  日期列(用于排序/输出统计)
#   strategy  合并策略
#               keep_first_nonnull  组内 bfill 后取首行 = 每列取第一个非空值
#               keep_last_nonnull   组内 ffill 后取末行 = 每列取最后一个非空值
#               plain_first         直接按主键取首行(不补空, 慎用)
#   watch     重点监控完整度的字段(清理前后对比非空率, 下降即告警)
#   note      备注/成因
#
# ⚠ 策略选择必须实测: 哪个版本才是当前服务端口径, 不能靠"后写入=新版"的直觉。
#    fina_indicator 实测 40/40 冲突组匹配"先写入"那版 → 用 keep_first_nonnull。
DB_CLEAN_TARGETS = {
    "fina_indicator": {
        "desc": "财务指标",
        "key": ["ts_code", "end_date"],
        "date_col": "end_date",
        "strategy": "keep_first_nonnull",
        "watch": ["fcff", "fcfe", "fcff_ps", "fcfe_ps", "cash_ratio", "eps",
                  "dt_eps", "assets_turn", "networking_capital", "ca_turn"],
        "note": ("Tushare 后续补充自由现金流字段后二次拉取未按主键去重、直接追加; "
                 "实测 33.4% 整行相同, 且存在 '两版都有值且不同' 的错误值组"),
    },
}

# ============================================================
# 7d. 体检主键表 (db-audit) — 2026-09-16 新增
# ============================================================
# 用于判断"目录内是否有真重复"。引擎: tools/db_audit.py
#
# ⚠ 主键必须按【单文件内是否能唯一标识一行】来定, 不是按全库。
#   例: 按标的拆文件的目录用 ["trade_date"] 就够(文件内只有一只标的);
#       按年分区的大表必须含标的列, 否则同一天全市场都会被判重复。
# 值说明:
#   ["ts_code","trade_date"] 等列表 = 复合主键
#   None = 跳过重复检查(无合适主键的接口)
DB_AUDIT_PKEYS = {
    # --- backfill 新增(按年分区) ---
    "margin":           ["trade_date", "exchange_id"],
    "margin_detail":    ["trade_date", "ts_code"],
    "margin_secs":      ["trade_date", "ts_code"],
    "moneyflow":        ["trade_date", "ts_code"],
    "top_list":         None,
    "top_inst":         None,
    "block_trade":      None,
    "hsgt_top10":       ["trade_date", "ts_code"],
    "ggt_top10":        None,
    "hk_hold":          ["trade_date", "code"],
    "suspend_d":        ["trade_date", "ts_code"],
    "fut_daily":        ["trade_date", "ts_code"],
    "fut_mapping":      ["trade_date", "ts_code"],
    "disclosure_date":  ["ts_code", "end_date"],
    "index_weight":     ["index_code", "con_code", "trade_date"],
    # --- backfill 新增(按标的/单文件) ---
    "dividend":         ["ts_code", "end_date", "ann_date", "div_proc"],
    "forecast":         ["ts_code", "ann_date", "end_date"],
    "index_dailybasic": ["ts_code", "trade_date"],
    "fund_adj":         ["ts_code", "trade_date"],
    "fund_share":       ["ts_code", "trade_date"],
    "fx_daily":         ["ts_code", "trade_date"],
    "stk_holdernumber": ["ts_code", "end_date"],
    "stk_holdertrade":  ["ts_code", "ann_date", "holder_name"],
    "pledge_stat":      ["ts_code", "end_date"],
    "share_float":      ["ts_code", "float_date", "holder_name"],
    "repurchase":       ["ts_code", "ann_date", "proc"],
    "namechange":       ["ts_code", "start_date"],
    "fina_mainbz":      ["ts_code", "end_date", "bz_item"],
    "fina_audit":       ["ts_code", "end_date"],
    "stock_company":    ["ts_code"],
    "cb_issue":         ["ts_code"],
    "cb_share":         ["ts_code", "publish_date"],
    "cb_rating":        ["ts_code", "rating_date"],
    "new_share":        ["ts_code"],
    "broker_recommend": None,
    "shibor":           ["date"],
    "us_tycr":          ["date"],
    "us_trycr":         ["date"],
    "us_tbr":           ["date"],
    "us_tltr":          ["date"],
    "us_trltr":         ["date"],
    "fut_basic":        ["ts_code"],
    "opt_basic":        ["ts_code"],
    "stk_rewards":      ["ts_code", "ann_date", "name", "title", "end_date"],
    "bc_otcqt":         ["ID"],
    "fund_div":         ["ts_code", "ann_date", "div_proc", "record_date"],
    "sge_daily":        ["ts_code", "trade_date"],
    "daily_info":       ["trade_date", "ts_code"],
    "sz_daily_info":    ["trade_date", "ts_code"],
    "eco_cal":          None,
    "fut_settle":       ["ts_code", "trade_date"],
    "fut_trade_cal":    ["exchange", "cal_date"],
    "shibor_quote":     ["date", "bank"],
    "hibor":            ["date"],
    "hk_tradecal":      ["cal_date"],
    "stk_managers":     ["ts_code", "ann_date", "name", "title"],
    "express":          ["ts_code", "ann_date", "end_date"],
    "pledge_detail":    ["ts_code", "ann_date", "holder_name", "pledge_amount"],
    "weekly":           ["ts_code", "trade_date"],
    "monthly":          ["ts_code", "trade_date"],
    "index_weekly":     ["ts_code", "trade_date"],
    "index_monthly":    ["ts_code", "trade_date"],
    "index_global":     ["ts_code", "trade_date"],
    "index_member_all": ["ts_code"],
    "hs_const":         ["ts_code", "hs_type"],
    # --- 尚未入库/已废弃的表（2026-09-19 补：R11 要求每张表都有归宿）---
    #     配 None = 跳过重复检查（结构未实测，宁可不查也不误报）
    "opt_daily":        ["ts_code", "trade_date"],
    "repo_daily":       ["trade_date", "ts_code"],
    "hk_daily":         ["ts_code", "trade_date"],
    "stk_mins":         ["ts_code", "trade_date"],
    "fut_holding":      None,   # 单日多会员多合约，主键未实测
    "fut_wsr":          None,   # 仓单日报，主键未实测
    # ⚠ 2026-09-22 修复（P1-3）: 此处原有 "fut_weekly_detail": None，
    #   与文件末尾的 "fut_weekly_detail": ["exchange","prd","week"] 构成
    #   **字典重复键**（后写者生效，前面的 None 被静默覆盖，且注释"已停更"
    #   与实际"已建库"相矛盾）。已删除本处条目，保留末尾的正确版本。
    "report_rc":        None,   # 研报，限频死结
    "bc_bestotcqt":     None,   # 死结：无日期列，无法成历史
    "gz_index":         None,   # tier C 未拉（民间利率，已被 shibor 覆盖）
    "wz_index":         None,   # 同上
    "us_basic":         None,   # 美股: ts_code 不唯一(55处业务多行) → 全列判重
    "us_tradecal":      ["cal_date"],
    # 基金公司: 原配 ['ts_code'] 但该表**无此列** → 判重静默失效（2026-09-19 修正）。
    # ⚠ 不可改用 credit_code—— 实测 207 行仅 154 个唯一值，重复是公司更名
    #   业务事实（如 91310000717880594D = 浦银安盛/浦银基金，同一法人的两个名字），
    #   用它做主键会产生 53 行“重复”误报，据此去重还会丢掉更名历史。
    #   name / (name,setup_date) 实测均唯一，取更稳的复合键。
    "fund_company":     ["name", "setup_date"],
    "fund_manager":     None,
    "slb_len_mm":       None,
    "bse_mapping":      ["o_code"],
    "hk_basic":         ["ts_code"],
    "cn_cpi":           None,
    "cn_ppi":           None,
    "cn_m":             None,
    "cn_pmi":           None,
    "cn_gdp":           None,
    "sf_month":         None,
    # --- 原有核心目录 ---
    "daily":            ["ts_code", "trade_date"],
    "daily_basic":      ["ts_code", "trade_date"],
    "fund_daily":       ["ts_code", "trade_date"],
    "cb_daily":         ["ts_code", "trade_date"],
    "index_daily":      ["ts_code", "trade_date"],
    "adj_factor":       ["ts_code", "trade_date"],
    "stk_limit":        ["ts_code", "trade_date"],
    "fund_nav":         ["ts_code", "nav_date"],
    "income":           ["ts_code", "end_date", "report_type"],
    "balancesheet":     ["ts_code", "end_date", "report_type"],
    "cashflow":         ["ts_code", "end_date", "report_type"],
    "fina_indicator":   ["ts_code", "end_date"],
    # ---- 2026-09-19 接口审查补充 ----
    # 判重口径遵循项目"二分"原则（真重复 vs 业务多行），全部用复合键或全列：
    "fut_index_daily":     ["ts_code", "trade_date"],
    "etf_limit":           ["ts_code", "trade_date"],
    "top10_holders":       ["ts_code", "end_date", "holder_name"],
    "top10_floatholders":  ["ts_code", "end_date", "holder_name"],
    "index_classify":      ["index_code"],
    "stk_weekly_monthly":  ["ts_code", "trade_date"],
    "stk_week_month_adj":  ["ts_code", "trade_date"],
    "fut_weekly_monthly":  ["ts_code", "trade_date"],
    "shibor_lpr":          ["date"],
    "cn_schedule":         None,   # 日程表，无天然主键，跳过判重
    "stk_seasoned":        None,   # IPO 明细，同一 ts_code 可多次公告，跳过
    "stk_account":         ["date"],
    "us_daily":            ["ts_code", "trade_date"],
    # --- 2026-09-20 fut_weekly_detail 建库（由 tier X 改为 tier D/paged）---
    #   注：源端数据已于 2020-04-24 停更，本表为**历史归档**，非日更表。
    # ⚠ 判重键**不能用 week_date**：实测 154 行 week_date 缺失（NaN），
    #    这些行是 week=201553（2015 年第 53 周，ISO 跨年周，源端未给日期），
    #    会导致按 [exchange,prd,week_date] 误判 98 行"重复"。
    #    改用 week（周次，无缺失）→ 实测 20,653 行**零重复**。
    "fut_weekly_detail":   ["exchange", "prd", "week"],
}

# 体检时用于缺口分析的 by_date 类接口(有明确交易日维度)
DB_AUDIT_DATE_APIS = [
    "margin", "margin_detail", "margin_secs", "moneyflow",
    "top_list", "top_inst", "block_trade", "hsgt_top10", "ggt_top10",
    "hk_hold", "suspend_d", "fut_daily",
]

# 断点键名 → 实际目录名 的别名映射
# 背景: .checkpoint.json 里部分键带 _full 后缀(旧命名), 实际落盘目录无后缀。
#       不做映射会被误报成"断点有记录但目录不存在"。
DB_AUDIT_CKPT_ALIASES = {
    "fund_daily_full":  "fund_daily",
    "fund_nav_full":    "fund_nav",
    "index_daily_full": "index_daily",
}

# 数据由其他流程填充、断点文件不记录 的目录
# 背景: adj_factor / stk_limit 由 redtide-supply 流程填充,
#       download-full 的接口清单里没有它们, 故断点为 0 属正常。
#       这类"有文件但断点为0"应降级为提示, 不算异常。
DB_AUDIT_CKPT_EXTERNAL = {
    "adj_factor", "stk_limit", "market_state",
}

# 上游数据源本身含重复行的接口
# 背景: 实测这些接口的**现拉返回**中即存在整行完全相同的记录, 非落盘引入。
#       本地落盘已做全列去重(本地重复数通常少于现拉), 剩余重复属数据源原貌。
#       体检时标注为提示, 不做清理(量小且保留原貌)。
DB_AUDIT_UPSTREAM_DUP = {
    "top_list",     # 实测: 20160202 现拉42行含1条重复, 与本地一致
    "block_trade",  # 实测: 20161102 现拉231行含24条重复(本地已去重至4)
}

# by_date 模式的批量落盘参数
# ============================================================
# 背景: 按年分区下"追加一天"要对整年文件做 read→concat→dedup→write,
#       成本随文件线性增长(moneyflow 82万行时单次 1.49s, 是网络请求的3.7倍)。
#       累积 N 天后一次写入, 把 N 次 O(文件大小) 降为 1 次。
# 字段: api -> {"days": 累积多少天后落盘, "rows": 累积多少行后落盘}
# 说明: 阈值越小越省内存、但写入次数多; 越大越快、但崩溃损失窗口大。
#       当前按"单年最终规模"设: 小接口可攒更多天。
BACKFILL_FLUSH_CONF = {
    "moneyflow":     {"days": 30, "rows": 500_000},
    "margin_detail": {"days": 30, "rows": 500_000},
    "margin_secs":   {"days": 30, "rows": 500_000},
    "fut_daily":     {"days": 40, "rows": 500_000},
    "hk_hold":       {"days": 40, "rows": 500_000},
    "top_list":      {"days": 40, "rows": 300_000},
    "top_inst":      {"days": 40, "rows": 300_000},
    "block_trade":   {"days": 40, "rows": 300_000},
    "suspend_d":     {"days": 60, "rows": 300_000},
    "hsgt_top10":    {"days": 60, "rows": 200_000},
    "ggt_top10":     {"days": 60, "rows": 200_000},
    "margin":        {"days": 60, "rows": 200_000},
    "disclosure_date": {"days": 30, "rows": 300_000},
}
BACKFILL_FLUSH_DEFAULT = {"days": 20, "rows": 500_000}

# ============================================================
# 8. 尾盘增量更新配置
# ============================================================
# 尾盘更新触发时间 (收盘后15:05开始)
# [ARCHIVED 2026-09-25 G1] 死配置：全库 grep（83 文件，含字符串/getattr 形态）+工作区/skills 交叉核实均无引用。原用途：盘中更新参数（盘中链路随背离监控停用）。保留定义保行号稳定，勿新增引用。
INTRADAY_UPDATE_TIME = "15:05"

# 尾盘增量更新涉及的API和目录映射
# key=API名, value=(目录路径, 主键列, 日期列)
INTRADAY_UPDATE_APIS = {
    "daily":         (STOCK_DAILY_DIR, "ts_code", "trade_date"),
    "daily_basic":   (STOCK_DAILY_BASIC_DIR, "ts_code", "trade_date"),
    "fund_daily":    (FUND_DAILY_DIR, "ts_code", "trade_date"),
    "cb_daily":      (CB_DAILY_DIR, "ts_code", "trade_date"),
    "index_daily":   (INDEX_DAILY_DIR, "ts_code", "trade_date"),
}

# 尾盘批量更新参数
# [ARCHIVED 2026-09-25 G1] 死配置：全库 grep（83 文件，含字符串/getattr 形态）+工作区/skills 交叉核实均无引用。原用途：盘中更新参数（同上）。保留定义保行号稳定，勿新增引用。
INTRADAY_BATCH_SIZE = 50     # 每批50个代码 (tushare按trade_date批量拉)
INTRADAY_RATE_INTERVAL = 0.35  # 请求间隔秒数 (保守些)

# ============================================================
# 8b. 基金净值更新配置 (fund_nav)
# ============================================================
# 基金净值 T 日约 20:00 后才陆续公布（QDII 更晚），17:30 主流程运行时
# 当日净值尚未齐全，因此每次回溯拉取最近 N 个交易日，自动补漏。
FUND_NAV_LOOKBACK_DAYS = 5       # 净值回溯交易日数
FUND_NAV_PAGE_SIZE = 5000        # fund_nav 单次返回上限（需 limit/offset 分页）

# ============================================================
# 8c. 每日增量更新 (daily-update) — 2026-09-17 新增
# ============================================================
# 背景: 全库已统一为"按年分区"(by_year)布局。每日更新若仍按 ts_code
#       逐文件读写, 会在年度目录里凭空生成数千个小文件, 并把 reader 的
#       布局探测打回 by_code, 导致下游读数全面降级。
#       故每日更新统一复用 fetch/backfill.py 的 by_date 执行路径:
#         按日并发拉取 + YearBatchWriter 批量落盘 + 断点三态纪律。
#
# 字段与 BACKFILL_TARGETS 同构（因此可直接交给 backfill.run_target 执行）:
#   date_col  分区列, 决定写入 {YYYY}.parquet
#   subset    行主键
#   start     库为空时的兜底起点
#
# ⚠ subset 纪律（必须遵守）:
#   by_date 类接口单日返回的是**全市场数千行**。若 subset 只给日期列,
#   同一天的所有标的都会被当成重复行, 只留 1 行。必须用复合主键。
#
# ⚠ 为何需要"复核窗口"而不能只看最新日期:
#   实测 2026-09-16 的 daily_basic 仅入库 130 行(正常约 5550 行),
#   而 daily 当日 5550 行。此时"库内最新日期"仍显示 20260916+,
#   按最新日期算缺口会**判定为已完成**, 该日的缺失将永久留存。
#   故每日更新除补缺口外, 还强制重拉最近 DAILY_UPDATE_LOOKBACK 个交易日
#   （落盘走主键去重, 幂等, 重复拉取不会产生重复行）。
DAILY_UPDATE_LOOKBACK = 3

# BACKFILL_TARGETS 未覆盖的核心表（每日更新新增定义）
DAILY_UPDATE_EXTRA = OrderedDict([
    ("daily", {
        "api": "daily", "desc": "个股日线", "mode": "by_date",
        "date_col": "trade_date", "subset": ["ts_code", "trade_date"],
        "max_rows": 6000, "start": "19901219",
        "note": "单日约5550行(实测未触6000上限, 仍配分页防标的扩容)",
    }),
    ("daily_basic", {
        "api": "daily_basic", "desc": "个股每日指标", "mode": "by_date",
        "date_col": "trade_date", "subset": ["ts_code", "trade_date"],
        "max_rows": 6000, "start": "19901219",
        "note": "单日约5550行",
    }),
    ("fund_daily", {
        "api": "fund_daily", "desc": "ETF/LOF日线", "mode": "by_date",
        "date_col": "trade_date", "subset": ["ts_code", "trade_date"],
        "max_rows": 6000, "start": "19901219",
        "note": "单日约2130行",
    }),
    ("cb_daily", {
        "api": "cb_daily", "desc": "可转债日线", "mode": "by_date",
        "date_col": "trade_date", "subset": ["ts_code", "trade_date"],
        "max_rows": 6000, "start": "19901219",
        "note": "单日约315行",
    }),
    ("index_daily", {
        "api": "index_daily", "desc": "指数日线", "mode": "by_date",
        "date_col": "trade_date", "subset": ["ts_code", "trade_date"],
        "max_rows": 0, "start": "19901219",
        "note": "单日约1.1万个指数, 已在 BACKFILL_PAGE_CONF 配分页",
    }),
    ("adj_factor", {
        "api": "adj_factor", "desc": "复权因子", "mode": "by_date",
        "date_col": "trade_date", "subset": ["ts_code", "trade_date"],
        "max_rows": 6000, "start": "19901219",
        "note": "原 redtide-supply Phase1; 单日约5500行",
    }),
    ("stk_limit", {
        "api": "stk_limit", "desc": "涨跌停价", "mode": "by_date",
        "date_col": "trade_date", "subset": ["ts_code", "trade_date"],
        "max_rows": 6000, "start": "20080102",
        "note": "原 redtide-supply Phase3; 单日约5500行",
    }),
    # ---------------- 2026-09-19 接口审查补充（日更类）----------------
    ("etf_limit", {
        "api": "etf_limit", "desc": "ETF涨跌停价", "mode": "by_date",
        "date_col": "trade_date", "subset": ["ts_code", "trade_date"],
        "max_rows": 6000, "start": "20190101",
        "note": "全市场约2166行/日; 实测2019年起",
    }),
    ("us_daily", {
        # ⚠ 美股日线 —— 特殊情况（2026-09-19 实测）:
        #   ① **独立权限漏网**: 官方标 2000元/年 独立权限，实测可调
        #   ② **限频 1次/小时** —— 关键坑: 第 1 次调用报"1次/分钟"，
        #      65 秒后再调才暴露"1次/小时"（差 60 倍，直接决定补不补）
        #   ③ 因此**历史回补不可行**（9041 交易日 ÷ 1次/小时 ≈ 377 天），
        #      但**日更完全可行**（每天 1~2 次调用）
        #   → start 设为近期，避免首次日更误触历史全量；历史用 akshare 新浪源补
        "api": "us_daily", "desc": "美股日线", "mode": "by_date",
        "calendar": "us",          # ⚠ 用美股日历(us_tradecal), 不能用 A 股日历
        "date_col": "trade_date", "subset": ["ts_code", "trade_date"],
        "max_rows": 0, "start": "20260901",
        "note": "⚠ 限频1次/小时(实测: 首次报'1次/分钟'、65秒后才是真值); "
                "历史回补不可行(约377天), 日更可行; 用美股日历; "
                "历史用 akshare stock_us_daily(新浪源) 补",
    }),
])

# ============================================================
# 权限漏网登记 — 2026-09-20 新增
# ============================================================
# 背景：全库筛查（239 接口实测）确认本账号（2000 积分档）有 **7 项漏网**——
#       官方标称门槛高于本档、但实测可调。属**权限校验缺口**，不是门槛下调：
#         ① 对照组被正常拦截（同档的 fund_portfolio 稳定报"无权限"）
#         ② 官方文档至今未改 ③ 平台无任何公告
#
# ⚠ 核心风险：**漏网状态不可作为长期依赖**。一旦平台收紧，这些表会
#   **静默断裂**（返回"您没有接口(xxx)访问权限"），而体检只能看到
#   "数据落后 N 天"，分辨不出是权限收紧还是网络故障。
#
# 本清单的用途（三处消费，勿删）：
#   1. fetch/base.py      —— 命中无权限错误时，打印 ★ 提示与替代路径
#   2. fetch/daily_update —— 日更结束时汇总告警（PushPlus），把
#                            "断了一个月才发现"变成"收紧当天就知道"
#   3. tools/regress_pipeline R15 —— 交叉验证清单与 BACKFILL_TARGETS
#
# 字段：
#   claimed     官方标称门槛（用于判断"是否已变成正当权限"）
#   kind        漏网类型：quota=积分漏网 / indep=独立权限漏网
#   use         下游用途（命中时据此判断影响面）
#   alt         替代路径（命中时提示"该启用哪个"）
#   monitor     是否纳入日更告警（True=告警；False=已知无下游依赖）
LEAKY_PERMISSIONS = {
    # ── 积分漏网（4 项）──
    "share_float": {
        "claimed": 3000, "kind": "quota",
        "use": "解禁压力预判",
        "alt": "akshare stock_restricted_release_* 五件套（细节更全，含解禁股东）",
        "monitor": True,
    },
    "index_dailybasic": {
        "claimed": 4000, "kind": "quota",
        "use": "★ 奇点估值锚点（PE/PB/股息率）",
        "alt": "akshare stock_index_pe_lg（乐咕，但**实测月频**、Tushare 是日频）"
               "；日频源 stock_zh_index_value_csindex 仅保留近 1 月 → **替代不完整**",
        "monitor": True,
    },
    "fund_adj": {
        "claimed": 5000, "kind": "quota",
        "use": "★ 宽基 ETF 轮动复权价",
        "alt": "净值 + 分红自算（**精度未验证**）→ **替代不完整**",
        "monitor": True,
    },
    "opt_daily": {
        "claimed": 5000, "kind": "quota",
        "use": "期权杠杆监控（iv_tracker/leverage 运行时调用）",
        "alt": "akshare option_sse_daily_sina（部分覆盖）",
        "monitor": False,      # 未建库、无长期依赖，命中时不必告警刷屏
    },
    # ── 独立权限漏网（2 项）──
    "hk_daily": {
        "claimed": "独立权限 1000元/年", "kind": "indep",
        "use": "港股日线（港股二件套）",
        "alt": "akshare stock_hk_daily（新浪源，实测 5476 行 / 2004 起）",
        "monitor": True,
    },
    "us_daily": {
        "claimed": "独立权限 2000元/年", "kind": "indep",
        "use": "美股日线（三件套之一）",
        "alt": "akshare stock_us_daily（新浪源，实测 10035 行 / 1984 起，延迟 15 分钟）",
        "monitor": True,
    },
    # ── 券商金股（官方表未列此档）──
    "broker_recommend": {
        "claimed": 6000, "kind": "quota",
        "use": "券商月度金股",
        "alt": "akshare stock_institute_recommend —— **实测返空，替代不成立**",
        "monitor": True,
    },
}

# 漏网清单自检：不得登记未在 BACKFILL_TARGETS 中的表名
#   （防"清单写了但打错表名"导致监控空转 —— 与判重键"键名必须真实存在"同源）
# [ARCHIVED 2026-09-25 G1] 死配置：全库 grep（83 文件，含字符串/getattr 形态）+工作区/skills 交叉核实均无引用。原用途：旧权限漏网清单（已由 doctor/R15 新判据取代）。保留定义保行号稳定，勿新增引用。
LEAKY_PERMISSIONS_APIS = set(LEAKY_PERMISSIONS.keys())


# ============================================================
# 每日更新的接口组成（键 = 表目录名，也 = 断点键名）
#   core   —— 下游策略直接咬合，必须成功
#   extend —— 资金面/事件面，失败不影响当日主流程
#
# ⚠ 新增 by_date 表时必须同步本清单（或缺席必须登记到 DAILY_UPDATE_EXEMPT）。
#   这个清单是**手工维护的执行白名单**，与 BACKFILL_TARGETS（属性定义）分离：
#     属性定义 决定"这张表怎么拉"；本清单决定"这张表每天跑不跑"。
#   实证教训: fut_mapping/fut_settle 作为新接口只写了属性定义、漏了本清单
#   → 数据静默停在补数那天、逐日变旧、无任何告警。由回归 R10 拦截。
DAILY_UPDATE_GROUPS = OrderedDict([
    ("core", ["daily", "daily_basic", "fund_daily", "cb_daily",
              "index_daily", "adj_factor", "stk_limit"]),
    ("extend", ["moneyflow", "margin", "margin_detail", "margin_secs",
                "hk_hold", "fut_daily", "fut_mapping", "fut_settle",
                "top_list", "top_inst",
                "block_trade", "hsgt_top10", "ggt_top10", "suspend_d",
                # 2026-09-19 接口审查补充
                "etf_limit",
                # ⚠ 2026-09-21: us_daily **已移出本组**（改登记 DAILY_UPDATE_EXEMPT）。
                #   原因: ① 实测限频最紧维度是 **5次/天**（文案会随触发维度在
                #   1次/分钟｜1次/小时｜5次/天 之间变化），而 TS_RATE_MAX_RETRIES=12
                #   → 一次失败即重试 12 次、**直接烧穿当日配额并波及次日**
                #   （实证: 09-20 事故把配额烧光，09-21 未成功调用过仍被拒）；
                #   ② 全市场历史须走 akshare 通道（tushare 按日横截面，
                #   9041 交易日 ÷ 5次/天 ≈ 1808 天，不可行）。
                #   留在组里 = 每天撞配额，故归入豁免。
                # 2026-09-21 新增: by_range 日频利率表（原全族无归宿，静默变旧）
                #   背景: by_range 表此前**不在任何清单**，R10 也只扫 by_date
                #   → 实证 shibor 库内最新仅 2026-09-16（当日已 09-21）。
                #   本次同时改造 daily_update 支持 by_range 日更（否则是"假纳入"：
                #   原代码 `if conf.get("mode") != "by_date": 跳过`，进了组也不会跑）。
                "shibor", "us_tycr", "us_trycr",
                "us_tbr", "us_tltr", "us_trltr",
                # 2026-09-22 新增: 原登记豁免的**日频**表（复核后纳入）
                #   背景: 2026-09-22 复核发现这些表**全部是标准日频数据**
                #   （实测每年 222~243 个日期，与 A 股交易日数一致），
                #   当初的豁免理由部分混淆了「**行数少**」与「**频率低**」——
                #   如 fund_div 写着"低频事件(每日仅10-40行)"，
                #   而"每日 10~40 行"恰恰说明它是**日频、只是数据量小**。
                #   复核依据两条客观判据（见 文档/数据与工程/豁免表复核_20260922.md）：
                #     ① 161 个 .py 文件扫描 → 均无下游代码消费（"关联弱"成立）
                #     ② 数据密度 → 全部日频（242/243 日期/年）
                #   ⇒ 「无消费」不构成「不必日更」的理由：日频表不日更会逐日变旧，
                #     而成本极低（A 组各 4 请求/天；B 组 opt_daily 36、fut_holding 20）。
                #   ⚠ opt_daily/fut_holding 是成本大头（9页/5页分页），
                #     若日更压力大可单独回退到豁免（其余 8 张成本可忽略）。
                "eco_cal", "sge_daily", "repo_daily", "fund_div",
                "daily_info", "sz_daily_info",
                "fut_holding", "fut_wsr", "opt_daily", "bc_otcqt",
                # index_global: by_code 模式，靠 daily_full_refresh 标记走
                # "每日全量重拉"（22 请求/天）。含 IBOVESPA = 持仓标的指数。
                "index_global",
                # 2026-09-23 新增: fx_daily / index_dailybasic（by_date 日更）
                #   背景: 两表 mode 原为 by_code，不在日更组 → 逐日变旧
                #   (实证 09-23: fx_daily 库内最新 09-17、index_dailybasic 09-18，
                #    当日已 09-23)。实测两接口均支持 trade_date 按日拉全市场
                #   (fx 单日约101品种 / idb 单日约15指数)，1~2 请求/天，
                #   且两表磁盘已是 by_year 布局，直接走标准 by_date 日更路径。
                "fx_daily", "index_dailybasic"]),
])

# 每日更新默认只跑 core（main.py gap-update）；--group all 时加上 extend
DAILY_UPDATE_GROUPS_ALIASES = {"all": ["core", "extend"]}

# ------------------------------------------------------------
# 每日更新豁免清单 — 2026-09-19 新增
# ------------------------------------------------------------
# 用途: mode=by_date 且 freq=day 的表若**刻意不纳入每日更新**，必须在此登记
#       并写明原因。回归 R10 要求"每张这类表要么在组里、要么在豁免清单里"，
#       否则报错 —— 以此杜绝"新加接口只写属性定义、忘了登记执行清单"的漏登。
#
# ⚠ 值为**豁免原因**，不是可选项。写不出正当理由的表，说明它该被纳入。
# ⚠ freq != day 的表（weekly/monthly，周线/月线）由 R10 自动豁免：
#   它们本是"周末/月末才该跑一次"，机制上就不属于日更范畴，无需人工判断。
DAILY_UPDATE_EXEMPT = {
    # ⚠ 2026-09-22: index_global **已纳入 extend 组**（走 daily_full_refresh
    #   的"每日全量重拉"），不再豁免。原豁免理由（by_code 模式不被支持 +
    #   100次/天配额顾虑）已分别由 daily_update 的 by_code 分支和
    #   "不加复核窗口"的设计解决。
    # ⚠ 2026-09-22: 以下 10 张**已移出豁免、纳入 extend 组**（复核确认为日频）：
    #   eco_cal / sge_daily / repo_daily / fund_div / daily_info / sz_daily_info /
    #   fut_holding / fut_wsr / opt_daily / bc_otcqt
    #   移除它们曾经的豁免理由，供追溯：
    #     fut_holding: "tier C: 期货持仓排名, 单日约4000行, 与现有策略关联弱"
    #     fut_wsr:     "tier C: 期货仓单日报, 商品库存数据, 价值有限"
    #     opt_daily:   "tier C: 期权日线单日约1.5万行, 量大且暂无下游消费方"
    #     repo_daily:  "tier C: 债券回购日行情, 资金面利率, 已被 shibor 覆盖"
    #     bc_otcqt:    "tier C: 柜台债券报价, 单日约8160行需5页, 与现有策略关联弱"
    #     fund_div:    "tier C: 基金分红, 低频事件(每日仅10-40行), 无实时需求"
    #     sge_daily:   "tier C: 上海黄金现货, 非核心品种, 需用时手动补即可"
    #     daily_info:  "tier C: 沪深每日交易统计, baifenwei.com 有更直观版本"
    #     sz_daily_info: "tier C: 深市每日交易统计, 同上"
    #     eco_cal:     "tier C: 全球财经事件日历, 事件驱动型, 按需回补即可"
    #   ⚠ 其中 fund_div/eco_cal 的理由把"行数少"当"频率低"，是判断混淆；
    #     daily_info/sz_daily_info 的"别处有更直观版本"属工具替代、不改变数据本身是日频。
    # --- 2026-09-21: by_date 但刻意不日更 ---
    "us_daily":     "⚠ 2026-09-21 从 extend 组移入。tushare 通道**限频最紧维度 5次/天**"
                    "（实测文案随触发维度变化: 1次/分钟｜1次/小时｜**5次/天**），"
                    "而 TS_RATE_MAX_RETRIES=12 → 一次失败即重试 12 次、烧穿当日配额"
                    "并波及次日（实证 09-20 事故后 09-21 未成功调用过仍被拒）。"
                    "且美股**全市场**历史须走 akshare（tushare 按日横截面，"
                    "9041 交易日 ÷ 5次/天 ≈ 1808 天不可行）。"
                    "全市场建库方案: akshare stock_us_daily(新浪源, 按标的, "
                    "实测 0.24~0.65s/只、1984 年起、18,163 只约 1.5 小时)",
    # --- by_range 族（2026-09-21 新增：该模式原无任何归宿）---
    # 说明: by_range = "传 start_date/end_date 拉一段区间"，非逐日任务。
    #      需日更的 6 张利率表已纳入 extend 组（daily_update 已支持 by_range）；
    #      以下 4 张属**低频/事件型**，日更无意义，故登记豁免。
    "shibor_lpr":   "LPR 利率为**月度公布**（每月20日左右），日更无意义；"
                    "按月回补即可（1 次请求拉全区间，限额 1次/小时）",
    "hk_tradecal":  "港股交易日历：年度静态数据（实测 2016起 3915 行），"
                    "日更无意义；新年份日历发布后补一次即可",
    "us_tradecal":  "美股交易日历：同上（2016起 3914 行），年度静态数据",
    "new_share":    "IPO 新股上市：**事件驱动型**（有新股才新增），"
                    "非连续日频；按需区间回补即可",
}

# daily / daily_basic 单日行数已逼近 6000 上限（实测 5553），
# 加进分页表以防未来标的数扩容后被静默截断（每天多 1 次请求）。
# 其余核心表单日行数远低于上限, 无需分页。
# ⚠ BACKFILL_PAGED 是 BACKFILL_PAGE_CONF 的键快照(定义在 7b 节), 此处
#   必须一并同步, 否则 run_target 判断 `api in BACKFILL_PAGED` 仍为假。
BACKFILL_PAGE_CONF.update({
    "daily":       (5000, 5),
    "daily_basic": (5000, 5),
})
BACKFILL_PAGED.update({"daily", "daily_basic"})

# ============================================================
# 9. 盘中监控配置
# ============================================================
# 盘中扫描标的
MONITOR_SYMBOLS = [
    {"code": "000001", "secid": "1.000001", "name": "上证指数"},
    {"code": "399001", "secid": "0.399001", "name": "深证成指"},
    {"code": "399006", "secid": "0.399006", "name": "创业板指"},
    {"code": "159992", "secid": "0.159992", "name": "创新药ETF"},
    {"code": "159256", "secid": "0.159256", "name": "创业板软件ETF"},
    {"code": "588170", "secid": "1.588170", "name": "科创50ETF"},
    {"code": "002606", "secid": "0.002606", "name": "大连电瓷"},
]

# 盘中扫描周期: (名称, klt, 取数条数)
MONITOR_PERIODS = [
    ("5分钟", 5, 300),
    ("15分钟", 15, 300),
    ("30分钟", 30, 200),
    ("60分钟", 60, 200),
    ("日线", 101, 120),
]

# 东财请求间隔 (秒) — 分时K线(200根)是大请求，8秒会触发限流，必须≥15秒
EM_REQUEST_INTERVAL = 15

# 东财限流后的指数退避参数
EM_BACKOFF_BASE = 300       # 首次失败后等待300秒(5分钟)
EM_BACKOFF_MAX = 1800       # 退避上限1800秒(30分钟)
EM_BACKOFF_MULTIPLIER = 3   # 退避倍率: 300→900→1800(上限)
EM_MAX_CONSECUTIVE_FAILURES = 3  # 连续失败3次后放弃本轮扫描，等下一轮定时触发

# ============================================================
# 9b. 新浪分时K线备用数据源 (8/18验证可用)
# ============================================================
# 东财push2his限流后自动切换到新浪API
# 新浪API响应0.15秒/次(东财需15秒间隔)，无限流，但只返回最近约8交易日数据
SINA_KLINE_URL = "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
SINA_REQUEST_INTERVAL = 2   # 新浪请求间隔2秒(实测0.15秒响应，2秒已非常保守)
# [ARCHIVED 2026-09-25 G1] 死配置：全库 grep（83 文件，含字符串/getattr 形态）+工作区/skills 交叉核实均无引用。原用途：旧新浪限频参数（现行走 fetch 层统一限流）。保留定义保行号稳定，勿新增引用。
SINA_DAILY_LIMIT = 2000     # datalen最大值(实测300完全够用)

# klt→新浪scale映射 (1=1分,5=5分,15=15分,30=30分,60=60分,101=日线)
KLT_TO_SINA_SCALE = {
    1: 1,
    5: 5,
    15: 15,
    30: 30,
    60: 60,
    # 101=日线: 新浪此API不支持日线，日线走tushare
}

# ============================================================
# 10. 差额补全配置
# ============================================================
# 采样标的：用于探测数据库最新日期
# 按目录类型分开配置（个股/基金/可转债各用对应类型的代码）
# 需要分页拉取的API (按trade_date返回行数可能超过5000行限制)
PAGED_APIS = {"index_daily"}  # 指数日线~10000+个, 需offset分页

GAP_UPDATE_SAMPLE_CODES = {
    "daily":         ["000001.SZ", "600519.SH"],           # 个股: 平安银行、贵州茅台
    "daily_basic":   ["000001.SZ", "600519.SH"],           # 个股: 同上
    "fund_daily":    ["510050.SH", "510300.SH", "159915.SZ"],  # ETF: 上证50、沪深300、创业板
    "cb_daily":      ["113605.SH", "123269.SZ", "127080.SZ"],  # 可转债: 在市活跃券 (避免用已退市券)
    "index_daily":   ["000300.SH", "000001.SH", "399006.SZ"],  # 指数: 沪深300、上证综指、创业板指
}
