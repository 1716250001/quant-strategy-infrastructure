# -*- coding: utf-8 -*-
"""
alt/spec.py — akshare 备用库的【表规格唯一真源】
=================================================
集中定义每张表的：接口、取数模式、布局、日期列、去重键、列映射。

为什么独立成文件：
  主库把表规格散在 config.py 的 4 处（BACKFILL_TARGETS / TABLE_DATE_COL /
  DB_AUDIT_PKEYS / db_migrate.DATE_COL），新增表需多处同步、易漏。
  alt 库把规格收敛到本文件一处，从结构上消除"多处不同步"问题。

⚠ 三条关键纪律（都由实测踩坑得出）：
  1. layout="by_year" 时一个文件含全市场数据 → 去重键必须含标的列或
     subset="*"；若只用日期列判重，同一天的全部行会被整批删光。
  2. enum 模式 concat 多组结果 → 必须写入"枚举值标识列"，否则不同枚举值
     的同日数据会互相判重。
  3. 按日型接口（如涨停池）返回数据**不含日期列**，必须由调用方注入
     （见 inject_date 标志）。
"""
from dataclasses import dataclass, field
from typing import Optional, Union


@dataclass
class TableSpec:
    """一张表的完整取数与落盘规格"""

    name: str                                   # alt 库中的表名（目录名）
    func: str                                   # akshare 函数名
    tier: str = "P0"                            # 优先级
    layout: str = "single"                      # single | by_year
    date_col: str = "date"                      # 落盘后的日期列名（统一 YYYYMMDD 字符串）

    # 源数据的日期列名；None 表示源数据不含日期列（按日型接口），需注入
    src_date_col: Optional[str] = None

    # 去重键。列表=复合主键（运行时会校验列是否存在，缺失则回退全列判重）
    # "*" = 全列判重
    subset: Union[list, str, None] = None

    # 接口的日期参数名（如涨停池的 "date"）。None = 接口不接受日期参数。
    # 与 src_date_col 的区别：date_param 管"怎么调接口"，src_date_col 管"怎么读结果"
    date_param: Optional[str] = None

    mode: str = "once"                          # once=无参 | enum=按参数枚举多次
    params: dict = field(default_factory=dict)  # 固定参数
    enum_param: Optional[str] = None            # enum 参数名
    enum_values: list = field(default_factory=list)
    enum_col: Optional[str] = None              # enum 值写入的列名

    # 列重命名：{源列名: 目标列名}。日期列无需在此写，由 src_date_col 处理
    rename: dict = field(default_factory=dict)

    # 数据新鲜度阈值（天）。最新数据超过此天数即判"源端可能停更"。
    # 宏观类留足发布滞后余量（月度+季度）用 120；
    # 行情/快照类应设更小（如 15）。
    max_age_days: int = 120

    # 每日更新时的刷新间隔（天）。用于降低无谓请求：
    #   日频数据 = 1；月度宏观数据 = 7（每周查一次足够）
    update_every_days: int = 1

    # 可回补窗口（**交易日**天数）。源端只保留最近 N 个交易日数据时填写；0 = 无限制。
    # ⚠ 为什么必须显式登记：超窗口请求会**报错或静默返回空表**，
    #   若回补脚本不知道窗口，会浪费时间逐个试错、且把"窗口限制"
    #   误判为"断点已完成"（空数据也记断点）→ 历史永久缺口。
    #
    # ⚠ 单位是**交易日**，不是自然日（2026-09-19 统一口径）。
    #   源端语义即"最近 N 个交易日"（akshare 源码层硬校验，实测
    #   stock_zt_pool_zbgc_em / _dtgc_em 超出直接 raise
    #   "只能获取最近 30 个交易日的数据"）。
    #   ⚠ 历史教训：本字段曾被两处代码用**不同口径**解释——
    #     backfill 用 recent_trade_dates(n)（交易日），
    #     update/audit 用 Timestamp - Timedelta(days=n)（自然日），
    #     实测 30 天档下两者相差 9 个自然日（20260810 vs 20260819），
    #     且都偏离源端实际边界（20260820）。现已统一为交易日口径，
    #     **新增调用点必须用 recent_trade_dates()**，不得用 Timedelta。
    #
    # ⚠ 取值原则：宜**略宽于**源端实际窗口（宁大勿小）。
    #   填大了：多尝试几个必然失败的日期，但"越界即停"兜底，代价仅 1 次请求；
    #   填小了：永久丢失尚可补的历史（源端过期不候，不可再生）。
    #   实测参考：登记 20 天，源端实际只保留约 15 个交易日；登记 30 天，实际约 22 天。
    max_backfill_days: int = 0

    # 核心指标列（用于「数值健全性」的**强信号**判定：最新值为零即判损坏）。
    # 只对真正承载信号的列做此判定，避免把辅助字段的 0 值当损坏
    # （实测误报：涨停池的「炸板次数」零值 37%、「涨速」常数为 0，均属业务正常）。
    # 为空 = 对所有数值列判定（谨慎使用）。
    core_metric_cols: list = field(default_factory=list)

    # 强制转为字符串的列。用于"语义混杂"的列：
    # 实测 stock_market_activity_legu 的 value 列同时含
    # 计数(3937.0)、百分比字符串('75.41%')、时间戳('2026-09-18 15:00:00')，
    # 落盘时 pandas 无法统一类型 → 分区写入失败。
    # 统一转 str 可让落盘稳定（该表本就用于快照查看，不做数值运算）。
    force_str_cols: list = field(default_factory=list)

    # 该表**没有日期列**（如"所有城市清单""接口元数据表"）。
    # 置 True 时：跳过日期校验与归一化，按单一文件落盘、全列判重。
    # 实测用例：air_city_table（城市清单）、air_quality_watch_point（监测点表）。
    has_date: bool = True

    # 「已知源端停更」标记（2026-09-21 新增）。
    # 非空 = 该表源端已**确认停更**，freshness_report 会把它报为
    # 「已登记停更」而非泛泛的「陈旧」，避免与"真正新出现的断更"混在一起
    # —— 后者才是需要关注的（否则每次自检都刷 15 条已知项 = 狼来了）。
    known_stale: str = ""

    note: str = ""

    @property
    def inject_date(self) -> bool:
        """是否需要在落盘时注入日期列（按日型接口）"""
        return self.src_date_col is None

    @property
    def is_snapshot(self) -> bool:
        """是否为「纯快照表」——接口完全没有日期维度，只能拿到"当前"数据。

        判据：需要注入日期（src_date_col is None）**且**接口不接受日期参数
              （date_param is None）。

        ⚠ 为什么必须区分（R1，2026-09-19 实证，属严重静默错误）：
          inject_date=True 的表其实分两类，命运完全相反：

            · **传参型**（zt_pool 族，date_param="date"）
              → 传日期能拿到**当日真值**；复核窗口（重拉最近 N 天）
                对它们是**有益**的，能修"断点已记但当日数据残缺"。

            · **纯快照型**（本属性 True：分时波指 9 张 + 赚钱效应 1 张）
              → 接口无论传什么日期都只返回**同一份当前快照**；
                复核窗口会把"今天的数据"逐行覆盖到最近 N 个历史日期上，
                造成**静默污染**（不报错、行数正常、日期正常，只有数值是错的）。

          实测证据：10 张这类表的 0916/0917/0918 三天数据逐行完全相同
                    （对照组 zt_pool 族三日各不相同）。

        ⚠ 衍生纪律：纯快照表**无法回补历史**，其注入日期必须等于"拉取时刻"
          所属的最近已收盘交易日；不允许由调用方指定任意历史日期。
        """
        return self.inject_date and self.date_param is None

    def n_requests(self) -> int:
        """本表首次建库的预估请求数"""
        return len(self.enum_values) if self.mode == "enum" else 1


