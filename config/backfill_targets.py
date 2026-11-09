# -*- coding: utf-8 -*-
"""config.backfill_targets — 补数目标清单（纯数据，W3b 起独立成文件）。

豁免「单文件 ≤500 行」标准：本文件为单一字面量（BACKFILL_TARGETS），
无逻辑；按行拆会改变 OrderedDict 构造方式，引入键冲突与顺序风险。
"""
from collections import OrderedDict

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
        # DB-R10b：自然键取自 DB_AUDIT_PKEYS，防"同日重拉值变化→重复入库"
        "subset": ["ts_code", "trade_date"],
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
        # DB-R10b：自然键取自 DB_AUDIT_PKEYS，防"同日重拉值变化→重复入库"
        "subset": ["trade_date", "ts_code"],
        "note": "单日约4400行",
    }),
    ("margin_secs", {
        "api": "margin_secs", "desc": "两融标的清单", "mode": "by_date",
        "max_rows": 0, "tier": "B", "start": "20120101",
        # DB-R10b：自然键取自 DB_AUDIT_PKEYS，防"同日重拉值变化→重复入库"
        "subset": ["trade_date", "ts_code"],
        "note": "单日约4450行",
    }),
    ("moneyflow", {
        "api": "moneyflow", "desc": "个股资金流向", "mode": "by_date",
        "max_rows": 0, "tier": "A", "start": "20080101",
        # DB-R10b：自然键取自 DB_AUDIT_PKEYS，防"同日重拉值变化→重复入库"
        "subset": ["trade_date", "ts_code"],
        "note": "单日约5540行; β四因子的资金流",
    }),
    ("fund_adj", {
        "api": "fund_adj", "desc": "基金复权因子", "mode": "by_date",
        "max_rows": 0, "tier": "A", "start": "19991022",
        "allow_codes_file": True,  # A 段 2026-10-17 特例：by_date 形态允许 codes-file 补历史
        # DB-R10b：自然键取自 DB_AUDIT_PKEYS，防"同日重拉值变化→重复入库"
        "subset": ["ts_code", "trade_date"],
        "note": "宽基ETF/LOF轮动**必须用复权价**（fund_daily 是未复权价，ETF 份额折算会"
                "造成单日假暴涨，实证 159300.SZ 2024-06-25 +255.8%）。"
                "★2026-10-01 纳入日更(extend组): mode 由 by_code 改 by_date —— "
                "实测接口支持 trade_date 按日拉全市场（单日 2,183 行/2,183 只，"
                "**1 请求/天**），与 fx_daily(09-23) 同款改造。"
                "⚠ 库内 19991022~20260918（2,762 只/311 万行）系当年 by_code 建库产物，"
                "日更只做增量、不受影响；**若整库重建需临时改回 by_code**——by_date 形态"
                "按日拉取**不含已摘牌标的**，历史覆盖面会变窄，且 1999 起约 6,500 交易日"
                "≈ 6,500 请求。start 与 SOURCE_START_BASELINE 一致（19991022）。"
                "⚠ **漏网接口**（官方门槛 5000 分，本账号 2000 档）：进日更后由每日调用"
                "兜住失效告警；日更失败须视为「可能已失效」，消费侧"
                "**不得静默退回未复权价**（见 md_core.quotes.get_fund_daily_adj 的 stale 断言）。"
                "⚠ **源端豁免面 21 只（0.7%）**：有 fund_daily 行情但源端**无** fund_adj 数据"
                "（2026-10-01 实证：带 start/end 区间调用仍返 0，已用 510300.SH 对照组排除"
                "调用形态问题），多为 7 位分级/派生码（1500011.SZ/1610221.SZ/5020561.SH…）"
                "与个别 LOF（501049.SH/501063.SH…）——**属源端无数据，非入库缺口，勿重复排查**。",
    }),
    ("fund_share", {
        "api": "fund_share", "desc": "基金份额规模", "mode": "by_code",
        "codes": "fund_etf", "max_rows": 5000, "tier": "A",
        "note": "汇金ETF化趋势判断口径",
    }),
    ("fund_daily", {
        "api": "fund_daily", "desc": "ETF/LOF日线（按代码补历史）", "mode": "by_code",
        "codes": "fund_etf", "max_rows": 5000, "tier": "A",
        "audit_skip": True,
        "note": "★2026-10-17 EE-FUND-1 新增：by_code 补历史入口（DAILY_UPDATE_EXTRA 另存 by_date 日更路径，共享同一 checkpoint 文件）。"
                "仅收 A 类（库内 0 行）；B 类（库内有数据但起点晚于上市 >30 天）需 --force 或 A 段脚本。"
                "⚠ audit_skip=True 原因：checkpoint 里 fund_daily 的 completed 键是 by_date 日期串（DAILY_UPDATE_EXTRA 写），"
                "本条目 mode=by_code 期望的是代码串——比对模式错配导致 db_audit 算全 miss 非真实缺口。"
                "历史缺口可见性由 check-coverage --history 专管，db_audit 只管增量断点一致性。"
                "根因：同表双入口（by_date 日更 + by_code 补历史）共享 checkpoint 但键型不同——待 A 段执行后评估是否统一入口。",
    }),
    ("fund_nav", {
        "api": "fund_nav", "desc": "基金净值（按代码补历史）", "mode": "by_code",
        "codes": "fund_etf", "max_rows": 5000, "tier": "A",
        "audit_skip": True,
        "note": "★2026-10-17 EE-FUND-1 新增：fund_nav_update.py 仅 5 日回看增量（FUND_NAV_LOOKBACK_DAYS=5），"
                "本条目提供 by_code 全历史补数入口（含首次收录自愈）。仅收 A 类（库内 0 行）；"
                "B 类（起点晚）需 --force 或 A 段脚本。"
                "⚠ audit_skip=True 原因：fund_nav 在 checkpoint 中无 DAILY_UPDATE_EXTRA 写入的 completed 记录"
                "（fund_nav_update.py 不走 backfill checkpoint），本条目首次运行会把 2963 只代码全部算 pending——"
                "但 fund_nav 全表 A=817（含非交集）≠ 派单要求的 173（226 交集中），范围超限，故暂由 audit_skip 跳过。"
                "历史缺口可见性由 check-coverage --history 专管；fund_nav 全表缺口属另立项（派单 §19.6 第 6 项）。",
    }),
    ("fx_daily", {
        "api": "fx_daily", "desc": "外汇日线", "mode": "by_date",
        "start": "20070521", "max_rows": 0, "tier": "A",
        # DB-R10 R10-4：无 subset ⇒ 走 "*" 全列判重，而同日两次快照的值本来就不同
        #   （AUDUSD.FXCM 20261027 的 bid_close 0.70289/0.70523、tick_qty 3,510/169,698）
        #   ⇒ 被判成两行（20260923 起重复 1,402 行 / 全表 1,543 行）。
        #   改按 (ts_code, trade_date) 判重：merge_append(keep="last") 让后到的
        #   完整快照覆盖先到的残快照，正是所需语义。
        "subset": ["ts_code", "trade_date"],
        "note": "巴西ETF证伪条件 BRLCNY 破1.25连5日。"
                "★2026-09-23 纳入日更(extend组): mode 由 by_code 改 by_date —— "
                "实测接口支持 trade_date 按日拉全市场(单日约101品种, 1请求/天)。"
                "⚠库内 1938~2007 稀疏历史(XAUUSD伦敦金等)系当年 by_code 建库产物, "
                "日更不受影响; 若整库重建需临时改回 by_code(69请求/标的拉全历史)。"
                "⚠缺口按 SSE 日历计算: A股长假(国庆/春节)期间外汇照常交易但不会被自动补, "
                "如需覆盖手动 --lookback 扩窗。start 与 SOURCE_START_BASELINE 一致(20070521)。"
                "⚠同一交易日重复拉取会产出两份快照(bid_close/tick_qty 等值不同), "
                "故 subset 已改为 (ts_code, trade_date) 按主键判重(2026-10-29, DB-R10 R10-4)。",
    }),
    ("forecast", {
        "api": "forecast", "desc": "业绩预告", "mode": "by_date",
        "start": "20060101", "date_param": "ann_date", "date_col": "ann_date",
        "max_rows": 0, "tier": "A",
        "subset": ["ts_code", "ann_date", "end_date"],
        "audit_no_stale": True,   # 事件表：公告并非每个交易日都有，空日≠缺日（§320 F3）
        "note": "业绩预告（涛涛战法业绩雷规避）；"
                "⚠ 参数名是 **ann_date**（非 trade_date），同 fund_div；"
                "⚠ 不支持只传 period（实测报『ann_date和ts_code至少输入一个』）；"
                "按公告日横截面拉全市场，**每日约 1~2 行**（稀疏事件）；"
                "存量 20240808 前为 by_code 逐只回补、2024 分区仅 1 行 ⇒ 需按日回补 20240809 起",
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
        # DB-R10b：自然键取自 DB_AUDIT_PKEYS，防"同日重拉值变化→重复入库"
        "subset": ["trade_date", "ts_code"],
    }),
    ("ggt_top10", {
        "api": "ggt_top10", "desc": "港股通十大成交股", "mode": "by_date",
        "max_rows": 0, "tier": "B", "start": "20170101",
        "subset": "*", "note": "同股可多行(市场类型不同)",
    }),
    ("hk_hold", {
        "api": "hk_hold", "desc": "沪深股通持股明细", "mode": "by_date",
        "max_rows": 0, "tier": "B", "start": "20170101", "note": "单日约960行",
        # DB-R10b：自然键取自 DB_AUDIT_PKEYS，防"同日重拉值变化→重复入库"
        "subset": ["trade_date", "code"],
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
        "codes": "stock_all", "max_rows": 5000, "tier": "B",
        # ⚠⚠ 2026-10-01 实证：本接口**不可改 by_date**，与 fund_adj 不同 !
        #   实测 trade_date='20260930' / '20250122' / '20230101' **返回完全相同的
        #   6,000 行**，float_date 固定落在 20270914~20340120 —— 即 `trade_date`
        #   参数**根本不起筛选作用**，接口只吐"未来解禁预约窗口"的首页。
        #   ⇒ 若照 fund_adj 改 by_date：①拿不到 20060403~20270913 的历史（本地库
        #     现有 4,521,447 行 / 5,535 标的，全靠 by_code 建的）②每天拉同一批
        #     6,000 行未来日程，纯属无意义消耗。③by_code+daily_full_refresh 则需
        #     5,535 请求/天，对 3,000 分漏网接口不可接受。
        #   ⇒ **保持 by_code**，真实数据只能靠 ts_code 逐只拉（实测 600000.SH
        #     单只 222 行 / 2007~2020 全历史）。新解禁按需补：
        #     `main.py gap-update --only share_float` 或 akshare 五件套。
        "note": "解禁压力预判｜⚠ by_date 不可用（trade_date 不生效，2026-10-01 实证）",
        # CC-FIX-4（2026-11-09）：单码单次硬顶 6,000 行 ⇒ by_code 改走
        #   fetch_paged_by_code（limit/offset 翻页）。此处三键只对 by_code 生效，
        #   max_rows 是 by_range 的截断检测参数，**不改**。
        #   subset 与周期通道（config/daily.py）同口径：按业务键幂等写入，
        #   防"重拉产生同键双版"。
        # CC-FIX-6 v3（2026-11-09，§368）：page_size=5000 是**分页请求**页大小
        #   （刻意留安全边际）；cap=6000 是**源端单次硬顶**，也就是截断残留的
        #   真实签名（旧实现停在整页即 6,000 行）。audit_truncation 优先用 cap
        #   判定，未配 cap 的表回落 page_size。
        "page_size": 5000, "max_pages": 20, "cap": 6000,
        # CC-FIX-5（2026-11-09，§362）：原 4 键不含 float_share，
        #   源端存在同 (ts_code,float_date,ann_date,holder_name,share_type)
        #   的多笔解禁（实证 002215.SZ 李广泽 20260610 三笔），
        #   键式判重会静默折叠 ⇒ 全列判重。
        "subset": "*",
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
        # DB-R10b：自然键取自 DB_AUDIT_PKEYS，防"同日重拉值变化→重复入库"
        # CC-FIX-8（2026-11-09，§369/§372）：同日同股可同时有 S（停牌）与 R（复牌），
        #   实证 89 组（2011–2015，均无活跃写入）；键加 suspend_type 后
        #   潜在折叠损失 0（640,675 行不变）。与 DB_AUDIT_PKEYS 逐字一致。
        "subset": ["trade_date", "ts_code", "suspend_type"],
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
        # DB-R10b：自然键取自 DB_AUDIT_PKEYS，防"同日重拉值变化→重复入库"
        "subset": ["trade_date", "ts_code"],
        "note": "IF/IC/IM 基差与升贴水; 按trade_date拉, 每日约150-200行",
    }),
    ("fut_settle", {
        "api": "fut_settle", "desc": "期货结算参数", "mode": "by_date",
        "max_rows": 0, "tier": "B", "start": "20120101",
        # DB-R10b：自然键取自 DB_AUDIT_PKEYS，防"同日重拉值变化→重复入库"
        "subset": ["ts_code", "trade_date"],
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
        # DB-R10b：自然键取自 DB_AUDIT_PKEYS，防"同日重拉值变化→重复入库"
        "subset": ["trade_date", "ts_code"],
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
        # DB-R10b：自然键取自 DB_AUDIT_PKEYS，防"同日重拉值变化→重复入库"
        "subset": ["ts_code", "trade_date"],
    }),
    ("repo_daily", {
        "api": "repo_daily", "desc": "债券回购日行情", "mode": "by_date",
        "max_rows": 0, "tier": "C", "start": "20000101", "note": "资金面利率",
        # DB-R10b：自然键取自 DB_AUDIT_PKEYS，防"同日重拉值变化→重复入库"
        "subset": ["trade_date", "ts_code"],
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
        # ⚠ 2026-11-01 DB-R15：标记为「每日全量重拉」类（daily_update 专用）。
        #   同 index_global / fut_index_daily 先例：mode=by_code 且无日期入参
        #   ⇒ 只加进 DAILY_UPDATE_GROUPS 会被静默跳过（"假纳入"，R10 判据⑤）；
        #   全量重拉落盘按 (ts_code, trade_date) 去重，天然幂等；12 个指数 = 12 请求/天。
        "daily_full_refresh": True,
        "codes": "index", "max_rows": 1000, "tier": "B",
        "subset": ["ts_code", "trade_date"],
        "note": "⚠单次上限1000行, 靠 ts_fetch 的 end_date 续拉(已验证 end_date 有效); "
                "仅拉13个关注指数 —— 全市场10862个指数按周拉会爆请求数",
    }),
    ("index_monthly", {
        "api": "index_monthly", "desc": "指数月线(验算用)", "mode": "by_code",
        # §306 G4b：本表接口不服务 CSI 批量（58 个 CSI 返回 0 行，日线齐全），
        # 码清单改为 13 个实测有月线的（见 BACKFILL_INDEX_MONTHLY_CODES）。
        "codes": "index_monthly", "max_rows": 1000, "tier": "B",
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
        "page_size": 2000, "max_rows": 0, "tier": "C",      # DB-R10 R10-5a：A→C
        "subset": ["ts_code", "hs_type"],
        "note": "⚠ 源端冻结于 2019-12-27：传任意 trade_date 返回值完全相同（实测 2026-10-29 "
                "四个日期），本表**不得用于「当前沪股通标的」判定**；库内 823 行＝源端全集，非缺口。"
                "⚠必填 hs_type(不传直接报错); SH 581行 / SZ 242行; "
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
        "start": "20240320", "date_col": "trade_date", "max_rows": 0, "tier": "C",
        # DB-R10 R10-2：源端已改版（2026-10-29 实测）
        #   ① 字段名全部**小写**（原大写 TRADE_DATE/ID 等已不存在）
        #   ② **默认只返 2 列**（trade_date, ts_code）⇒ 必须靠 fields 显式要全列
        #   ③ fields 必须全小写：传大写直接抛「指定了返回列，但是列名不匹配」
        #   ④ 单日 5,145 行 / 3 页（原"8,160 行 / 5 页"已过期）
        # ⚠ subset 用 "*"（全列判重）：原 ["ID"] 的 ID 列源端已删；若改
        #   (ts_code, trade_date) 会把同一债券的多笔报价判成重复而**静默丢数据**。
        # ⚠ 库内旧分区仍是 18 列大写 schema；schema 迁移由审查方另做（§204 §12），
        #   迁移前**不得触发本表任何写入**（否则新小写列会被旧大写 schema 对齐掉）。
        "fields": ("trade_date,ts_code,maturity,buy_yield,sell_yield,name,"
                   "coupon_rate,remain_maturity,buy_price,qt_time,bond_type,sell_price"),
        "subset": "*",
        "note": "⚠返回字段已全部**小写**(原大写 TRADE_DATE 已不存在, 故 date_col=trade_date); "
                "⚠**默认只返 2 列**(trade_date/ts_code), 必须靠 fields 显式要 12 列, "
                "且列名须全小写否则报「列名不匹配」; "
                "⚠单日约5145行需**3页**(实测 20261028: 2000+2000+1145); "
                "⚠数据起点实测为 2024-03-20(20240319返0/20240320返1985), "
                "故 start 设 20240320 而非 20160101, 可省约 2400 请求; "
                "⚠库内旧分区仍是 18 列大写 schema, 迁移由审查方另做",
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
        # ⚠ 2026-10-11（E-04，三轮架构审查）：**本条目是历史搁置记录，实体在 alt 库**
        #   —— 实际落盘在 `D:\全量数据\alt_data\hk_daily`（by_year / 29 分区），
        #   由 `alt/spec.py::P4_HK`（`alt/list_backfill.py` 轮转重建）维护，
        #   与主库 `market_data` 无关。**勿按本条目在主库建 hk_daily 表**
        #   （会造成同表两册、归属互斥被打破）。
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
        # ⚠ 2026-11-01 DB-R12b: 标记为「每日全量重拉」类（daily_update 专用）。
        #   为什么必须标记: 本表 mode=by_code，而 daily_update 只执行 by_date/by_range
        #   或带本标记的 by_code ⇒ 只加进 DAILY_UPDATE_GROUPS 会被**静默跳过**
        #   （"假纳入"，R10 判据⑤）。同 index_global 的处理。
        #   为什么全量重拉: 该接口必填 ts_code、只能一次拉该指数全历史（2006 起约 5,033 行），
        #   无"某一天"入参 ⇒ 全量重拉即正确形态；落盘按 (ts_code, trade_date) 去重，天然幂等。
        #   成本: 5 个指数 = **5 请求/天**，可忽略。
        "daily_full_refresh": True,
        "codes": "fut_index", "max_rows": 5000, "tier": "B",
        "subset": ["ts_code", "trade_date"],
        "note": "南华商品/农产品/金属/能化/工业品指数(2006起, 20261030 实测单指数 5,057 行); "
                "⚠ 行数已超 max_rows=5000 —— 全量重拉时若源端按 max_rows 截断，"
                "只会丢最早的行、已落盘分区不受影响（merge 只增不删）; "
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
    # 申万行业历史成分（2026-10-22 EE-FUND-13 新增）
    #   497 请求 × 31(L1)+131(L2)+335(L3) = 23,460 行（实测）
    #   主键 (index_code, con_code, in_date) —— 同一股票同一行业可多段进出
    #   out_date 可空（当前在册），非空（已离开该行业）
    #   由 pro.index_member(index_code=...) 拉全量（start/end 参数无效，只能整行业拉）
    ("index_member_hist", {
        "api": "index_member", "desc": "申万行业历史成分", "mode": "once",
        "max_rows": 0, "tier": "B",
        "code_param": "index_code", "codes": "sw_all_497",
        "note": "23460行; L1=7740/L2=8170/L3=7550; 含历史进出段(out_date非空34.6%); "
                "父子关系: L2上级=L1名称→L1代码, L3上级=L2名称→L2代码; "
                "派生重建index_member_all(5504行,原5913行); 409只退市股因申万2021版不再覆盖被正确排除",
    }),
    ("stk_weekly_monthly", {
        "api": "stk_weekly_monthly", "desc": "股票周月线", "mode": "by_date",
        "freq": "week", "week_anchor": "friday", "max_rows": 0, "tier": "B",
        "start": "20160101",
        "subset": "*",
        "dedup_key": ["ts_code", "trade_date"],
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
        "dedup_key": ["ts_code", "trade_date"],
        "dedup_sort": "end_date",
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
    ("moneyflow_hsgt", {
        "api": "moneyflow_hsgt", "desc": "沪深港通资金流向", "mode": "by_range",
        "start_param": "start_date", "end_param": "end_date",
        "start": "20141117", "max_rows": 300, "tier": "B",
        "subset": ["trade_date"],
        # DB-R1：源端 6 个数值列返回 str，须落盘前转 float64（见 §0.2 不可逆性）
        "coerce_float_cols": ["hgt", "sgt", "ggt_ss", "ggt_sz",
                              "north_money", "south_money"],
        "note": "不支持 trade_date 参数须用区间；单次上限 300 行；"
                "⚠ 禁 single_range（全历史约2900行 > 300）；"
                "⚠ 两条口径断层：北向 hgt/sgt/north_money 于 20240819 由净买额转成交额；"
                "南向 ggt_ss/ggt_sz/south_money 于 20231124 由净流入转存量。"
                "南向列勿作日流量用，南向流量一律取 ggt_daily",
    }),
    ("ggt_daily", {
        "api": "ggt_daily", "desc": "港股通每日成交", "mode": "by_range",
        "start_param": "start_date", "end_param": "end_date",
        "start": "20141117", "max_rows": 1000, "tier": "B",
        "subset": ["trade_date"],
        "note": "不支持 trade_date 参数须用区间；单次上限 1000 行；"
                "⚠ 禁 single_range（全历史约2900行 > 1000）；"
                "源端已是 float64，无需 coerce_float_cols；"
                "实测 2015-2026 净额连续全正、日环比中位 12~20%，"
                "是唯一可信的南向日流量源",
    }),
])
