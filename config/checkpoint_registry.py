# -*- coding: utf-8 -*-
"""config/checkpoint_registry.py — 断点载体登记单一真源（P3-1）
================================================================
本文件是**纯声明**（无 IO、无运行时探测）。断点工具的读写一律经
`tools/db/checkpoint_registry.py` 门面（P3-2），不直接读本文件。

为什么要有它
------------
2026-11-10 P3-0 实测：数据根下共 21 个断点类文件、跨 2 个库、8 种 key_space，
此前 `tools/db/audit.py::CHECKPOINT_FILES` 只认 3 个（6/21），
"audit 全绿" 并不覆盖其余 18 个。本文件把「谁写、写到哪、键是什么空间、
完成度字段叫什么、权威等级」一次性写死，供门面与审计共同消费。

记录模型（v2.1 §5.1 ＋ 条件四）
------------------------------
- library           market_data | alt_data（derived_data / wind_data 实测无载体，不建域）
- owner             唯一写入方模块（相对代码根）
- file              物理路径（相对库根）
- key_space         date8 | range8 | 8 | code | __whole__ | page | param | month6 | period | mixed | last_run | None
- completed_field   completed | done | done_dates | last_run | None
- timestamp_field   last_fetch | updated | last_run | updated_at | None
- authority         PRIMARY | SUPPLEMENTARY | LEGACY | DEAD | NOT_A_CHECKPOINT（5 值，见下）
- tables            该域负责的表集合（frozenset；空集＝只登记文件不声明表）
- note              口径来源（必填）

五条唯一性规则（P3-1 §2.3）
--------------------------
1. 同一 (library, table, key_space) 恰 1 个 PRIMARY。`domains_for()` 可复核。
2. 不同 key_space 同表 = 两个维度，各自独立（daily 在补数账是 date8、在日更账是 code）。
3. 不同 library 同表 = 两个域（hibor 主库 range8／alt param）；消费侧 P3-0 未定 ⇒ UNDECIDED。
4. LEGACY / DEAD 只读不写。
5. mixed 只登记不动值（wz_index / stk_seasoned 的 'once' 死键归 P3-5）。

第 6 条（`.lock` 处置，2026-11-11 老大裁定，18 号 §399-N1）
--------------------------------------------------------
6. `*.lock` 文件不登记。
   理由：锁文件不回答「某键是否已完成」，也不承载任何完成度语义；
   把它登记为 NOT_A_CHECKPOINT 会让「反向扫描 0 未登记」这条判据
   依赖一份会随进程增删的文件清单 —— 锁文件生命周期是秒级，登记即过时。
   ⇒ 扫描口径改为：**排除 `*.lock` 后再比对**，并在扫描脚本里显式写出这条排除。

authority 判定
--------------
PRIMARY            完成判定的权威载体
SUPPLEMENTARY      同空间但非权威，或旁证（水位/心跳/重复账）
LEGACY             旧格式/备份/无写入方，只读
DEAD               静止且确定不再写入
NOT_A_CHECKPOINT   不回答「某键是否已完成」——判定标准只此一条（条件四）

P3-0 实测更正（优先于派单 §2.1 的估算）
--------------------------------------
- `_meta/*_progress.json`：派单记 8，**实测 10**。
- `_meta/rotate_state.json`：派单记 11 表，**实测 10 表**。
- `.periodic_state.json`：派单记 8，**实测 3 表**（dividend / namechange / share_float）。
- `.backfill_checkpoint_codes.json`：顶层混入 1 个非法键 `done`（list），非表名。
- `_meta/update_state.json`：覆盖 96/98（缺 us_daily_qfq / us_universe）。
- `_meta/oversize_hk_val_pct_checkpoint.json`：全仓无写入方，且 hk_val_pct 不在 alt 98 表内。
- `derived_data` / `wind_data`：`*checkpoint*`、`*_state.json`、`*_progress.json` 0 命中（C5 结案）。
"""
from __future__ import annotations

from dataclasses import dataclass

LIBRARIES = ("market_data", "alt_data")

