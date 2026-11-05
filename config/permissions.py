# -*- coding: utf-8 -*-
"""config.permissions — 权限漏网登记 + 日更空转豁免清单。"""

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
        "use": "★ 宽基 ETF / LOF 轮动复权价（fund_daily 未复权价遇份额折算会假暴涨）",
        "alt": "[2026-09-25 复核订正] fund_nav（2000 档**正式权限**）自带 **adj_nav 复权"
               "净值**字段，直取即可、无需自算 —— 替代**基本完整**（原记「净值+分红自算、」"
               "精度未验证」已过时）。⚠ 精度与 fund_adj 的等价性**未做逐日对账**，"
               "失效时须先对账再切换，不得默认等价。",
        "monitor": True,
    },
    "opt_daily": {
        "claimed": 5000, "kind": "quota",
        "use": "期权杠杆监控（iv_tracker/leverage 运行时调用）",
        "alt": "akshare option_sse_daily_sina（部分覆盖）",
        "monitor": False,      # 未建库、无长期依赖，命中时不必告警刷屏
    },
    # ── 独立权限漏网（2 项）──
    # ⚠ 2026-10-01 两者的 monitor 由 True 降为 **False**：它们**本地无表**（不走
    #   Tushare 日更那条路），doctor 的空转判定对它们本来就是噪音 —— 既不在
    #   DAILY_UPDATE_GROUPS 也不会被日更引擎触及，报「⛔ 空转」是误报。
    #   新鲜度由**替代源侧**（akshare alt 库 66 表，落 D:\全量数据\alt_data）自带检查；
    #   doctor 的「本地无表」分支仍会列出它们（信息，非告警）。
    "hk_daily": {
        "claimed": "独立权限 1000元/年", "kind": "indep",
        "use": "港股日线（港股二件套）",
        "alt": "akshare stock_hk_daily（新浪源，实测 5476 行 / 2004 起）"
               "｜**替代源自带新鲜度检查**，无需 Tushare 侧漏网告警",
        "monitor": False,
    },
    "us_daily": {
        "claimed": "独立权限 2000元/年", "kind": "indep",
        "use": "美股日线（三件套之一）",
        "alt": "akshare stock_us_daily（新浪源，实测 10035 行 / 1984 起，延迟 15 分钟）"
               "｜**替代源自带新鲜度检查**，无需 Tushare 侧漏网告警",
        "monitor": False,
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

# ------------------------------------------------------------
# 漏网接口「日更空转」豁免清单 — 2026-10-01 新增
# ------------------------------------------------------------
# 用途: LEAKY_PERMISSIONS 中 monitor=True 的接口，若**按设计就不该每日调用**
#       （接口语义不支持 / 全量重拉代价不可接受 / 走替代源），必须在此登记并
#       写明原因。doctor 的「漏网接口」检查据此把"设计豁免"与"真空转"分开：
#       ⛔ 空转 = 本该每日调用却没人调（真风险，必须进日更组）；
#       －  豁免 = 刻意不日更（可接受，但须有理由且可追溯）。
# ⚠ 值为**豁免原因**，不是可选项。写不出正当理由的，说明它该进日更组。
LEAKY_IDLE_EXEMPT = {
    "share_float":
        "⚠ 2026-10-01 实证：trade_date 参数**不起筛选作用**（传 20260930 / "
        "20250122 / 20230101 均返回同一批 6,000 行，float_date 恒在 "
        "20270914~20340120 未来窗口）⇒ by_date 日更既拿不到 20060403~20270913 "
        "的历史（库内 4,521,447 行 / 5,535 标的全靠 by_code），又每天重复拉同一批。"
        "真实数据只能 ts_code 逐只拉。by_code + daily_full_refresh 需 5,535 请求/天，"
        "对 3,000 分漏网接口不可接受 ⇒ 登记为「按需补库 + 新鲜度人工关注」。",
    "broker_recommend":
        "★ 接口**必填 month**（非 trade_date/ts_code），mode 已是 by_month，"
        "而日更引擎 engine_supports_daily 只放行 by_date / by_range / "
        "by_code+daily_full_refresh ⇒ by_month 不在引擎日更范畴内，"
        "结构上就不可能每日调用（非配置疏漏）。按月回补即可；"
        "替代源 akshare stock_institute_recommend **实测返空、替代不成立**。",
}
