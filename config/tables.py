# -*- coding: utf-8 -*-
"""config.tables — 表→分区列 / 源端起点基线 / 清理目标 / 体检主键。"""

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
#
# ⚠ 2026-10-11 三轮架构审查 E-03（日期列"双轨制"收敛结论）:
#   除上表外，BACKFILL_TARGETS / DAILY_UPDATE_EXTRA 的**条目级** `date_col`
#   字段也声明日期列（实测 15 处）。两者是**不同层**、不是重复：
#     条目级 `date_col` = **该条目的取数执行参数**（backfill 落盘 / 缺口计算用）
#     本表（顶层）      = **全库权威索引**（reader / db_audit / db_migrate 共用）
#   收敛口径：**条目级为条目真源，顶层为派生索引** —— 两处必须同值；
#   该约束已由 `tools/config_check.py` 判据 ⑤b（date_col_consistency）机检
#   （实测分歧 0 处）。35 张表无顶层映射者多为静态/无日期列表，属设计内。
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
    # ---- 2026-10-28 DB-R2：沪深港通资金流向两表（by_range，单日一行）----
    "moneyflow_hsgt": "trade_date",
    "ggt_daily":      "trade_date",
    # 基金
    "fund_nav": "nav_date", "fund_adj": "trade_date",
    "fund_share": "trade_date", "fx_daily": "trade_date",
    # 财务（报告期）
    "income": "end_date", "balancesheet": "end_date",
    "cashflow": "end_date", "fina_indicator": "end_date",
    "fina_audit": "end_date", "fina_mainbz": "end_date",
    # 事件面
    "dividend": "end_date", "forecast": "ann_date",
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
    # ⚠ bc_otcqt 源端已于 2026-10 改版：日期字段由大写 TRADE_DATE 改为小写 trade_date
    #   （DB-R10 R10-2 / R10a-1）。此处为全库权威索引，必须与
    #   BACKFILL_TARGETS["bc_otcqt"]["date_col"] 同值（config_check 判据 ⑤b）。
    "bc_otcqt": "trade_date",
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
    # 19000908 → 19920113（2026-11-08 R10/F2）：原基线来自 1900.parquet 的
    # 3 行垃圾（end_date 全空，源端脏记录），已归档至
    # 备份/归档文件/marketdata-f2-cleanup-20261108/。剔后实测最小 end_date
    # = 19920113（21 行真实历史）。**只改基线值，判据方向与强度不变**。
    "stk_holdernumber": "19920113",
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

