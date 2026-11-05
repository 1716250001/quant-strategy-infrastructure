# -*- coding: utf-8 -*-
"""tools/consumer_pool.py — 消费侧标的池（W5-C1，2026-11-05）

2026-11-05 自 strategies/ 冻结，此后本文件为真源。
来源：
  QIDIAN_POOL / XAU_WATCHLIST      ← strategies/qidian.py（2026-10-21 停止裁定前版本）
  WIND_CB_THERMOMETER              ← strategies/qidian_daily_scan.py
策略计算函数不在本文件，仅在 tools/consumer_coverage.py 中消费。
"""

QIDIAN_POOL = {
    # ── 推荐操作池 14个 ★ ──
    "560700.SH": {"name": "央企股东回报ETF", "recommended": True, "indices": [
        {"code": "932039.CSI", "name": "央企股东回报"},
    ]},
    "512800.SH": {"name": "银行ETF", "recommended": True, "indices": [
        {"code": "399986.SZ", "name": "中证银行"},  # 2026-10-15 统一为基金合同跟踪指数（审计_20260917 §3.2；tushare 后缀须 .SZ）
    ]},
    "515630.SH": {"name": "800证券保险ETF", "recommended": True, "indices": [
        {"code": "399966.SZ", "name": "中证800证券保险"},  # 2026-10-15 修配对：931479（证券保险领先）→399966（合同跟踪中证800证券保险，审计_20260917 §3.1）
    ]},
    "160639.SZ": {"name": "高铁产业LOF", "recommended": True, "indices": [
        {"code": "399807.SZ", "name": "高铁产业"},
    ]},
    "515300.SH": {"name": "300红利低波ETF", "recommended": True, "indices": [
        {"code": "930740.CSI", "name": "300红利低波"},
    ]},
    "159793.SZ": {"name": "线上消费ETF", "recommended": True, "indices": [
        {"code": "931481.CSI", "name": "沪港深线上消费"},  # 2026-10-15 修配对：931480→931481（合同跟踪，审计_20260917 §3.1）
    ]},
    "510990.SH": {"name": "180ESG ETF", "recommended": True, "indices": [
        {"code": "931088.CSI", "name": "180ESG"},
    ]},
    "515220.SH": {"name": "中证煤炭ETF", "recommended": True, "indices": [
        {"code": "399998.SZ", "name": "中证煤炭"},
    ]},
    "159995.SZ": {"name": "芯片ETF", "recommended": True, "indices": [
        {"code": "931865.CSI", "name": "半导体"},
    ]},
    "515880.SH": {"name": "通信ETF", "recommended": True, "indices": [
        {"code": "931160.CSI", "name": "通信设备"},
    ]},
    "515580.SH": {"name": "科技100ETF", "recommended": True, "indices": [
        {"code": "931187.CSI", "name": "中证科技100"},  # 2026-10-15 修配对：原「保险ETF/399809」双重错配（实为华泰柏瑞中证科技100ETF，合同跟踪931187，审计_20260917 §3.1）
    ]},
    "512200.SH": {"name": "房地产ETF", "recommended": True, "indices": [
        {"code": "399393.SZ", "name": "房地产"},
    ]},
    "512400.SH": {"name": "有色金属ETF", "recommended": True, "indices": [
        {"code": "000819.SH", "name": "中证申万有色"},  # 2026-10-15 统一为基金合同跟踪指数（审计_20260917 §3.2）
    ]},
    "159666.SZ": {"name": "物流ETF", "recommended": True, "indices": [
        {"code": "H30171.CSI", "name": "中证全指运输"},  # 2026-10-15 修配对：H30315→H30171（合同跟踪，审计_20260917 §3.1）
    ]},
    # ── 双回测一致优秀 23个 ──
    "517770.SH": {"name": "游戏传媒ETF", "recommended": False, "indices": [
        {"code": "931580.CSI", "name": "沪港深游戏及文化传媒"},  # 2026-10-15 修配对：399248→931580（合同跟踪，审计_20260917 §3.1）
    ]},
    "589070.SH": {"name": "科创芯片设计ETF", "recommended": False, "indices": [
        {"code": "950162.CSI", "name": "科创芯片设计"},
    ]},
    "159530.SZ": {"name": "机器人ETF", "recommended": False, "indices": [
        {"code": "980022.SZ", "name": "机器人产业"},
        {"code": "H30590.CSI", "name": "机器人(国证)"},
    ]},
    "159730.SZ": {"name": "龙头家电ETF", "recommended": False, "indices": [
        {"code": "980028.SZ", "name": "龙头家电"},
    ]},
    "159760.SZ": {"name": "公共卫生ETF", "recommended": False, "indices": [
        {"code": "H30344.CSI", "name": "健康产业"},
    ]},
    "560990.SH": {"name": "科技先锋ETF", "recommended": False, "indices": [
        {"code": "931447.CSI", "name": "科技先锋"},
    ]},
    "515000.SH": {"name": "科技龙头ETF", "recommended": False, "indices": [
        {"code": "931087.CSI", "name": "中证科技龙头"},  # 2026-10-15 修配对：931186→931087（合同跟踪，审计_20260917 §3.1）
    ]},
    "512880.SH": {"name": "全指证券公司ETF", "recommended": False, "indices": [
        {"code": "399975.SZ", "name": "证券公司"},
    ]},
    "159865.SZ": {"name": "畜牧养殖ETF", "recommended": False, "indices": [
        {"code": "930707.CSI", "name": "中证畜牧"},
    ]},
    "562530.SH": {"name": "1000价值稳健ETF", "recommended": False, "indices": [
        {"code": "931588.CSI", "name": "中证智选1000价值稳健"},  # 2026-10-15 修配对：399631→931588（合同跟踪；原配对曾致20260917指数侧假信号，审计_20260917 §3.1）
    ]},
    "159692.SZ": {"name": "证券公司30ETF", "recommended": False, "indices": [
        {"code": "931412.CSI", "name": "证券公司30"},
    ]},
    "515960.SH": {"name": "医药健康100ETF", "recommended": False, "indices": [
        {"code": "931166.CSI", "name": "医药健康100"},
    ]},
    "561500.SH": {"name": "核心竞争力50ETF", "recommended": False, "indices": [
        {"code": "931526.CSI", "name": "企业核心竞争力50"},
    ]},
    "516830.SH": {"name": "300ESG ETF", "recommended": False, "indices": [
        {"code": "931463.CSI", "name": "300ESG"},
    ]},
    "160127.SZ": {"name": "新兴消费LOF", "recommended": False, "indices": [
        {"code": "000942.CSI", "name": "内地消费"},  # 原报告399942.SZ无数据→替换
    ]},
    "159523.SZ": {"name": "300成长创新ETF", "recommended": False, "indices": [
        {"code": "931589.CSI", "name": "300成长创新"},
    ]},
    "159620.SZ": {"name": "500成长创新ETF", "recommended": False, "indices": [
        {"code": "931590.CSI", "name": "500成长创新"},
    ]},
    "516130.SH": {"name": "消费龙头ETF", "recommended": False, "indices": [
        {"code": "931068.CSI", "name": "消费龙头"},
    ]},
    "563980.SH": {"name": "800红利低波ETF", "recommended": False, "indices": [
        {"code": "931848.CSI", "name": "800红利低波"},
    ]},
    "512660.SH": {"name": "军工ETF", "recommended": False, "indices": [
        {"code": "399967.SZ", "name": "中证军工"},  # 2026-10-15 统一为基金合同跟踪指数（审计_20260917 §3.2）
    ]},
    "501030.SH": {"name": "环境治理LOF", "recommended": False, "indices": [
        {"code": "399806.SZ", "name": "环境治理"},
    ]},
    "589720.SH": {"name": "科创创新药ETF", "recommended": False, "indices": [
        {"code": "980086.CNI", "name": "创新药"},
    ]},
    "517160.SH": {"name": "长江保护ETF", "recommended": False, "indices": [
        {"code": "931554.CSI", "name": "长江保护"},
    ]},
    # ── 2026-08-26行业ETF扫描新增 4个（非推荐池）──
    "561910.SH": {"name": "电池ETF", "recommended": False, "indices": [
        {"code": "931719.CSI", "name": "电池主题"},
    ]},
    "159992.SZ": {"name": "创新药ETF", "recommended": False, "indices": [
        {"code": "931152.CSI", "name": "中证创新药产业"},  # 2026-10-15 修配对：399993→931152（合同跟踪，审计_20260917 §3.1）
    ]},
    "159611.SZ": {"name": "电力ETF", "recommended": False, "indices": [
        {"code": "H30199.CSI", "name": "中证全指电力公用事业"},  # 2026-10-15 修配对：399554→H30199（合同跟踪，审计_20260917 §3.1）
    ]},
    "512980.SH": {"name": "传媒ETF", "recommended": False, "indices": [
        {"code": "399971.SZ", "name": "中证传媒"},
    ]},
    # ── 2026-08-30 可转债ETF新增 2只（非推荐池，奇点战法泛化测试标的）──
    # 注: 511180原配对指数H11049.CSI系错码——tushare与中证官网双重核实该代码实为
    #     "AMAC文体"股票行业指数(基日20081231)，行情数据与转债市场无关（20260903实锤）
    #     511180真实跟踪指数为950041.CSI(上证投资级转债及可交换债)，但tushare中该指数
    #     仅提供close(无OHLC)+数据滞后1日，故双重确认配对000832.CSI(中证转债，OHLC完整)
    "511180.SH": {"name": "可转债ETF海富通", "recommended": False, "indices": [
        {"code": "000832.CSI", "name": "中证转债"},
    ]},
    "511380.SH": {"name": "可转债ETF博时", "recommended": False, "indices": [
        {"code": "931078.CSI", "name": "中证转债及可交换债"},
    ]},
}


XAU_WATCHLIST = {
    "XAUUSD.FXCM": {"name": "伦敦金现(XAU/USD)", "source": "FXCM"},
}


WIND_CB_THERMOMETER = {
    "889033": {"name": "万得转债等权(温度计)", "role": "情绪温度计·等权口径"},
    "889047": {"name": "万得转债双低(策略基准)", "role": "双低策略基准"},
    "889043": {"name": "万得转债低价(防守锚)", "role": "低价防守锚"},
}