# ============================================================
# P0 · 估值情绪锚（11 张 / 25 次请求）
#   once 5 张 + enum 4 张(gxl/below_net_asset/market_pe/market_pb/high_low)
#   + market_pe_kcb 1 张（科创版从 market_pe_lg 拆出）
# tushare 完全没有、本地库空白、可一次回补十余年历史
# 全部走乐咕乐股稳定域名（实测零风控问题；限速 10 次/分，30 次/分会 429）
# ============================================================
P0 = [
    # —— 无参一次型 ——
    # 注：以下均为**日频**数据，max_age_days 设 15（留足长假余量）；
    #     若超过 15 天没更新，说明日更链路断了，必须报警。
    TableSpec("ebs_lg", "stock_ebs_lg", "P0", "single", "date",
              src_date_col="日期", subset="*", mode="once", max_age_days=15,
              note="股债利差（2005至今）"),
    TableSpec("buffett_lg", "stock_buffett_index_lg", "P0", "single", "date",
              src_date_col="日期", subset="*", mode="once", max_age_days=15,
              note="巴菲特指标 总市值/GDP（2005至今）"),
    TableSpec("congestion_lg", "stock_a_congestion_lg", "P0", "single", "date",
              src_date_col="date", subset="*", mode="once", max_age_days=15,
              note="大盘拥挤度（2011至今）"),
    TableSpec("all_pb_lg", "stock_a_all_pb", "P0", "single", "date",
              src_date_col="date", subset="*", mode="once", max_age_days=45,
              note="全部A股等权重/中位数市净率（月频）"),
    TableSpec("ttm_lyr_lg", "stock_a_ttm_lyr", "P0", "single", "date",
              src_date_col="date", subset="*", mode="once", max_age_days=45,
              note="全部A股等权重/中位数市盈率（月频）"),

    # —— 枚举型（枚举列参与判重）——
    TableSpec("gxl_lg", "stock_a_gxl_lg", "P0", "single", "date",
              src_date_col="日期", subset=["date", "symbol"], mode="enum",
              enum_param="symbol", enum_col="symbol", max_age_days=15,
              enum_values=["上证A股", "深证A股", "创业板", "科创板"],
              note="A股股息率（4 类）"),
    TableSpec("below_net_asset", "stock_a_below_net_asset_statistics", "P0", "single", "date",
              src_date_col="date", subset=["date", "symbol"], mode="enum",
              enum_param="symbol", enum_col="symbol", max_age_days=15,
              enum_values=["全部A股", "沪深300", "上证50", "中证500"],
              note="破净股统计（4 类）"),
    TableSpec("market_pe_lg", "stock_market_pe_lg", "P0", "single", "date",
              src_date_col="日期", subset=["date", "symbol"], mode="enum",
              enum_param="symbol", enum_col="symbol", max_age_days=45,
              enum_values=["上证", "深证", "创业板"],
              note="主板市盈率（月频；科创版已拆至 market_pe_kcb——该组为日频且列不同）"),
    # ⚠ 实测陷阱（2026-09-18）：stock_market_pe_lg 的 4 个枚举值返回结构不一致：
    #   上证/深证/创业板 → 月频，列为 ['日期','指数','平均市盈率']
    #   科创版         → 日频，列为 ['日期','总市值','市盈率']（无『平均市盈率』）
    #   混在一张表里会造成"同表不同频率"的下游陷阱，故单独成表。
    TableSpec("market_pe_kcb", "stock_market_pe_lg", "P0", "single", "date",
              src_date_col="日期", subset="*", mode="once", max_age_days=15,
              params={"symbol": "科创版"},
              note="科创板市盈率（日频；科创板为奇点框架科技估值锚）"),
    TableSpec("market_pb_lg", "stock_market_pb_lg", "P0", "single", "date",
              src_date_col="日期", subset=["date", "symbol"], mode="enum",
              enum_param="symbol", enum_col="symbol", max_age_days=45,
              enum_values=["上证", "深证", "创业板", "科创版"],
              note="主板市净率（月频，4 类）"),
    TableSpec("high_low_stat", "stock_a_high_low_statistics", "P0", "single", "date",
              src_date_col="date", subset=["date", "symbol"], mode="enum",
              enum_param="symbol", enum_col="symbol", max_age_days=15,
              enum_values=["all", "sz50", "hs300", "zz500"],
              note="创新高新低股票数（4 类，仅近约2年）"),
]