AUTHORITIES = (
    "PRIMARY", "SUPPLEMENTARY", "LEGACY", "DEAD", "NOT_A_CHECKPOINT",
)

KEY_SPACES = (
    "date8", "range8", "8", "code", "__whole__", "page", "param",
    "month6", "period", "mixed", "last_run",
)


@dataclass(frozen=True)
class CheckpointDomain:
    """一个断点域的静态声明（不含任何读写方法）。"""

    id: str
    library: str
    owner: str
    file: str
    key_space: str | None
    completed_field: str | None
    timestamp_field: str | None
    authority: str
    tables: frozenset[str]
    note: str


def _tables(*parts: str) -> frozenset[str]:
    """把逗号分隔的表名（可多段）转成 frozenset（纯函数）。"""
    return frozenset(x.strip() for p in parts for x in p.split(",") if x.strip())


DOMAINS: tuple[CheckpointDomain, ...] = (
    CheckpointDomain(
        id="market.backfill.by_date",
        library="market_data",
        owner="fetch/backfill.py",
        file=".backfill_checkpoint.json",
        key_space="date8",
        completed_field="completed",
        timestamp_field="last_fetch",
        authority="PRIMARY",
        tables=_tables(
            "adj_factor", "bc_otcqt", "block_trade", "cb_daily", "daily", "daily_basic",
            "daily_info", "eco_cal", "etf_limit", "forecast", "fund_adj", "fund_div",
            "fut_daily", "fut_holding", "fut_mapping", "fut_settle", "fut_weekly_monthly",
            "fut_wsr", "fx_daily", "ggt_top10", "hk_hold", "hsgt_top10", "index_daily",
            "index_dailybasic", "margin", "margin_detail", "margin_secs", "moneyflow",
            "monthly", "opt_daily", "repo_daily", "sge_daily", "stk_limit",
            "stk_week_month_adj", "stk_weekly_monthly", "suspend_d", "sz_daily_info",
            "top_inst", "top_list", "weekly",
        ),
        note="补数总账 by_date 域；键=YYYYMMDD。⚠ monthly/weekly 另含日更日期键（42/23 条，P3-0 实证），同一 key_space 双语义，待 P3-4 拆域。",
    ),
    CheckpointDomain(
        id="market.backfill.by_code",
        library="market_data",
        owner="fetch/backfill.py",
        file=".backfill_checkpoint.json",
        key_space="code",
        completed_field="completed",
        timestamp_field="last_fetch",
        authority="PRIMARY",
        tables=_tables(
            "cb_issue", "cb_rating", "cb_share", "dividend", "express", "fina_audit",
            "fina_mainbz", "fund_share", "fut_index_daily", "index_global", "index_monthly",
            "index_weekly", "namechange", "pledge_detail", "pledge_stat", "repurchase",
            "share_float", "stk_holdernumber", "stk_holdertrade", "stk_managers",
            "stk_rewards", "stock_company",
        ),
        note="补数总账 by_code 域；键=ts_code。fund_daily 单列见 market.backfill.by_code.fund_daily。",
    ),
    CheckpointDomain(
        id="market.backfill.by_range",
        library="market_data",
        owner="fetch/backfill.py",
        file=".backfill_checkpoint.json",
        key_space="range8",
        completed_field="completed",
        timestamp_field="last_fetch",
        authority="PRIMARY",
        tables=_tables(
            "ggt_daily", "hibor", "hk_tradecal", "moneyflow_hsgt", "new_share", "shibor",
            "shibor_lpr", "shibor_quote", "us_tbr", "us_tltr", "us_tradecal", "us_trltr",
            "us_trycr", "us_tycr",
        ),
        note="键=YYYYMMDD|YYYYMMDD；右端日随 today 漂移，旧右端键为历史区间记录（P3-0 C4 实证），⛔ 不按 extra 计数删除。",
    ),
    CheckpointDomain(
        id="market.backfill.once",
        library="market_data",
        owner="fetch/backfill.py",
        file=".backfill_checkpoint.json",
        key_space="__whole__",
        completed_field="completed",
        timestamp_field="last_fetch",
        authority="PRIMARY",
        tables=_tables(
            "bse_mapping", "cn_cpi", "cn_gdp", "cn_m", "cn_pmi", "cn_ppi", "cn_schedule",
            "fund_company", "gz_index", "hk_basic", "index_classify", "index_member_hist",
            "sf_month", "stk_account",
        ),
        note="落盘 completed == ['once']（C3）；build_tasks 返 [None] 是构建期中间值，⛔ 不改 build_tasks。",
    ),
    CheckpointDomain(
        id="market.backfill.paged",
        library="market_data",
        owner="fetch/backfill.py",
        file=".backfill_checkpoint.json",
        key_space="page",
        completed_field="completed",
        timestamp_field="last_fetch",
        authority="PRIMARY",
        tables=_tables(
            "fund_manager", "fut_weekly_detail", "index_member_all", "slb_len_mm",
            "stk_seasoned", "us_basic", "wz_index",
        ),
        note="键=页号；⚠ wz_index/stk_seasoned 各混入 1 条 'once' 死键（归 P3-5）。",
    ),
    CheckpointDomain(
        id="market.backfill.by_param",
        library="market_data",
        owner="fetch/backfill.py",
        file=".backfill_checkpoint.json",
        key_space="param",
        completed_field="completed",
        timestamp_field="last_fetch",
        authority="PRIMARY",
        tables=_tables(
            "fut_basic", "fut_trade_cal", "hs_const", "opt_basic",
        ),
        note="键=枚举参数；⚠ fut_trade_cal 另含 11 条年度区间键（旧 by_range 模式残留）。",
    ),
    CheckpointDomain(
        id="market.backfill.by_period",
        library="market_data",
        owner="fetch/backfill.py",
        file=".backfill_checkpoint.json",
        key_space="period",
        completed_field="completed",
        timestamp_field="last_fetch",
        authority="PRIMARY",
        tables=_tables(
            "disclosure_date", "top10_floatholders", "top10_holders",
        ),
        note="键=报告期 YYYYMMDD。",
    ),
    CheckpointDomain(
        id="market.backfill.by_month",
        library="market_data",
        owner="fetch/backfill.py",
        file=".backfill_checkpoint.json",
        key_space="month6",
        completed_field="completed",
        timestamp_field="last_fetch",
        authority="PRIMARY",
        tables=_tables(
            "broker_recommend", "index_weight",
        ),
        note="键=code|YYYYMMDD（月末）；⚠ index_weight 另含 747 条非月末/旧代码键（G6）。",
    ),
    CheckpointDomain(
        id="market.backfill.by_code.fund_daily",
        library="market_data",
        owner="fetch/backfill.py",
        file=".backfill_checkpoint.json",
        key_space="code",
        completed_field="completed",
        timestamp_field="last_fetch",
        authority="SUPPLEMENTARY",
        tables=_tables(
            "fund_daily",
        ),
        note="fund_daily 的 by_code 条目实测 35 条全为日期键、0 条代码键 ⇒ 完成度以 market.daily_ckpt.code 为准，本条降为 SUPPLEMENTARY。",
    ),
    CheckpointDomain(
        id="market.codes_backfill.primary",
        library="market_data",
        owner="fetch/backfill.py",
        file=".backfill_checkpoint_codes.json",
        key_space="code",
        completed_field="completed",
        timestamp_field="last_fetch",
        authority="PRIMARY",
        tables=_tables(
            "fund_adj", "fund_nav",
        ),
        note="`--codes` 显式清单补数专用；只对无竞争 code 载体的两表为 PRIMARY。⚠ 文件顶层混入非法键 'done'（list），非表名。",
    ),
    CheckpointDomain(
        id="market.codes_backfill.aux",
        library="market_data",
        owner="fetch/backfill.py",
        file=".backfill_checkpoint_codes.json",
        key_space="code",
        completed_field="completed",
        timestamp_field="last_fetch",
        authority="SUPPLEMENTARY",
        tables=_tables(
            "cb_issue", "cb_rating", "fund_daily", "fund_share", "share_float",
        ),
        note="与主账/日更账同 (table, code) 空间，完成度以各表 PRIMARY 为准 ⇒ SUPPLEMENTARY。",
    ),
    CheckpointDomain(
        id="market.daily_ckpt.code",
        library="market_data",
        owner="fetch/base.py",
        file=".checkpoint.json",
        key_space="code",
        completed_field="completed",
        timestamp_field=None,
        authority="PRIMARY",
        tables=_tables(
            "balancesheet", "cashflow", "daily", "daily_basic", "fina_indicator",
            "fund_daily", "income",
        ),
        note="日更管线 Checkpoint 按代码记完成；fund_daily 的 15,000 码是全库最全 code 载体。",
    ),
    CheckpointDomain(
        id="market.daily_ckpt.done_dates",
        library="market_data",
        owner="fetch/base.py",
        file=".checkpoint.json",
        key_space="date8",
        completed_field="done_dates",
        timestamp_field=None,
        authority="LEGACY",
        tables=_tables(
            "adj_factor", "stk_limit",
        ),
        note="旧命名 done_dates（最后 20260917）；现行由主账 by_date 写至 20261110 ⇒ 只读 LEGACY。",
    ),
    CheckpointDomain(
        id="market.cb_ckpt",
        library="market_data",
        owner="fetch/cb_download.py",
        file=".cb_checkpoint.json",
        key_space="code",
        completed_field="completed",
        timestamp_field=None,
        authority="LEGACY",
        tables=_tables(
            "cb_daily",
        ),
        note=("可转债独立断点（cb_download.py 的 Checkpoint 子类）；键=债券代码。"
              "★2026-11-11 P3-1 收口：PRIMARY→LEGACY（18 号 §399-N2）。"
              "理由：mtime 2026-08-16 静止 87 天；cb_download 只由 cli/fetch_cmds.py:67 "
              "手工触发、不进 SCHEDULE；非 DEAD 因手工触发仍会写。"
              "⚠ 双载体：cb_daily 另有 .backfill_checkpoint.json 的 35 条日期键（daily_update 写），"
              "与 fund_daily 同构；消费侧按 code 维度应继续读本文件，但不得视为每日刷新源。"),
    ),
    CheckpointDomain(
        id="market.periodic_state",
        library="market_data",
        owner="fetch/periodic_update.py",
        file=".periodic_state.json",
        key_space="last_run",
        completed_field="last_run",
        timestamp_field="last_run",
        authority="PRIMARY",
        tables=_tables(
            "dividend", "namechange", "share_float",
        ),
        note="无键集合，仅 last_run 水位（7 天周期）；completed_field=last_run 表示最后运行日。",
    ),
    CheckpointDomain(
        id="market.backfill.bak_20260925",
        library="market_data",
        owner="(none)",
        file=".backfill_checkpoint.json.bak_20260925_220428",
        key_space="mixed",
        completed_field="completed",
        timestamp_field="last_fetch",
        authority="LEGACY",
        tables=frozenset(),
        note="人工备份副本（非写入方）；只登记文件，不声明表集合。",
    ),
    CheckpointDomain(
        id="market.backfill.bak_20261001",
        library="market_data",
        owner="(none)",
        file=".backfill_checkpoint.json.bak_20261001_165026",
        key_space="mixed",
        completed_field="completed",
        timestamp_field="last_fetch",
        authority="LEGACY",
        tables=frozenset(),
        note="人工备份副本（非写入方）；只登记文件，不声明表集合。",
    ),
    CheckpointDomain(
        id="alt.ckpt.__whole__",
        library="alt_data",
        owner="alt/backfill.py",
        file=".alt_checkpoint.json",
        key_space="__whole__",
        completed_field="done",
        timestamp_field="updated",
        authority="PRIMARY",
        tables=_tables(
            "all_pb_lg", "bond_index_composite", "bond_index_new_composite",
            "bond_yield_us", "buffett_lg", "cftc_fx_merchant", "cftc_fx_nc",
            "cftc_goods_merchant", "cftc_goods_nc", "congestion_lg", "ebs_lg",
            "esg_hz_rating", "etf_scale_sse", "fund_linghuo_position_lg",
            "fund_stock_position_lg", "macro_au_cpi", "macro_au_unemp",
            "macro_bank_australia", "macro_bank_euro", "macro_bank_japan",
            "macro_bank_swiss", "macro_bank_uk", "macro_bank_usa", "macro_ca_cpi",
            "macro_ca_gdp", "macro_ca_unemp", "macro_cn_cpi", "macro_de_cpi",
            "macro_de_gdp", "macro_euro_cpi", "macro_jp_cpi", "macro_jp_unemp",
            "macro_uk_cpi", "macro_uk_gdp", "macro_uk_unemp", "macro_usa_adp",
            "macro_usa_core_cpi", "macro_usa_cpi", "macro_usa_cpi_yoy_em", "macro_usa_gdp",
            "macro_usa_non_farm", "macro_usa_ppi", "macro_usa_unemp", "market_pe_kcb",
            "qvix_1000index", "qvix_100etf", "qvix_300etf", "qvix_300index", "qvix_500etf",
            "qvix_50etf", "qvix_50index", "qvix_cyb", "qvix_kcb", "ttm_lyr_lg",
            "us_universe",
        ),
        note="键='__all__' 单标记（98 表中 55 张）。",
    ),
    CheckpointDomain(
        id="alt.ckpt.date8",
        library="alt_data",
        owner="alt/backfill.py",
        file=".alt_checkpoint.json",
        key_space="date8",
        completed_field="done",
        timestamp_field="updated",
        authority="PRIMARY",
        tables=_tables(
            "cb_redeem", "etf_scale_szse", "market_activity", "option_sse_stats",
            "qdii_spot_asia", "qdii_spot_euro", "qvix_1000index_min", "qvix_100etf_min",
            "qvix_300etf_min", "qvix_300index_min", "qvix_500etf_min", "qvix_50etf_min",
            "qvix_50index_min", "qvix_cyb_min", "qvix_kcb_min", "zt_pool", "zt_pool_dtgc",
            "zt_pool_previous", "zt_pool_strong", "zt_pool_sub_new", "zt_pool_zbgc",
        ),
        note="键='date:YYYYMMDD'。",
    ),
    CheckpointDomain(
        id="alt.ckpt.param",
        library="alt_data",
        owner="alt/backfill.py",
        file=".alt_checkpoint.json",
        key_space="param",
        completed_field="done",
        timestamp_field="updated",
        authority="PRIMARY",
        tables=_tables(
            "below_net_asset", "bond_index_treasury", "fund_aum_em", "gxl_lg", "hibor",
            "high_low_stat", "market_pb_lg", "market_pe_lg", "stock_gdhs",
        ),
        note="键='enum:枚举值'；hibor 消费侧 UNDECIDED（P3-0 开口 1）。",
    ),
    CheckpointDomain(
        id="alt.ckpt.range8",
        library="alt_data",
        owner="alt/backfill.py",
        file=".alt_checkpoint.json",
        key_space="range8",
        completed_field="done",
        timestamp_field="updated",
        authority="PRIMARY",
        tables=_tables(
            "bond_yield_cn", "currency_boc_sina",
        ),
        note="键='range:YYYYMMDD'（区间起点）。",
    ),
    CheckpointDomain(
        id="alt.ckpt.code",
        library="alt_data",
        owner="alt/backfill.py",
        file=".alt_checkpoint.json",
        key_space="code",
        completed_field="done",
        timestamp_field="updated",
        authority="PRIMARY",
        tables=_tables(
            "commodity_foreign", "hk_daily", "hk_fina_snap", "hk_qfq_factor",
            "us_daily_qfq", "us_daily_raw", "us_fina_ind", "us_qfq_factor",
            "us_report_balance", "us_report_cashflow", "us_report_income",
        ),
        note="键=代码；hk_* 为 5 位港股码，us_* 为美股 ticker（含 1 个带空格 'HUDA U'）。",
    ),
    CheckpointDomain(
        id="alt.rotate_state",
        library="alt_data",
        owner="alt/list_backfill.py",
        file="_meta/rotate_state.json",
        key_space="code",
        completed_field=None,
        timestamp_field=None,
        authority="SUPPLEMENTARY",
        tables=_tables(
            "commodity_foreign", "hk_daily", "hk_fina_snap", "hk_qfq_factor",
            "us_daily_raw", "us_fina_ind", "us_qfq_factor", "us_report_balance",
            "us_report_cashflow", "us_report_income",
        ),
        note="轮转水位 {表:{代码:日期}}；与 alt.ckpt.code 同空间 ⇒ SUPPLEMENTARY。",
    ),
    CheckpointDomain(
        id="alt.update_state",
        library="alt_data",
        owner="alt/update.py",
        file="_meta/update_state.json",
        key_space="date8",
        completed_field=None,
        timestamp_field="last_update",
        authority="SUPPLEMENTARY",
        tables=_tables(
            "all_pb_lg", "below_net_asset", "bond_index_composite",
            "bond_index_new_composite", "bond_index_treasury", "bond_yield_cn",
            "bond_yield_us", "buffett_lg", "cb_redeem", "cftc_fx_merchant", "cftc_fx_nc",
            "cftc_goods_merchant", "cftc_goods_nc", "commodity_foreign", "congestion_lg",
            "currency_boc_sina", "ebs_lg", "esg_hz_rating", "etf_scale_sse",
            "etf_scale_szse", "fund_aum_em", "fund_linghuo_position_lg",
            "fund_stock_position_lg", "gxl_lg", "hibor", "high_low_stat", "hk_daily",
            "hk_fina_snap", "hk_qfq_factor", "macro_au_cpi", "macro_au_unemp",
            "macro_bank_australia", "macro_bank_euro", "macro_bank_japan",
            "macro_bank_swiss", "macro_bank_uk", "macro_bank_usa", "macro_ca_cpi",
            "macro_ca_gdp", "macro_ca_unemp", "macro_cn_cpi", "macro_de_cpi",
            "macro_de_gdp", "macro_euro_cpi", "macro_jp_cpi", "macro_jp_unemp",
            "macro_uk_cpi", "macro_uk_gdp", "macro_uk_unemp", "macro_usa_adp",
            "macro_usa_core_cpi", "macro_usa_cpi", "macro_usa_cpi_yoy_em", "macro_usa_gdp",
            "macro_usa_non_farm", "macro_usa_ppi", "macro_usa_unemp", "market_activity",
            "market_pb_lg", "market_pe_kcb", "market_pe_lg", "option_sse_stats",
            "qdii_spot_asia", "qdii_spot_euro", "qvix_1000index", "qvix_1000index_min",
            "qvix_100etf", "qvix_100etf_min", "qvix_300etf", "qvix_300etf_min",
            "qvix_300index", "qvix_300index_min", "qvix_500etf", "qvix_500etf_min",
            "qvix_50etf", "qvix_50etf_min", "qvix_50index", "qvix_50index_min", "qvix_cyb",
            "qvix_cyb_min", "qvix_kcb", "qvix_kcb_min", "stock_gdhs", "ttm_lyr_lg",
            "us_daily_raw", "us_fina_ind", "us_qfq_factor", "us_report_balance",
            "us_report_cashflow", "us_report_income", "zt_pool", "zt_pool_dtgc",
            "zt_pool_previous", "zt_pool_strong", "zt_pool_sub_new", "zt_pool_zbgc",
        ),
        note="最后更新目标日 last_target_date；覆盖 96/98（缺 us_daily_qfq／us_universe）。",
    ),
    CheckpointDomain(
        id="alt.progress.commodity_foreign",
        library="alt_data",
        owner="alt/list_backfill.py",
        file="_meta/commodity_foreign_progress.json",
        key_space=None,
        completed_field=None,
        timestamp_field="updated_at",
        authority="SUPPLEMENTARY",
        tables=_tables(
            "commodity_foreign",
        ),
        note="心跳/进度计数（done/total/pct），非键集合；派单记 8，实测 10。",
    ),
    CheckpointDomain(
        id="alt.progress.hk_daily",
        library="alt_data",
        owner="alt/list_backfill.py",
        file="_meta/hk_daily_progress.json",
        key_space=None,
        completed_field=None,
        timestamp_field="updated_at",
        authority="SUPPLEMENTARY",
        tables=_tables(
            "hk_daily",
        ),
        note="心跳/进度计数（done/total/pct），非键集合；派单记 8，实测 10。",
    ),
    CheckpointDomain(
        id="alt.progress.hk_fina_snap",
        library="alt_data",
        owner="alt/list_backfill.py",
        file="_meta/hk_fina_snap_progress.json",
        key_space=None,
        completed_field=None,
        timestamp_field="updated_at",
        authority="SUPPLEMENTARY",
        tables=_tables(
            "hk_fina_snap",
        ),
        note="心跳/进度计数（done/total/pct），非键集合；派单记 8，实测 10。",
    ),
    CheckpointDomain(
        id="alt.progress.hk_qfq_factor",
        library="alt_data",
        owner="alt/list_backfill.py",
        file="_meta/hk_qfq_factor_progress.json",
        key_space=None,
        completed_field=None,
        timestamp_field="updated_at",
        authority="SUPPLEMENTARY",
        tables=_tables(
            "hk_qfq_factor",
        ),
        note="心跳/进度计数（done/total/pct），非键集合；派单记 8，实测 10。",
    ),
    CheckpointDomain(
        id="alt.progress.us_daily_raw",
        library="alt_data",
        owner="alt/list_backfill.py",
        file="_meta/us_daily_raw_progress.json",
        key_space=None,
        completed_field=None,
        timestamp_field="updated_at",
        authority="SUPPLEMENTARY",
        tables=_tables(
            "us_daily_raw",
        ),
        note="心跳/进度计数（done/total/pct），非键集合；派单记 8，实测 10。",
    ),
    CheckpointDomain(
        id="alt.progress.us_fina_ind",
        library="alt_data",
        owner="alt/list_backfill.py",
        file="_meta/us_fina_ind_progress.json",
        key_space=None,
        completed_field=None,
        timestamp_field="updated_at",
        authority="SUPPLEMENTARY",
        tables=_tables(
            "us_fina_ind",
        ),
        note="心跳/进度计数（done/total/pct），非键集合；派单记 8，实测 10。",
    ),
    CheckpointDomain(
        id="alt.progress.us_qfq_factor",
        library="alt_data",
        owner="alt/list_backfill.py",
        file="_meta/us_qfq_factor_progress.json",
        key_space=None,
        completed_field=None,
        timestamp_field="updated_at",
        authority="SUPPLEMENTARY",
        tables=_tables(
            "us_qfq_factor",
        ),
        note="心跳/进度计数（done/total/pct），非键集合；派单记 8，实测 10。",
    ),
    CheckpointDomain(
        id="alt.progress.us_report_balance",
        library="alt_data",
        owner="alt/list_backfill.py",
        file="_meta/us_report_balance_progress.json",
        key_space=None,
        completed_field=None,
        timestamp_field="updated_at",
        authority="SUPPLEMENTARY",
        tables=_tables(
            "us_report_balance",
        ),
        note="心跳/进度计数（done/total/pct），非键集合；派单记 8，实测 10。",
    ),
    CheckpointDomain(
        id="alt.progress.us_report_cashflow",
        library="alt_data",
        owner="alt/list_backfill.py",
        file="_meta/us_report_cashflow_progress.json",
        key_space=None,
        completed_field=None,
        timestamp_field="updated_at",
        authority="SUPPLEMENTARY",
        tables=_tables(
            "us_report_cashflow",
        ),
        note="心跳/进度计数（done/total/pct），非键集合；派单记 8，实测 10。",
    ),
    CheckpointDomain(
        id="alt.progress.us_report_income",
        library="alt_data",
        owner="alt/list_backfill.py",
        file="_meta/us_report_income_progress.json",
        key_space=None,
        completed_field=None,
        timestamp_field="updated_at",
        authority="SUPPLEMENTARY",
        tables=_tables(
            "us_report_income",
        ),
        note="心跳/进度计数（done/total/pct），非键集合；派单记 8，实测 10。",
    ),
    CheckpointDomain(
        id="alt.oversize_hk_val_pct",
        library="alt_data",
        owner="(none)",
        file="_meta/oversize_hk_val_pct_checkpoint.json",
        key_space="code",
        completed_field="done",
        timestamp_field=None,
        authority="LEGACY",
        tables=_tables(
            "hk_val_pct",
        ),
        note="无代码写入方（全仓 grep 0 命中）；hk_val_pct 不在 .alt_checkpoint.json 的 98 表内。",
    ),
    CheckpointDomain(
        id="alt.meta.audit_report",
        library="alt_data",
        owner="(db audit)",
        file="_meta/audit_report.json",
        key_space=None,
        completed_field=None,
        timestamp_field=None,
        authority="NOT_A_CHECKPOINT",
        tables=frozenset(),
        note="审计产物，每次 db audit 覆写；不回答「某键是否已完成」。",
    ),
    CheckpointDomain(
        id="alt.meta.spec_verify_report",
        library="alt_data",
        owner="(alt verify)",
        file="_meta/spec_verify_report.json",
        key_space=None,
        completed_field=None,
        timestamp_field=None,
        authority="NOT_A_CHECKPOINT",
        tables=frozenset(),
        note="校验报告产物；不回答「某键是否已完成」。",
    ),
    CheckpointDomain(
        id="alt.meta.isolation_log",
        library="alt_data",
        owner="(alt io)",
        file="_meta/isolation_log.json",
        key_space=None,
        completed_field=None,
        timestamp_field=None,
        authority="NOT_A_CHECKPOINT",
        tables=frozenset(),
        note="隔离事件日志（只追加），非键集合。",
    ),
    CheckpointDomain(
        id="alt.meta.main_db_snapshot",
        library="alt_data",
        owner="(alt update)",
        file="_meta/main_db_snapshot.json",
        key_space=None,
        completed_field=None,
        timestamp_field=None,
        authority="NOT_A_CHECKPOINT",
        tables=frozenset(),
        note="主库指认快照，供 alt 侧比对；非完成度。",
    ),
    CheckpointDomain(
        id="market.meta.db_version",
        library="market_data",
        owner="(db migrate)",
        file="metadata/db_version.json",
        key_space=None,
        completed_field=None,
        timestamp_field="generated_at",
        authority="NOT_A_CHECKPOINT",
        tables=frozenset(),
        note="版本元数据（6 键），非完成度。",
    ),
)

