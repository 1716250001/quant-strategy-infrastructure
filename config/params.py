# -*- coding: utf-8 -*-
"""config.params — 计算参数常量 + IV/波动率监控配置。"""

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