# ============================================================
# P1 · 波动率（QVIX 18 只 / tushare 完全没有）
# ============================================================
# ⚠ 实测（2026-09-18）QVIX 分两类结构，必须分开处理：
#   日频 9 只：列 ['date','open','high','low','close'] —— 有 date 列，直接归一化
#   分时 9 只：列 ['time','qvix'] —— time 是**时刻**(如 '9:30:00')而非日期！
#              仅返回当日 239 行，属**快照型**，需按日累积（无法回补历史）。
_QVIX_DAILY = [
    ("qvix_50etf", "index_option_50etf_qvix", "50ETF波指"),
    ("qvix_300etf", "index_option_300etf_qvix", "300ETF波指"),
    ("qvix_500etf", "index_option_500etf_qvix", "500ETF波指"),
    ("qvix_100etf", "index_option_100etf_qvix", "深证100ETF波指"),
    ("qvix_50index", "index_option_50index_qvix", "上证50股指波指"),
    ("qvix_300index", "index_option_300index_qvix", "中证300股指波指"),
    ("qvix_1000index", "index_option_1000index_qvix", "中证1000股指波指"),
    ("qvix_cyb", "index_option_cyb_qvix", "创业板波指"),
    ("qvix_kcb", "index_option_kcb_qvix", "科创板波指"),
]
_QVIX_MIN = [
    ("qvix_50etf_min", "index_option_50etf_min_qvix", "50ETF波指-分时"),
    ("qvix_300etf_min", "index_option_300etf_min_qvix", "300ETF波指-分时"),
    ("qvix_500etf_min", "index_option_500etf_min_qvix", "500ETF波指-分时"),
    ("qvix_100etf_min", "index_option_100etf_min_qvix", "深证100ETF波指-分时"),
    ("qvix_50index_min", "index_option_50index_min_qvix", "上证50股指波指-分时"),
    ("qvix_300index_min", "index_option_300index_min_qvix", "中证300股指波指-分时"),
    ("qvix_1000index_min", "index_option_1000index_min_qvix", "中证1000股指波指-分时"),
    ("qvix_cyb_min", "index_option_cyb_min_qvix", "创业板波指-分时"),
    ("qvix_kcb_min", "index_option_kcb_min_qvix", "科创板波指-分时"),
]
P1_QVIX = (
    # 日频：有 date 列，可一次性回补全历史（2015至今）
    # ⚠ 实测（2026-09-18）9 只日频 QVIX 中，**股指类 3 只数值已损坏**：
    #   qvix_50index / qvix_300index / qvix_1000index
    #   → 数据稀疏（731~1460 非空 / 2816 行）、含 0.0、**最新连续 3 个交易日 close 全为 0.0**
    #   → 同期 ETF 类（qvix_50etf 等 6 只）完全正常（0 零值、最新 14~36）
    #   处置：保留落盘（历史段仍可用），但在 note 中标注，
    #         且校验工具的「数值健全性」检测会每次报警。
    [TableSpec(n, f, "P1", "single", "date", src_date_col="date",
               subset="*", mode="once", max_age_days=15,
               core_metric_cols=["close"],
               note=note + ("【⚠数值已损坏·最新为0，不可用于当期】"
                            if n in ("qvix_50index", "qvix_300index", "qvix_1000index")
                            else "（日频，可回补）"))
     for n, f, note in _QVIX_DAILY]
    # 分时：**快照型**，time 为时刻，需按日累积
    + [TableSpec(n, f, "P1", "by_year", "trade_date",
                 src_date_col=None, date_param=None, max_age_days=15,
                 core_metric_cols=["qvix"],
                 subset=["trade_date", "time"], mode="once",
                 note=note + "（分时快照，须日更累积）")
       for n, f, note in _QVIX_MIN]
)