ALL_DOMAINS: tuple[CheckpointDomain, ...] = DOMAINS

_BY_ID = {d.id: d for d in DOMAINS}


def all_domains() -> tuple[CheckpointDomain, ...]:
    """全部域声明（按声明顺序）。"""
    return DOMAINS


def domain(domain_id: str) -> CheckpointDomain:
    """按 id 取域；不存在直接 KeyError（⛔ 不许静默跳过）。"""
    return _BY_ID[domain_id]


def domains_of(library: str, authority: str = "PRIMARY") -> list[str]:
    """该库在给定 authority 下覆盖的表清单（排序去重）。默认只看 PRIMARY。"""
    out: set[str] = set()
    for d in DOMAINS:
        if d.library == library and d.authority == authority:
            out |= set(d.tables)
    return sorted(out)


def domains_for(table: str, library: str | None = None,
                key_space: str | None = None) -> list[CheckpointDomain]:
    """按表名（可选库/键空间）找域；返回空 list 表示未登记。"""
    out = []
    for d in DOMAINS:
        if table not in d.tables:
            continue
        if library is not None and d.library != library:
            continue
        if key_space is not None and d.key_space != key_space:
            continue
        out.append(d)
    return out


def describe() -> dict:
    """机读摘要（供 audit / config-check 打印）。"""
    return {
        "n_domains": len(DOMAINS),
        "by_authority": {a: sum(1 for d in DOMAINS if d.authority == a) for a in AUTHORITIES},
        "by_library": {
            lib: {
                "n_domains": sum(1 for d in DOMAINS if d.library == lib),
                "n_tables_primary": len(domains_of(lib)),
            } for lib in LIBRARIES
        },
        "n_files": len({d.file for d in DOMAINS}),
    }
