# -*- coding: utf-8 -*-
"""config.daily — 尾盘增量 / 基金净值 / 每日更新分组与豁免 / 周期更新配置。"""
import os
from collections import OrderedDict

from config.paths import (
    CB_DAILY_DIR,
    FUND_DAILY_DIR,
    MARKET_DATA_DIR,
    STOCK_DAILY_BASIC_DIR,
    STOCK_DAILY_DIR,
)

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
    # INDEX_DAILY_DIR 由 config.universe 定义；子模块间不互引（D5），
    # 此处按同一锚点派生，值恒等于 config.INDEX_DAILY_DIR。
    "index_daily":   (os.path.join(MARKET_DATA_DIR, "index_daily"), "ts_code", "trade_date"),
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
        # ⚠ 2026-10-11（E-04，三轮架构审查）：**主册登记 ≠ 主库有实体**。
        #   `us_daily` 的实体在 **alt 库**（`D:\全量数据\alt_data\us_daily*`），
        #   由 `alt/spec.py` 的 `us_daily` / `us_daily_qfq` 条目维护（mode=list）。
        #   本条目仅为"该接口在主册有属性定义/限频登记"的留痕；主库侧不建表。
        #   查数据请走 `alt.reader`，勿按本条在 `market_data` 找 us_daily。
    }),
])

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
                # 2026-10-28 DB-R2：沪深港通资金流向（⚠ 须先建库，见 18 号 §176 O-1：
                #   空库时 resolve_gaps 会返回 2014→今天的单区间，by_range 无截断检测 ⇒ 静默残缺）
                "moneyflow_hsgt", "ggt_daily",
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
                "fx_daily", "index_dailybasic",
                # 2026-10-01 新增: fund_adj（基金/ETF 复权因子，by_date 日更）
                #   背景: 与 fx_daily 同一病症——mode 原为 by_code（日更引擎静默跳过）、
                #   且不在本清单 ⇒ 库内停在 20260918 逐日变旧，而 fund_daily 已到 09-30。
                #   实证: 接口支持 trade_date 按日拉全市场（单日 2,183 行/2,183 只），
                #   **1 请求/天**（已同步改 mode=by_date）。
                #   ⚠ 关键副作用修复: 它是 7 个漏网接口之一（官方 5000 分/本档 2000），
                #   LEAKY_PERMISSIONS 登记 monitor=True，但告警依赖"接口被调用过"——
                #   不进日更 ⇒ 无人调用 ⇒ **漏网告警空转**。纳入本组后失效才可被日发现。
                "fund_adj",
                # 2026-11-01 DB-R12：南华指数日线（by_code，5 个指数 .NH）
                #   背景: 配置有定义但**不在任何日更组** ⇒ gap-update --group all 从不碰它，
                #   库内停在 20260918（缺 20260919~20261030 共 24 交易日 ≈120 行）。
                #   先例: core 的 fund_daily / extend 的 index_global 同为 by_code 模式。
                "fut_index_daily",
                # 2026-11-01 DB-R15: 六张低频表接入日更（陈旧 41~60 天，体检见 18 号 §261）
                #   5 张 by_date：周线/周月复权/月度宏观，按 trade_date 拉全市场，各 1 请求/缺口日
                "weekly", "stk_weekly_monthly", "stk_week_month_adj",
                "fut_weekly_monthly", "monthly",
                #   index_weekly：by_code → 必须带 daily_full_refresh 标记（同 index_global 先例），
                #   12 个指数全量重拉 = 12 请求/天
                "index_weekly",
                # 2026-11-02 DB-R16: forecast（业绩预告）接入日更
                #   原 mode=by_code 且不在任何日更组 ⇒ 从未被调度、2024 分区仅 1 行；
                #   改 by_date + date_param=ann_date（同 fund_div），每日 1 请求、约 1~2 行。
                "forecast"]),
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