# ============================================================
# P1 · 海外宏观（tushare 最大空白区）
# ============================================================
# ⚠ 实测（2026-09-18）海外宏观有**两种数据源结构**，列名完全不同：
#   结构 A（金十/新浪系）：['商品','日期','今值','预测值','前值'] → src_date_col='日期'
#   结构 B（东财系）：    ['时间','前值','现值','发布日期']     → src_date_col='发布日期'
#       其中 '时间' 是中文月份文本（如 '2026年09月'），真正日期在 '发布日期'。
#       故重命名为 period 予以保留，两列都可用（period 看归属期，发布日期防未来函数）。
_MACRO_A = [
    # ⚠⚠ 重要实测发现（2026-09-18）：下列「金十系」接口**源端已停更约 1 年**
    #      所有抽样函数最新数据均停在 2025-08~2025-10（当期 2026-09）。
    #      接口可调用、结构正常、格式对 —— 但数据是陈旧的，**不可用于当期判断**。
    #      处置：保留（有 1970 年至今的长历史，可做历史研究），
    #            但 max_age_days 设小以在每次校验时自动报警；
    #            当期分析请改用下节的东财系替代源。
    ("macro_usa_cpi", "macro_usa_cpi_monthly", "美国CPI月率"),
    ("macro_usa_core_cpi", "macro_usa_core_cpi_monthly", "美国核心CPI月率"),
    ("macro_usa_gdp", "macro_usa_gdp_monthly", "美国GDP月率"),
    ("macro_usa_unemp", "macro_usa_unemployment_rate", "美国失业率"),
    ("macro_usa_non_farm", "macro_usa_non_farm", "美国非农就业"),
    ("macro_usa_ppi", "macro_usa_ppi", "美国PPI"),
    ("macro_usa_adp", "macro_usa_adp_employment", "美国ADP就业"),
    ("macro_bank_usa", "macro_bank_usa_interest_rate", "美联储利率决议"),
    ("macro_bank_euro", "macro_bank_euro_interest_rate", "欧洲央行利率决议"),
    ("macro_bank_uk", "macro_bank_english_interest_rate", "英国央行利率决议"),
    ("macro_bank_japan", "macro_bank_japan_interest_rate", "日本央行利率决议"),
    ("macro_bank_australia", "macro_bank_australia_interest_rate", "澳联储利率决议"),
    ("macro_bank_swiss", "macro_bank_switzerland_interest_rate", "瑞士央行利率决议"),
    ("macro_euro_cpi", "macro_euro_cpi_yoy", "欧元区CPI年率"),
]
# ⚠ 东财系美国宏观（**新鲜源**，实测到 2026-09-01）——
#   美国宏观在 akshare 上有两条渠道，命运相反：
#     金十系：历史长（1970 起），但已停更 1 年
#     东财系：仅 2008 起，但**当期可用**
_MACRO_US_EM = [
    ("macro_usa_cpi_yoy_em", "macro_usa_cpi_yoy", "美国CPI年率（东财）"),
]
_MACRO_B = [
    ("macro_uk_cpi", "macro_uk_cpi_yearly", "英国CPI年率"),
    ("macro_jp_cpi", "macro_japan_cpi_yearly", "日本CPI年率"),
    ("macro_de_cpi", "macro_germany_cpi_yearly", "德国CPI年率"),
    ("macro_ca_cpi", "macro_canada_cpi_yearly", "加拿大CPI年率"),
    ("macro_au_cpi", "macro_australia_cpi_yearly", "澳大利亚CPI年率"),
    ("macro_uk_gdp", "macro_uk_gdp_yearly", "英国GDP年率"),
    ("macro_de_gdp", "macro_germany_gdp", "德国GDP"),
    ("macro_ca_gdp", "macro_canada_gdp_monthly", "加拿大GDP月率"),
    ("macro_uk_unemp", "macro_uk_unemployment_rate", "英国失业率"),
    ("macro_jp_unemp", "macro_japan_unemployment_rate", "日本失业率"),
    ("macro_ca_unemp", "macro_canada_unemployment_rate", "加拿大失业率"),
    ("macro_au_unemp", "macro_australia_unemployment_rate", "澳大利亚失业率"),
]

