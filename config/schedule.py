# -*- coding: utf-8 -*-
"""config.schedule — 周期调度频率表（CC-FIX-13 / R3'-b，2026-11-10，§345 P1）

回答一个问题：**不在日更组里的表，谁来定期拉、多久拉一次？**

口径（派单 §三）
----------------
1. 一张表只管「什么时候拉」，**不管拉什么** —— 取数参数一律来自
   `config.backfill_targets.BACKFILL_TARGETS`（禁止在本文件复制粘贴配置）。
2. 排班集合 = `BACKFILL_TARGETS` −（`DAILY_UPDATE_GROUPS` 全部组 − `DAILY_UPDATE_EXTRA`
   − `PERIODIC_UPDATE_TARGETS`）= **57 张**（2026-11-10 复算，与派单 §二逐字一致）。
   其中 24 张已定性为「不再进排班」（见 `EXCLUDED`），**实际在册 33 张**
   （CC-FIX-15 前为 21／36）。
3. alt 侧：`alt.spec.BY_NAME` 102 张**全部**已由 `main.py alt-update` 按
   `spec.update_every_days` 更新（无日更组、无孤立表）⇒ 本表差集为空，**不重复排班**。

频率判据（每档的落地依据）
--------------------------
    D  源端按交易日更新、且无专用日更通道：`wz_index`（万得指数日频）
    W  事件驱动或周度节奏：发行/评级/质押/回购/增减持/高管/新股/快报（公告随到随有，
       按周扫一次即可覆盖，且远低于源端限频）
    M  月频发布：宏观三表（`cn_cpi/ppi/m`、`sf_month`，源端次月 9-12 日发布）、
       `shibor_lpr`（每月 20 日）、`index_weight`/`index_monthly`（月度定期调整）
    Q  财报期/定期调整：`fina_*`、十大股东（季报）、`pledge_stat`、`stk_holdernumber`、
       `stk_rewards`、`stock_company`、三地交易日历（年度发布）、`cn_gdp`（1/4/7/10 月）

⚠ 本文件的频率是**提案**（派单 §四）：灰度（≥2026-11-16，须老大明示）前由赤潮逐行复核，
  复核通过才视为固化；执行器 `fetch/schedule_runner.py` 只读本表。
⚠ 红线（派单 §三.6）：不改任何 `subset`；本文件**不含**取数参数。

复核结论（CC-FIX-15 §四，见 `复核-SCHEDULE频率表-20261110.md`；只记结论、不改值）
------------------------------------------------------------------------------
1. `FREQ_DAYS`（D1/W7/M30/Q90）是**等间隔近似**，把「每月 10/12 日、季后 20 日」等
   具体发布日简化掉了；按 `last_fetch` 起算会随执行日漂移 ⇒ **全量启用前必须改
   日历月对齐**（列为开口，不进 CC-FIX-15/16）。
2. `--refresh` 全档成本 **84,325 次请求**（D 5,994／M 9,320／Q 35,634／W 33,377），
   最大单表 `index_weight` 9,301、by_code 系 10 张各 5,922 ⇒ 裸 `--refresh` 必须拒绝，
   分片/预算护栏由 CC-FIX-16 实现。
"""

# 频率 → 到期天数（执行器 `_is_due` 用；D 当日、W 7 天、M 30 天、Q 90 天）
FREQ_DAYS = {"D": 1, "W": 7, "M": 30, "Q": 90}