# ------------------------------------------------------------
# 事件面表的「周期性更新」归宿 — 2026-10-11 新增（三轮审查 E-01 专项）
# ------------------------------------------------------------
# 用途: 补齐**第三种归宿**。此前每张表只有两种状态：
#   ① 在 DAILY_UPDATE_GROUPS（每天跑）② 在 DAILY_UPDATE_EXEMPT（刻意不日更，须写理由）。
#   但 DAILY_UPDATE_EXEMPT 的判据只覆盖 by_date / freq=day 的表 ⇒ **by_code 的事件面表
#   落在两者之外 = 无归宿、无陈旧监控**。
#
# 背景（2026-10-11 三轮架构审查 E-01，中级别）:
#   `dividend`（公司行动装载源，btf `feed.py` 区间实施态）与 `namechange`（ST 判定源，
#   btf `state.py`）均为 tier A/B 的 btf 股票侧**数据源**，mode=by_code、既不在日更组、
#   也不在豁免清单，且**没有任何陈旧监控** ⇒ 实证 namechange 库内最新 start_date
#   停在 20260912（审查日已滞后 4 周），dividend 靠手动续命（表内含未来 ex_date）。
#   危害形态：表内「无行」与「真无事件」不可区分 —— 新分红/送转/ST 事件**静默缺失**，
#   近端股票回测结果出错且无 fail-closed 防线。
#
# 为什么不进 DAILY_UPDATE_GROUPS（成本核算，实测）:
#   两表 mode=by_code 且 codes=stock_all（约 5,900 标的）⇒ 每天全量重拉 = 5,900
#   请求/天/表（170次/分 ⇒ 约 35 分钟/表），日更代价与收益完全不匹配。
#   实测接口**支持按日期拉取**（2026-10-11 探针）：
#     · namechange  `start_date`/`end_date`（公告日期）区间拉取 —— 1 请求/次
#     · dividend    `ann_date`（公告日期）单日横截面 —— 1 请求/日
#   ⇒ 正确的形态是「**周期性 + 增量窗口**」：每 interval_days 跑一次，
#     只拉 (最近 lookback_days) 窗口，落盘按主键去重 ⇒ 幂等、可重放。
#
# 纪律（由回归 R10 校验，缺一报错）:
#   ① 每项必须写明 `reason` 与 `interval_days`（写不出理由的说明它该进日更组）；
#   ② 不得与 DAILY_UPDATE_GROUPS 重复（双通道 = "两个结构各管一摊"的复发形态）；
#   ③ `recency_col` 必须真实存在于该表（陈旧监控据此判新鲜度）。
# ⚠ 陈旧监控落点：`tools/doctor.py` 第 10 项「事件面表」（stale_after_days）；
#   更新执行落点：`fetch/periodic_update.py`（命令 `python -m fetch.periodic_update`
#   或 `main.py periodic-update`）。
PERIODIC_UPDATE_TARGETS = OrderedDict([
    ("namechange", {
        "desc": "股票曾用名（ST/借壳识别；btf state.py ST 判定装载源）",
        # ── 取数形态：一次区间请求（公告日期区间）──
        "pull": "by_range",
        "start_param": "start_date", "end_param": "end_date",
        # ── 落盘形态（须与 TABLE_DATE_COL / DB_AUDIT_PKEYS 一致）──
        "date_col": "start_date",
        "subset": ["ts_code", "start_date"],
        # ── 新鲜度信号列：公告日（分区列是"生效日"，可能含未来值，不可作新鲜度信号）──
        "recency_col": "ann_date",
        "interval_days": 7,            # 每周一次（事件稀疏，实测 4 周 19 条）
        "lookback_days": 60,           # 每次回看窗口（覆盖"更名生效日晚于公告日"的情形）
        "stale_after_days": 45,        # 库内最新 ann_date 落后超此天数 ⇒ doctor 告警
        "reason": "ST/借壳判定的事件面装载源：既不该日更（全市场 by_code 重拉 5,900 请求/"
                  "天），也不能无归宿（实证停更 4 周无人发现）。改走**每 7 天一次**的"
                  "公告日区间增量：1 请求/次，落盘幂等。",
    }),
    ("dividend", {
        "desc": "分红送股（btf feed.py 公司行动装载源，tier A）",
        # ── 取数形态：逐公告日横截面（接口无区间参数，按 ann_date 单日拉全市场）──
        "pull": "by_date",
        "date_param": "ann_date",
        # ── 落盘形态 ──
        "date_col": "end_date",
        "subset": ["ts_code", "end_date", "ann_date", "div_proc"],
        # ── 新鲜度信号列：公告日（end_date 是报告期，无法反映"有无新公告"）──
        "recency_col": "ann_date",
        "interval_days": 7,
        "lookback_days": 60,
        "stale_after_days": 60,        # 分红公告季节性（年报 4~7 月密集）⇒ 阈值放宽
        "reason": "公司行动装载源：除权除息/送转/派息的原始事件表。分季公告（年报/中报/"
                  "季报）⇒ 日更无收益、却要 5,900 请求/天。改走**每 7 天一次**的公告日"
                  "横截面增量（1 请求/日，窗口内约 5 请求/次），落盘幂等。",
    }),
    ("share_float", {
        "desc": "限售股解禁（by_date 不可用 2026-10-01 实证；ann_date 单日可用 2026-11-08 P7 实证）",
        # ── 取数形态：逐公告日横截面（by_date 的 trade_date 参数不生效）──
        "pull": "by_date",
        "date_param": "ann_date",
        # ── 落盘形态 ──
        "date_col": "ann_date",
        "subset": ["ts_code", "float_date", "ann_date", "holder_name"],
        # ── 新鲜度信号列：公告日（float_date 是未来解禁日，含未来值，不可作新鲜度信号）──
        "recency_col": "ann_date",
        "interval_days": 7,
        "lookback_days": 60,
        # 单日翻页：源端单次硬顶 6,000 行（实测 20261102=11,794、20261104=9,056 行）
        "page_size": 6000,
        "stale_after_days": 30,
        "reason": "解禁公告日均有量（P7 实测单日 49 行）但 by_code 断点 5,920 全完成挡死 gap-update、"
                  "by_date(trade_date) 参数不生效（2026-10-01 实证），属四无孤儿表，10-30 后增量归零。"
                  "ann_date 单日横截面每 7 天增量：首次窗口 ≤10 请求，此后 ≤8 请求/次，落盘幂等。",
    }),
])