# ⚠ 已弃用表登记（不静默丢弃，留痕说明原因）
DROPPED = [
    {
        "name": "macro_ch_cpi",
        "func": "macro_swiss_cpi_yearly",
        "reason": "数据源不完整：仅 20 行季度数据，且 20 行中 7 行缺发布日期"
                  "（2007-2013 段全为 NaT），日期无法可靠归一化；"
                  "瑞士季度 CPI 对 A 股策略价值极低。",
        "evidence": "2026-09-18 实测：发布日期非空 13/20（65%），"
                    "低于 80% 可解析阈值",
    },
]

# ⚠ 已知数据质量问题登记（表已建但数据不可用于当期，留痕备查）
KNOWN_ISSUES = [
    {
        "name": "qvix_*_index（股指波指 3 只）"
                "= qvix_50index / qvix_300index / qvix_1000index",
        "kind": "数值损坏",
        "detail": "数据稀疏 + 含 0.0 + 最新连续 3 个交易日 close 全为 0.0；"
                  "同期 ETF 类波指完全正常",
        "impact": "不可用于当期波动率判断；历史段（2015~2025-09）仍可用",
        "detect": "alt/verify_spec.py 的「数值健全性」检测会报警",
    },
    {
        "name": "金十系美国/欧洲宏观 14 张"
                "（macro_usa_* / macro_bank_* / macro_euro_cpi）",
        "kind": "源端停更",
        "detail": "接口可调用、结构正常，但最新数据停在 2025-08~10（陈旧约 1 年）",
        "impact": "历史研究可用；当期分析须改用东财系替代源"
                  "（已补 macro_usa_cpi_yoy_em）",
        "detect": "alt/verify_spec.py 的「数据新鲜度」检测会报警（阈值 45 天）",
    },
]