# ============================================================
# 在册 33 张（57 − 24 已定性）
# ============================================================
SCHEDULE = {
    # ── D：日频源端且无专用日更通道 ──
    "wz_index":            "D",

    # ── M：月频发布 ──
    "cn_cpi":              "M",   # 统计局次月 9-10 日
    "cn_ppi":              "M",   # 统计局次月 9 日（CC-FIX-12 §九）
    "cn_m":                "M",   # 央行次月 10-12 日
    "sf_month":            "M",   # 央行次月 10-12 日
    "shibor_lpr":          "M",   # 每月 20 日（限频 1 次/小时，月度足够）
    "index_weight":        "M",   # 指数权重月度/定期调整
    "index_monthly":       "M",   # 指数月线，源端月度更新

    # ── Q：财报期 / 年度定期 ──
    "cn_gdp":              "Q",   # 1/4/7/10 月 20 日后（CC-FIX-12 §九）
    "fina_audit":          "Q",   # 审计意见，随年报
    "fina_mainbz":         "Q",   # 主营构成，随财报
    "top10_holders":       "Q",   # 十大股东，季报
    "top10_floatholders":  "Q",   # 十大流通股东，季报
    "stk_holdernumber":    "Q",   # 股东户数，季报
    "stk_rewards":         "Q",   # 股权激励，随公告/季报
    "pledge_stat":         "Q",   # 质押统计，周/季
    "stock_company":       "Q",   # 公司概况，变更极少
    "fut_trade_cal":       "Q",   # 期货交易日历，年度发布
    "hk_tradecal":         "Q",   # 港股交易日历，年度发布
    "us_tradecal":         "Q",   # 美股交易日历，年度发布

    # ── W：事件驱动 / 周度节奏 ──
    "broker_recommend":    "W",   # 券商月度金股，月中陆续发布
    "cb_issue":            "W",   # 转债发行公告，事件驱动
    "cb_rating":           "W",   # 转债评级，不定期
    "cb_share":            "W",   # 转债股本，随转股变动
    "disclosure_date":     "W",   # 财报披露日程，每日滚动
    "express":             "W",   # 业绩快报，季度集中
    "fund_manager":        "W",   # 基金经理变更
    "new_share":           "W",   # 新股发行日程
    "pledge_detail":       "W",   # 质押明细，事件驱动
    "repurchase":          "W",   # 回购公告，每日披露
    "stk_holdertrade":     "W",   # 股东增减持，事件驱动
    "stk_managers":        "W",   # 高管变动
    "stk_seasoned":        "W",   # 次新股名单
}

# 灰度候选（CC-FIX-15 §三.1）；⚠ 本单只定义常量，**不启动灰度**（≥2026-11-16 且须老大明示）
# 硬约束：5 张全部非 once／非静态／非停更／非 PENDING_REFRESH／当前 due=True；
# 合计 54 请求/轮（8+11+11+11+13），远低于限频。原候选 `index_classify`／
# `index_member_all` 已定性为静态名录，`cn_gdp`／`cn_ppi` 是 once 整文件覆盖写，均剔除。
GRAYSCALE = ("fut_trade_cal", "hk_tradecal", "us_tradecal",
             "new_share", "index_monthly")

# ============================================================
# 57 − 33 = 24 张不进排班（逐张给依据；判据 1「零遗漏」靠这张表对账）
# ============================================================
EXCLUDED = {
    # ⛔ 启用级阻断（CC-FIX-15 §二）：源端列集少于库内，覆盖写会丢列
    "cn_pmi":         "源端列集变更（59 vs 65 列，缺 PMI012000），CC-FIX-14 §381 停单；"
                      "裁定前禁止排班/覆盖写",
    # 双通道（CC-FIX-15 §三.2）：由专用通道承载，避免同表双写链路冲突
    "fund_nav":       "由 fund-nav 独立通道承载，避免同表双写链路冲突",
    "fund_share":     "由 fund-nav 独立通道承载，避免同表双写链路冲突",
    # 已停更（STALE_BY_DESIGN，CC-FIX-6/12 定性）
    "gz_index":       "已停更（源端 2019-03-04 冻结，shibor 已覆盖）",
    "shibor_quote":   "已停更（SHELVED，维持 2016 现状）",
    "slb_len_mm":     "已停更（源端 2025-07-25 止）",
    "stk_account":    "已停更（源端 2019-02-22 止）",
    "hs_const":       "已停更（源端冻结 2019-12-27，tier C 无消费方）",
    "fut_weekly_detail": "已停更（API 实证两所 2020-04-24 止）",
    # 配置侧显式关闭（enabled=False / tier X）
    "hibor":          "未启用（market 侧不补，改引用 alt.hibor）",
    "hk_daily":       "未启用（market 侧不建，改引用 alt.hk_daily）",
    "bc_bestotcqt":   "未启用（enabled=False，tier X）",
    "stk_mins":       "未启用（enabled=False，tier X）",
    "report_rc":      "未启用（enabled=False，tier X）",
    # 静态名录（STATIC_TABLES，CC-FIX-12 定性）
    "bse_mapping":    "静态名录（旧码→新码映射）",
    "fut_basic":      "静态名录（合约名录）",
    "hk_basic":       "静态名录（港股标的名录）",
    "opt_basic":      "静态名录（期权合约名录）",
    "us_basic":       "静态名录（美股标的名录）",
    "index_classify": "静态名录（申万行业分类）",
    "index_member_all":  "静态名录（指数成分关系）",
    "index_member_hist": "静态名录（指数历史成分）",
    "cn_schedule":    "静态名录（财报披露日程快照）",
    "fund_company":   "静态名录（基金公司名录）",
}