# 基线"测量列"绑定 — 2026-11-03 审查 §283（T1b）
# ------------------------------------------------------------
# 用途: 仅当某表的基线值**不是**按 partition 列测出来的，才在此登记它实际用的列。
# 未登记的表：R13 仍按 TABLE_DATE_COL（partition 列）取列，行为不变。
#
# ⚠ 禁改 SOURCE_START_BASELINE 的数值 —— 把 forecast 的 19981231 改成 19990108
#   会让 R13 变成同义反复（底线恒等于库内当前最小值，将来真截断也可能判绿）。
#   正解是让基线绑定它**被测量时用的列**：
#     forecast 的 19981231 是按 end_date 测的（全表 min(end_date)=19981231）；
#     而 partition 列已改为 ann_date，其全表最小值为 19990108
#     （1998 年度预告要到 1999-01 才公告，天然如此）⇒ 换列后若不对齐测量列，
#     R13 会假红为"库内 19990108 > 基线 19981231"。
SOURCE_START_BASELINE_COL = {
    "forecast": "end_date",
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
    # ---- 2026-10-28 DB-R2：沪深港通资金流向两表 ----
    "moneyflow_hsgt":   ["trade_date"],
    "ggt_daily":        ["trade_date"],
    "hk_hold":          ["trade_date", "code"],
    # CC-FIX-8（2026-11-09，§369/§372）：同日同股可同时有 S（停牌）与 R（复牌），
    #   实证 89 组同键多行（全在 2011–2015 分区）；键加 suspend_type 后潜在
    #   折叠损失 0（640,675 行不变）。BACKFILL_TARGETS["subset"] 同步改同一键。
    "suspend_d":        ["trade_date", "ts_code", "suspend_type"],
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
    # R9a：stk_holdernumber 补 ann_date（同报告期可多次公告）；stk_holdertrade 不改键
    #   —— 同日多笔属合法多行（不同 holder_name/变动量），键已含 holder_name。
    "stk_holdernumber": ["ts_code", "end_date", "ann_date"],
    "stk_holdertrade":  ["ts_code", "ann_date", "holder_name"],
    "pledge_stat":      ["ts_code", "end_date"],
    # R9a：share_float 补 ann_date（同 float_date+holder_name 可多次公告）
    # CC-FIX-5（2026-11-09，§362）：原 4 键会把「同人同日同类型多笔解禁」
    #   误报为主键重复；加 share_type（5 键）后实测仍有 127 组合法多行
    #   （2026-11-09 全量复算：仅 float_share 不同；§368 Q3）
    #   口径＝duplicated() 默认 keep='first'（即审计输出口径）：
    #   4 键 211 行/206 组，5 键 131 行/127 组（CC-FIX-7，§370 四.2 更正）
    #   ⇒ 本表无覆盖源端的业务键，改 None（跳过键式判重，由全列判重兜底）。
    "share_float":      None,
    "repurchase":       ["ts_code", "ann_date", "proc"],
    "namechange":       ["ts_code", "start_date"],
    "fina_mainbz":      ["ts_code", "end_date", "bz_item"],
    "fina_audit":       ["ts_code", "end_date"],
    "stock_company":    ["ts_code"],
    "cb_issue":         ["ts_code"],
    # R9a：cb_share 补 end_date（同公告日可含多个报告期）
    "cb_share":         ["ts_code", "publish_date", "end_date"],
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
    # bc_otcqt：源端已无 ID 列（R10-2），改全列判重，与 BACKFILL_TARGETS.subset="*" 对齐
    "bc_otcqt":         None,
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
    # R9a：**不改键** —— 同日多笔质押属合法多行（不同 holder_name/质押量）
    "pledge_detail":    ["ts_code", "ann_date", "holder_name", "pledge_amount"],
    "weekly":           ["ts_code", "trade_date"],
    "monthly":          ["ts_code", "trade_date"],
    "index_weekly":     ["ts_code", "trade_date"],
    "index_monthly":    ["ts_code", "trade_date"],
    "index_global":     ["ts_code", "trade_date"],
    "index_member_all": ["ts_code"],
    "index_member_hist": ["index_code", "con_code", "in_date"],  # 2026-10-22 EE-FUND-13 新增
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
    # R9a（2026-11-08）：财报三表补 update_flag —— 同一 (ts_code,end_date,report_type)
    #   存在 update_flag=1/2 的多版本行（更正前/更正后），属业务多行而非重复。
    #   只改审计判重口径，写入端不动。
    "income":           ["ts_code", "end_date", "report_type", "update_flag"],
    "balancesheet":     ["ts_code", "end_date", "report_type", "update_flag"],
    "cashflow":         ["ts_code", "end_date", "report_type", "update_flag"],
    "fina_indicator":   ["ts_code", "end_date"],
    # ---- 2026-09-19 接口审查补充 ----
    # 判重口径遵循项目"二分"原则（真重复 vs 业务多行），全部用复合键或全列：
    "fut_index_daily":     ["ts_code", "trade_date"],
    "etf_limit":           ["ts_code", "trade_date"],
    "top10_holders":       ["ts_code", "end_date", "holder_name"],
    "top10_floatholders":  ["ts_code", "end_date", "holder_name"],
    "index_classify":      ["index_code"],
    # R9a：补 freq —— 同一 trade_date 下周线/月线两套行并存属业务多行
    "stk_weekly_monthly":  ["ts_code", "trade_date", "freq"],
    "stk_week_month_adj":  ["ts_code", "trade_date", "freq"],
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

# ============================================================
# 7d-1b. 键窄但**已裁定无需改**的表（CC-FIX-8 v2，2026-11-09，§369/§372）
# ============================================================
# 判据（三条缺一不可）：
#   1) 同键多出来的行是「同一事实的上游修订／补值」，不是两次业务事实；
#   2) 逐组比较「后一版 vs 前一版」非空列计数，**后版更少 = 0 组**
#      （否则 keep="last" 会丢值，禁止 ACK）；
#   3) 整行完全相同 = 0（存在整行相同 ⇒ 是写入重复，不是修订，必须 ⚠）。
# 另设 baseline_dup + alarm_ratio 护栏：同键多行超基线 1.5× 仍报 ⚠，
#   防 ACK 变成永久哑巴。新增项必须带 decided + reason + evidence + 两护栏。
KEY_NARROW_ACK = {
    "index_global": {
        "decided": "2026-11-09",
        "reason": "行情修订／补值：同一指数同日被重拉后部分字段更新，keep='last' 取最新快照",
        "evidence": "131 组同键多行（全在 2026 分区），整行完全相同 0；"
                    "差异 vol 97 / low 19 / swing 18 / close 15 / change 15 / high 14 / pct_chg 12 / open 1；"
                    "非空方向：后版更多 71 / 相同 60 / 更少 0",
        "baseline_dup": 131,
        "alarm_ratio": 1.5,
    },
}

# ============================================================
# 7d-2. 已裁定「不会再更新」的表 — freshness 单列一档（CC-FIX-6，2026-11-09，§362）
# ============================================================
# 为什么（§366）：6 张红表里有 4 张永远不会好转（源端停更 / 已裁搁置），
#   继续报红＝红灯疲劳。此处单列 `已停更` 档：**不计入红黄总数**，
#   `--strict` 也不因它非零。
# ⚠ 新增项必须带 decided（裁定日期）+ reason（依据），**不许把红表改配置改绿**。
STALE_BY_DESIGN = {
    "gz_index":     {"since": "2019-03-04", "decided": "2026-09-19",
                     "reason": "源端停更（民间利率，已被 shibor 覆盖）"},
    "shibor_quote": {"since": "2016-12-30", "decided": "2026-09-23",
                     "reason": "老大决策 SHELVED，维持 2016 现状，不补不删"},
    "hibor":        {"since": "2016-12-30", "decided": "2026-11-09",
                     "reason": "market 侧未补（限频 1 次/小时，补 11 年≈11.2h）；"
                               "港股资金面改引用 alt.hibor（实测 2026-11-06 正常）"},
    "stk_account":  {"since": "2019-02-22", "decided": "2026-09-19",
                     "reason": "源端停更，192 行仅存档"},
    "slb_len_mm":   {"since": "2025-07-25", "decided": "2026-11-09",
                     "reason": "源端停更（API 实证单次返 5,000 行，最新 20250725）"},
    # ---- CC-FIX-12（2026-11-10，§378）：24 张「未知」定性后的停更/改引用登记 ----
    "hs_const":     {"since": "2019-12-27", "decided": "2026-11-10",
                     "reason": "源端冻结（§203.2 实证：库内零缺口、无消费方、tier C）；"
                               "§369 裁定「只登记不修复」"},
    "fut_weekly_detail": {"since": "2020-04-24", "decided": "2026-11-10",
                          "reason": "源端停更（API 实证：SHFE／DCE 各拉 4,000 行，"
                                    "week_date 最新均 20200424，与库内止点逐字吻合）"},
    "hk_daily":     {"since": "2026-11-10", "decided": "2026-11-10",
                     "reason": "market 侧未建（enabled=False、tier X、断点无记录）；"
                               "港股日线改引用 alt.hk_daily（实测 2026-11-06 正常，"
                               "856.7 万行／135 MB），照 hibor 先例"},
}

# 静态名录表：无「内容日期」语义，只判「文件存在 + 行数 ≥ 下限」。
STATIC_TABLES = {
    "fund_company": {"min_rows": 200,
                     "reason": "207 行公司名录；date_col 探到常量 end_date=20091229，"
                               "真实 setup_date 最大 20250116 ⇒ 内容日期判据不适用"},
    # ---- CC-FIX-12（2026-11-10，§378）：一次性名录／关系快照（无「内容日期」语义）----
    # 共同依据：均为 mode=once / by_param / paged 的一次性建库，BACKFILL_TARGETS 无周期
    #   更新通道，断点只有 {'completed': ['once'...]}（从无 last_fetch）；库内日期列是
    #   **业务生效日**（list_date/in_date/out_date/month），不是数据新鲜度。
    #   ⇒ 与 fund_company 同类：判「目录存在且有 parquet」，行数下限本版不校验。
    "hk_basic":          {"min_rows": 2000,
                          "reason": "港股标的名录（2,785 行；list_date 最新 20260909 是上市日）"},
    "fut_basic":         {"min_rows": 5000, "reason": "期货合约名录（11,275 行）"},
    "opt_basic":         {"min_rows": 10000, "reason": "期权合约名录（30,730 行）"},
    "us_basic":          {"min_rows": 10000, "reason": "美股标的名录（24,255 行）"},
    "bse_mapping":       {"min_rows": 100, "reason": "北交所旧码→新码映射（248 行）"},
    "index_classify":    {"min_rows": 500, "reason": "申万行业分类（856 行，无日期列）"},
    "index_member_hist": {"min_rows": 10000,
                          "reason": "指数历史成分（23,460 行；in_date/out_date 为纳入/剔除生效日）"},
    "index_member_all":  {"min_rows": 2000,
                          "reason": "指数成分全量关系（5,211 行；in_date 为生效日）"},
    "cn_schedule":       {"min_rows": 100,
                          "reason": "财报披露日程（176 行；month 覆盖 202601-202612 未来日期）"},
}

# ============================================================
# 7d-3. 补拉计划登记 — freshness 单列「待补拉」档（CC-FIX-14，2026-11-10，§380）
# ============================================================
# 语义：**判定为「黄」且命中本登记**的表 → freshness 显示「待补拉」；
#   补拉完成后 ds 更新、判定回「正常」，登记条目保留作注记，**不删除**。
# ⚠ 只改写「黄」：未命中的黄表照常显示黄（禁止借登记吞警）；
#   「正常」「红」「已停更」「未知」一律不受本登记影响。
# 依据：CC-FIX-12 回执 §九/§十（赤潮 §379 批准），老大 §380 裁定「都做」。
PENDING_REFRESH = {
    "cn_ppi":    {"plan": "once 补拉至源端止点", "decided": "2026-11-10"},
    "cn_m":      {"plan": "once 补拉至源端止点", "decided": "2026-11-10"},
    # CC-FIX-15 §二（老大选 C 挂账不动）：保留登记，仅改 plan 注记；
    # ⛔ 不得为「待补拉归零」删登记或改判定词（freshness 仍显示「待补拉 1」）。
    "cn_pmi":    {"plan": "挂账不动：源端列集变更（59 vs 65 列），待裁定 A/B/C（§381）",
                  "decided": "2026-11-10"},
    "cn_gdp":    {"plan": "once 补拉至源端止点", "decided": "2026-11-10"},
    "sf_month":  {"plan": "once 补拉至源端止点", "decided": "2026-11-10"},
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
    # CC-FIX-10（2026-11-10，§373）：无主键表，整行相同 14 行（2024:2／2025:12／2026:0）；
    #   源端实测同一年整行返回两次（20240522/2305248.BC/09:50:20 逐列相同）⇒ 上游重复。
    #   不删、不压行：计数仍全量可见，只是判定从 ⚠ 降为 ℹ。
    "bc_otcqt",
}