P1_MACRO = (
    # 金十系（历史长、但已停更 1 年；max_age_days 调小以自动报警）
    [TableSpec(n, f, "P1", "single", "date", src_date_col="日期",
               subset="*", mode="once", max_age_days=45, update_every_days=7,
               note=note + "【金十源·已陈旧】",
               known_stale="金十系源端已停更（实测停于 2025-08~10；接口可调、"
                           "结构正常，仅数据陈旧，当期用东财系替代）")
     for n, f, note in _MACRO_A]
    # 东财系美国（新鲜）—— 实测为结构B（['时间','发布日期','现值','前值']）
    + [TableSpec(n, f, "P1", "single", "date", src_date_col="发布日期",
                 subset="*", mode="once", rename={"时间": "period"},
                 max_age_days=45, update_every_days=7, note=note)
       for n, f, note in _MACRO_US_EM]
    # 东财系其他国家（新鲜）
    # ⚠ macro_uk_gdp 是**年频发布**（每年 3 月发上一年 Q4，实测 17 行至 2025Q4），
    #   用组内统一的 120 天阈值会**误报「陈旧」**（实测 175 天）→ 单独放宽到 400 天。
    + [TableSpec(n, f, "P1", "single", "date", src_date_col="发布日期",
                 subset="*", mode="once", rename={"时间": "period"},
                 max_age_days=(400 if n == "macro_uk_gdp" else 120),
                 update_every_days=7, note=note)
       for n, f, note in _MACRO_B]
    # 中国 CPI（国家统计局口径）—— 2026-09-21 新增
    # ⚠ 为什么单列而非并入 _MACRO_B：
    #   ① 日期列名是『月份』（_MACRO_B 用的是『发布日期』）；
    #   ② 日期格式是 '2026年08月份'，`pd.to_datetime` **无法解析**
    #      （实测 0/224 全为 NaT）→ 已为它扩展
    #      `config_alt.normalize_date_series` 的中文年月分支；
    #   ③ 数据含全国/城市/农村 × 当月·同比·环比·累计，是**宽表**（非『今值/预测值/前值』）。
    # ⚠ 为什么选它而非金十系 `macro_china_cpi_monthly` / `_yearly`：
    #   金十系两接口**已停更于 2025-09**（与 KNOWN_ISSUES 记录的「金十系已陈旧 1 年」一致），
    #   而本接口**新到 2026-08**（实测）。
    # ⚠ 命名说明：与既有 `macro_{地区}_cpi` 规律的地区码保持一致用 `cn`；
    #   注意 DROPPED 里的 `macro_ch_cpi` 是**瑞士** CPI（当年命名有误导），两者无关。
    + [TableSpec("macro_cn_cpi", "macro_china_cpi", "P1", "single", "date",
                 src_date_col="月份", subset="*", mode="once",
                 max_age_days=120, update_every_days=7,
                 note="中国CPI(国家统计局口径): 全国/城市/农村 × 当月·同比·环比·累计")
    ]
)

