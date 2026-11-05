# -*- coding: utf-8 -*-
"""config.paths — 路径单一真源（W3-CFG-01 已确立：锚点派生 + 环境变量覆盖）。"""
import os

# ============================================================
# 0. 路径配置 — 全局唯一定义
# ============================================================
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 代码根（config/ 的上一级）
CODE_DIR = _BASE_DIR                                              # 代码目录（包根），公开名
PROJECT_DIR = os.path.dirname(_BASE_DIR)                          # A股三层轮动金字塔策略/
# 盘符锚点：数据盘随项目盘走（与 btf/config/paths.py 同哲学，ADR-4）
_DATA_DRIVE = os.path.splitdrive(PROJECT_DIR)[0]          # 形如 "D:"
# 三库共同父级；CHICHAO_DATA_ROOT 可整体迁移（换盘/挂载点）
DATA_ROOT = os.path.abspath(
    os.environ.get("CHICHAO_DATA_ROOT") or os.path.join(_DATA_DRIVE, os.sep, "全量数据")
)
DATA_DIR = os.path.join(PROJECT_DIR, "数据", "daily_data")        # 日报数据 (JSON/CSV)
MARKET_DATA_DIR = os.path.abspath(
    os.environ.get("CHICHAO_MARKET_DATA_DIR") or os.path.join(DATA_ROOT, "market_data")
)
ALT_DATA_DIR = os.path.abspath(
    os.environ.get("CHICHAO_ALT_DATA_DIR") or os.path.join(DATA_ROOT, "alt_data")
)
WIND_DATA_DIR = os.path.abspath(
    os.environ.get("CHICHAO_WIND_DATA_DIR") or os.path.join(DATA_ROOT, "wind_data")
)
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