P1 = P1_QVIX + P1_MACRO

# ============================================================
# P3 · 快照累积（按日型，历史不可回补，须尽快开工）
# 全部走 push2ex 稳定域名；数据不含日期列，需注入
# ============================================================
P3 = [
    # 赚钱效应（无日期参数，但数据无日期列 → 仅注入）
    TableSpec("market_activity", "stock_market_activity_legu", "P3", "by_year", "trade_date",
              src_date_col=None, date_param=None, max_age_days=15,
              max_backfill_days=0,
              subset=["trade_date", "item"], mode="once",
              force_str_cols=["value"],
              note="赚钱效应（快照，须日更积累）"),

    # 涨停族（接口接受 date 参数，数据无日期列 → 传参 + 注入）
    # ⚠ 实测（2026-09-18）保留窗口：
    #   zt_pool / _previous / _strong / _sub_new：无硬校验，但源端只留约 2~3 周
    #     （实测 20260814 返回 0 行；20260915 返回 32 行）
    #   zt_pool_zbgc / _dtgc：akshare **源码层硬校验 30 天**，超出直接 raise
    #   → 故这几张表**必须日更**，否则历史永久缺口
    TableSpec("zt_pool", "stock_zt_pool_em", "P3", "by_year", "trade_date",
              src_date_col=None, date_param="date", max_age_days=15,
              max_backfill_days=20, core_metric_cols=["涨跌幅"],
              subset=["trade_date", "代码"], mode="once",
              note="涨停股池【源端仅留约2~3周，须日更】"),
    TableSpec("zt_pool_previous", "stock_zt_pool_previous_em", "P3", "by_year", "trade_date",
              src_date_col=None, date_param="date", max_age_days=15,
              max_backfill_days=20, core_metric_cols=["涨跌幅"],
              subset=["trade_date", "代码"], mode="once",
              note="昨日涨停股池【源端仅留约2~3周，须日更】"),
    TableSpec("zt_pool_strong", "stock_zt_pool_strong_em", "P3", "by_year", "trade_date",
              src_date_col=None, date_param="date", max_age_days=15,
              max_backfill_days=20, core_metric_cols=["涨跌幅"],
              subset=["trade_date", "代码"], mode="once",
              note="强势股池【源端仅留约2~3周，须日更】"),
    TableSpec("zt_pool_sub_new", "stock_zt_pool_sub_new_em", "P3", "by_year", "trade_date",
              src_date_col=None, date_param="date", max_age_days=15,
              max_backfill_days=20, core_metric_cols=["涨跌幅"],
              subset=["trade_date", "代码"], mode="once",
              note="次新股池【源端仅留约2~3周，须日更】"),
    TableSpec("zt_pool_zbgc", "stock_zt_pool_zbgc_em", "P3", "by_year", "trade_date",
              src_date_col=None, date_param="date", max_age_days=15,
              max_backfill_days=30, core_metric_cols=["涨跌幅"],
              subset=["trade_date", "代码"], mode="once",
              note="炸板股池【源码硬校验30天，超出报错】"),
    TableSpec("zt_pool_dtgc", "stock_zt_pool_dtgc_em", "P3", "by_year", "trade_date",
              src_date_col=None, date_param="date", max_age_days=15,
              max_backfill_days=30, core_metric_cols=["涨跌幅"],
              subset=["trade_date", "代码"], mode="once",
              note="跌停股池【源码硬校验30天；弱市日可能为空属正常】"),
]

# ============================================================
# 汇总索引
# ============================================================
# ============================================================
# P4 · 美股（akshare 前复权）— 2026-09-21 新增
# ============================================================
# 背景与关键决策（实测得来，勿改）:
#   ① **复权问题（决定性）**: akshare stock_us_daily 默认返回**未复权**价格，
#      实测 AAPL 2020-08-31(4:1) 出现 -74.2% 断崖、NVDA 2024-06-10(10:1) -89.9%
#      → 直接用于技术分析会全线失真。**必须传 adjust="qfq"**（实测 119.33→123.56
#      即 +3.5%，已前复权；耗时不变约 0.29s/只）。
#   ② **无增量接口**: stock_us_daily 只能拉全历史，无按日期增量参数
#      ⇒ 日更 = 重拉全部 18,163 只 ≈ 41 分钟/天，且见 ③ ⇒ **不做日更**。
#   ③ **前复权基准会漂移（最易忽略）**: 前复权以"最新价"为基准回推，**每次新拆股/
#      分红都会改写全部历史** ⇒ 不能用"增量追加"（老数据旧基准、新数据新基准，
#      混存会前后不一致且不报错）⇒ **必须整表重建**（定期全量，非日更）。
#   ④ **生存者偏差**: 清单取自"当前在市快照"，已退市/被并购标的不在清单
#      ⇒ 长期历史回测会系统性高估，此偏差**建库即固化、不可事后修复**。
#   ⑤ **模式 mode="list"**: 本组为"按标的列表逐只拉"，alt 原有 once/enum
#      两种模式都表达不了 ⇒ 新增 list 模式，**由 alt/us_backfill.py 专门处理**；
#      backfill.py / update.py 已显式跳过该模式（避免被当 once 无参调用而报错）。
#   ⑥ 已知数据质量瑕疵（akshare 官方文档自承）: CIEN 复权因子错误；
#      AI 新股未发生复权却返回复权因子。使用该两标的时需留意。
P4 = [
    # —— 标的全市场清单（元数据，清单元数据无日期列）——
    TableSpec("us_universe", "stock_us_spot", "P4", "single", "date",
              has_date=False, subset="*", mode="list", max_age_days=15,
              note="美股标的全市场清单(18,163只): symbol/英文名/中文名/行业/交易所/市值/PE; "
                   "⚠ 快照型(每次重建)"),
    # —— 美股日线（前复权）——
    TableSpec("us_daily_qfq", "stock_us_daily", "P4", "by_year", "date",
              src_date_col="date", subset=["symbol", "date"], mode="list",
              max_age_days=5,
              note="美股日线【前复权 adjust=qfq】; 逐只拉(按标的) → 由 alt/us_backfill.py 处理; "
                   "⚠ 前复权基准随新拆股漂移 ⇒ 只可整表重建、不可增量追加; "
                   "⚠ 数据为美股 T-1（美股收盘=北京次日凌晨）"),
]

ALL_SPECS = P0 + P1 + P3 + P4
BY_NAME = {s.name: s for s in ALL_SPECS}
BY_TIER: dict = {}
for _s in ALL_SPECS:
    BY_TIER.setdefault(_s.tier, []).append(_s)

# ⚠ tier 清单的**单一真源**（2026-09-21 新增）:
#   原先 update.py / backfill.py / verify_spec.py 的 CLI 各自硬编码
#   `choices=["P0","P1","P3"]`，本文件的 summary() 也硬编码 `("P0","P1","P3")`
#   → 新增 P4 组后**四处全部漏改**，表现为 `--tier P4` 直接报错
#     （但默认无参运行不受影响，故不易察觉）。
#   这与本项目高频缺陷族"两个结构各管一摊"同源：
#     凡需要与 ALL_SPECS 联动的清单，都应**动态派生**而非手写，
#     否则新增 tier 时只改一处、且全程不报错。
#   现统一从 BY_TIER 派生（保序：按 ALL_SPECS 的登记顺序）。
TIERS = list(BY_TIER)


def get(name):
    """按表名取规格"""
    if name not in BY_NAME:
        raise KeyError(f"未登记的表: {name}（可选: {sorted(BY_NAME)}）")
    return BY_NAME[name]


def tier_specs(tier):
    """取某优先级的全部规格"""
    return list(BY_TIER.get(tier, []))


def summary():
    """规格概览（供 dry-run 打印）"""
    out = []
    for t in TIERS:                     # ⚠ 用 TIERS 而非硬编码（见上方说明）
        specs = BY_TIER.get(t, [])
        out.append({
            "tier": t,
            "tables": len(specs),
            "requests": sum(s.n_requests() for s in specs),
            "names": [s.name for s in specs],
        })
    return out
