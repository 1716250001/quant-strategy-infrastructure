# -*- coding: utf-8 -*-
"""
fetch/backfill_spec.py — 补数任务规格层
========================================
从 fetch/backfill.py 拆出（原文件 762 行、5 个职责混杂）。本层只做
"应该拉哪些数据"的纯计算，**不发任何网络请求、不碰磁盘数据**。

包含三类:
  一、代码清单解析   resolve_codes()
  二、日期维度工具   month_last_trade_dates / year_ranges / report_periods ...
  三、任务构建与估算 build_tasks() / estimate_requests()

by_date 的 freq 参数（2026-09-19 新增）:
  day   每个交易日（默认，行情类）
  week  每周最后一个交易日（weekly / index_weekly）
  month 每月最后一个交易日（monthly / index_monthly）
  —— 周线/月线接口只需在周末/月末那个交易日拉一次全市场，
     不需要逐日拉（逐日拉会重复 5 倍/20 倍请求且数据完全重复）。

五种取数模式(与 config.BACKFILL_TARGETS 的 mode 字段对应):
  by_code   按标的拉全序列   → 参数 ts_code=<code>
  by_date   按日拉全市场     → 参数 trade_date=<交易日>
  by_month  按月末           → 参数 <code_param>=<code> + trade_date=<月末>
  by_range  按年区间         → 参数 start_date/end_date
  by_period 按报告期精确匹配 → 参数 end_date=<报告期>
  once      一次性全量       → 无参数
  by_param  按枚举参数逐个   → 参数 <param_name>=<枚举值>（如 exchange）+ offset 分页
            适用于"既无日期也没有标的维度"的元数据表:
              fut_basic / opt_basic —— 无参数调用会被静默截断
              (fut_basic 返 10000 / 实测真实 11275; opt_basic 返 12000)
              且 opt_basic 的 trade_date 参数会被**静默忽略**
              (实测 20260918 与 20200102 返回完全相同)，只能按交易所分块拉。
            ⚠ 与 by_code 的区别: by_code 的支持值来自 resolve_codes 的元数据解析，
              本模式的值直接写在 config 里（交易所是有限枚举）。
  paged     按 limit/offset 逐页遍历 → 无参 + offset（全量元数据表）
            适用于"一次拿不完、也无日期/标的维度"的全量快照表:
              us_basic(24256) / fund_manager(85636) / slb_len_mm(55214)
              / index_member_all(5913)
            ⚠ 与 by_param 的区别: by_param 按"枚举值各拉一遍"，本模式是
              "同一查询逐页往后翻"（offset 递进直到空页或不足一页）。
            ⚠ 不要用 end_date 续拉代替: 这些表无日期列，只能 offset。

⚠ 三条日期纪律（均为实测踩坑，勿改）:
  1. by_date 必须按"今天"截断 —— 交易日历含未来交易日(实测到 2027-12-31,
     2016 起共 2916 天里 313 天在未来), 不截断会白跑数百次请求。
  2. by_range 的当年区间端点必须是"今天"而非 12-31, 否则已完成任务
     会被误判成缺口。
  3. by_period 的 end_date 是精确匹配报告期, 不是日期区间。
"""
import os
from datetime import datetime

import pandas as pd

from config import BACKFILL_INDEX_CODES, BACKFILL_PAGE_CONF, META_DIR
from common.calendar import open_dates, open_dates_by_market


# ============================================================
# 一、代码清单解析
# ============================================================
def resolve_codes(kind):
    """按 codes 字段解析出代码列表。全部读本地元数据, 不发请求。

    返回:
        代码列表；或 None 表示"需先拉前置基础表"(fx/hk)。
        ⚠ None 与 [] 语义不同, 调用方需区分。
    """
    if kind in (None, "none", ""):
        return []

    meta = lambda f: os.path.join(META_DIR, f)

    if kind in ("stock", "stock_all"):
        p = meta("stock_basic.parquet")
        if not os.path.exists(p):
            print(f"  [WARN] 缺少 {p}，代码清单为空")
            return []
        df = pd.read_parquet(p)
        if kind == "stock":
            df = df[df.get("list_status") == "L"]
        return df["ts_code"].dropna().tolist()

    if kind == "fund_etf":
        p = meta("fund_basic.parquet")
        if not os.path.exists(p):
            return []
        df = pd.read_parquet(p)
        # 场内基金才有行情; .OF 无 fund_daily/fund_adj
        if "market" in df.columns:
            df = df[df["market"] == "E"]
        return df["ts_code"].dropna().tolist()

    if kind in ("index", "index_major"):
        return list(BACKFILL_INDEX_CODES)

    if kind in ("cb", "cb_listed"):
        p = meta("cb_basic.parquet")
        if not os.path.exists(p):
            return []
        df = pd.read_parquet(p)
        if kind == "cb_listed" and "delist_date" in df.columns:
            df = df[df["delist_date"].isna() | (df["delist_date"] == "")]
        return df["ts_code"].dropna().tolist()

    if kind == "fx":
        # fx_daily 需要 fx_obasic 的代码; 首次运行时缓存到 metadata
        p = meta("fx_obasic.parquet")
        if not os.path.exists(p):
            return None          # 信号: 需先拉 fx_obasic
        df = pd.read_parquet(p)
        return df["ts_code"].dropna().tolist()

    if kind == "hk":
        p = meta("hk_basic.parquet")
        if not os.path.exists(p):
            return None          # 信号: 需先拉 hk_basic
        df = pd.read_parquet(p)
        return df["ts_code"].dropna().tolist()

    if kind == "index_global":
        # 国际指数行情(index_global)的代码清单。该接口的代码不是 tushare 常规
        # ts_code(如 "SPX")，而是固定的 22 个国际指数简称，故直接写在 config。
        from config import BACKFILL_GLOBAL_INDEX_CODES
        return list(BACKFILL_GLOBAL_INDEX_CODES)

    if kind == "fut_index":
        # 南华期货指数(fut_index_daily)的代码清单（2026-09-19 新增）。
        # ⚠ 该接口必填 ts_code；代码格式 <简称>.NH，清单写在 config。
        from config import BACKFILL_FUT_INDEX_CODES
        return list(BACKFILL_FUT_INDEX_CODES)

    print(f"  [WARN] 未知的 codes 类型: {kind}")
    return []


def BACKFILL_META_DIR():
    """（已废弃）元数据目录。保留仅为兼容旧调用方，请直接使用 config.META_DIR。"""
    return META_DIR


# ============================================================
# 二、日期维度工具
# ============================================================
def month_last_trade_dates(start, end=None, calendar="SSE"):
    """每月最后一个交易日。返回 [('201601', '20160129'), ...]

    calendar: "SSE"(默认, A股) / "us"(美股, 用 us_tradecal)
    """
    end = end or datetime.now().strftime("%Y%m%d")
    dates = open_dates_by_market(calendar, start_date=start, end_date=end)
    if not dates:
        return []
    s = pd.Series(dates)
    last = s.groupby(s.str[:6]).max()
    return list(zip(last.index.tolist(), last.values.tolist()))


def month_list(start, end=None):
    """月份列表 ['201601', ...]"""
    end = end or datetime.now().strftime("%Y%m%d")
    return [m for m, _ in month_last_trade_dates(start, end)]


def week_last_trade_dates(start, end=None, calendar="SSE", anchor="last_trade"):
    """每周的取样日。返回 ['20160108', '20160115', ...]

    用于"按周聚合"的接口（mode=by_date + freq=week）。

    anchor 决定取哪一天，**两个接口族的实证口径不同，不可混用**：
      - "last_trade"（默认）: 该周**最后一个交易日**。
        适用 weekly / index_weekly 等旧接口 —— 实证 weekly 的 trade_date
        在端午周为 20160608（周三，最后交易日）。
      - "friday": 该周**周五**（自然日，不论是否交易日）。
        适用 stk_weekly_monthly / stk_week_month_adj / fut_weekly_monthly：
        实证其 trade_date 恒为该周周五 —— 端午周=20160610、跨年周=20260102、
        春节周=20170127、国庆周=20191004（均非"最后交易日"）。
        ⚠ 若误用 last_trade：这些接口会**静默漏掉全部长假周**（实测 31 个：
          端午/中秋/春节/清明/劳动/国庆前的最后交易日），无任何报错。

    calendar: "SSE"(默认, A股) / "us"(美股)
    """
    end = end or datetime.now().strftime("%Y%m%d")
    dates = open_dates_by_market(calendar, start_date=start, end_date=end)
    if not dates:
        return []
    s = pd.Series(dates)
    dt = pd.to_datetime(s, format="%Y%m%d")

    if anchor == "friday":
        # 该周周一 + 4 天 = 该周周五（ISO 周必含周五）
        monday = dt - pd.to_timedelta(dt.dt.weekday, unit="D")
        friday = (monday + pd.Timedelta(days=4)).dt.strftime("%Y%m%d")
        today = datetime.now().strftime("%Y%m%d")
        return sorted({x for x in friday.tolist() if x <= today})   # 排除未来周五

    key = dt.dt.strftime("%G-%V")          # ISO 年-周，跨年周正确合并
    last = s.groupby(key).max()
    return sorted(last.tolist())


def year_ranges(start, end=None):
    """按年切分区间。返回 [('20160101','20161231'), ...]

    用于只支持 start_date/end_date 的接口(shibor/us_tycr), 避免单次返回
    被 2000 行上限截断。
    """
    end = end or datetime.now().strftime("%Y%m%d")
    y0, y1 = int(str(start)[:4]), int(str(end)[:4])
    out = []
    for y in range(y0, y1 + 1):
        s = max(int(start), y * 10000 + 101)
        e = min(int(end), y * 10000 + 1231)
        if s <= e:
            out.append((str(s), str(e)))
    return out


def report_periods(start, end=None):
    """报告期列表(季报口径)。返回 ['20160331', '20160630', ...]

    用于 disclosure_date 这类"按报告期精确匹配"的接口。
    实测: 该接口的 end_date 参数是**精确匹配报告期**, 不是日期区间;
          传 trade_date 会被静默忽略并返回被上限截断的全表前N行。
    A股报告期固定为 0331 / 0630 / 0930 / 1231。
    """
    end = end or datetime.now().strftime("%Y%m%d")
    y0, y1 = int(str(start)[:4]), int(str(end)[:4])
    out = []
    for y in range(y0, y1 + 1):
        for md in ("0331", "0630", "0930", "1231"):
            d = f"{y}{md}"
            if str(start) <= d <= str(end):
                out.append(d)
    return out


def truncate_to_today(dates):
    """把日期列表截断到"今天"，剔除交易日历里的未来交易日。

    交易日历 trade_cal 覆盖到未来(实测到 20271231), 不截断会导致:
      - 白跑数百次请求(未来日期 Tushare 返回 None)
      - 缺口分析算出"缺口到 2027 年"的假象
    """
    today = datetime.now().strftime("%Y%m%d")
    return [d for d in dates if str(d) <= today]


# 兼容旧名（内部曾用 _truncate_to_today）
_truncate_to_today = truncate_to_today


# ============================================================
# 三、任务构建与估算
# ============================================================
def build_tasks(name, conf, dry_run=False):
    """构建该 target 的任务清单。

    返回 (tasks, kind)
      kind="code"   → tasks = [code, ...]
      kind="date"   → tasks = [交易日, ...]
      kind="range"  → tasks = [(起始日, 结束日), ...]
      kind="period" → tasks = [报告期, ...]
      kind="month"  → tasks = [(code_or_None, 月末日), ...]
      kind="once"   → tasks = [None]
      kind="param"  → tasks = [枚举值, ...]（如交易所代码）
      kind="paged"  → tasks = [页码, ...]（0,1,2..., 按 limit/offset 遍历）
      kind="need_meta" → tasks = None（需先拉前置基础表）
    """
    mode = conf["mode"]

    if mode == "by_param":
        return list(conf.get("param_values", [])), "param"

    if mode == "paged":
        # 按 limit/offset 遍历的全量元数据表：任务 = 页码序列
        #   页数按实测总量 / page_size 推算（page_count 显式给出更稳妥）
        n = conf.get("page_count")
        if not n:
            rows = DAILY_ROWS.get(conf["api"], 0)
            page = conf.get("page_size", 5000)
            n = max(1, -(-rows // page)) if rows else 10
        return list(range(n)), "paged"

    if mode == "by_code":
        codes = resolve_codes(conf.get("codes"))
        if codes is None:
            return None, "need_meta"          # 需先拉基础信息表
        return codes, "code"

    if mode == "by_date":
        # ⚠ 必须按"今天"截断（见模块 docstring 纪律 1）
        # freq 决定日期粒度: day=每个交易日(默认) / week=每周末 / month=每月末
        #   —— 后两者给"周线/月线"这类按周/月聚合的接口用
        #   （它们只需拉周末/月末那个交易日，不是每一天）
        # calendar 决定用哪套交易日历（2026-09-19 新增）:
        #   "SSE"(默认) → A 股日历；"us" → 美股日历（us_tradecal）
        #   ⚠ 不能默认用 A 股日历拉美股：实测 2026-09 A股 21 日 vs 美股 13 日，
        #     错用会多拉 8 天（全部返空、浪费请求）并可能漏掉真实的交易日。
        start = conf.get("start", "20160101")
        freq = conf.get("freq", "day")
        cal = conf.get("calendar", "SSE")
        if freq == "week":
            return truncate_to_today(week_last_trade_dates(
                start, calendar=cal,
                anchor=conf.get("week_anchor", "last_trade"))), "date"
        if freq == "month":
            return truncate_to_today(
                [d for _, d in month_last_trade_dates(start, calendar=cal)]), "date"
        return truncate_to_today(open_dates_by_market(cal, start_date=start)), "date"

    if mode == "by_range":
        start = conf.get("start", "20160101")
        if conf.get("single_range"):
            # 数据量小（< 2000 行服务端上限）的接口一次拉全。
            # 实证动机: shibor_lpr 限频 **1次/小时**，按年切 11 段需 11 小时；
            #           而 LPR 全历史约 1500 行，单次即可拿完。
            return [(start, datetime.now().strftime("%Y%m%d"))], "range"
        return year_ranges(start), "range"

    if mode == "by_period":
        start = conf.get("start", "20160101")
        return report_periods(start), "period"

    if mode == "by_month":
        start = conf.get("start", "20160101")
        codes = resolve_codes(conf.get("codes"))
        if codes is None:
            return None, "need_meta"
        pairs = month_last_trade_dates(start, calendar=conf.get("calendar", "SSE"))
        if not codes:
            # broker_recommend 型: 只需月份
            return [(None, m) for m, _ in pairs], "month"
        return [(c, d) for c in codes for _, d in pairs], "month"

    if mode == "once":
        return [None], "once"

    return [], "unknown"


# 实测单日行数（用于推算分页数；来自各接口的真实探测）
DAILY_ROWS = {
    "index_daily": 11000, "fut_holding": 10650, "opt_daily": 25800,
    "fut_wsr": 1200, "repo_daily": 45, "moneyflow": 3000,
    "margin_detail": 1200, "margin_secs": 500, "top_inst": 800,
    "block_trade": 200, "hk_hold": 2200, "suspend_d": 90,
    "fut_daily": 340, "fut_mapping": 200,
    # by_param 类的实测总量(用于推算页数)
    "fut_basic": 11275, "opt_basic": 30730,
    # 单日多页类（用于推算 by_date 的页数）
    "bc_otcqt": 8160,      # 单日约 8160 行 → 2000/页 = 5 页
    # C 档其余表单日行数（均 < 50，1 页足够，列出便于估算换算）
    "fund_div": 40, "sge_daily": 41, "daily_info": 11, "sz_daily_info": 14,
    "eco_cal": 50,
}


def estimate_requests(tasks, conf):
    """估算请求数。

    by_date 类若在分页表中, 按该接口的"每页行数"与"实测单日行数"推算页数。
    by_param 类按"实测总量/页大小"推算总页数(含各枚举值的收尾空调)。
    页数依据 config.BACKFILL_PAGE_CONF 与 DAILY_ROWS, 不硬编码。
    """
    n = len(tasks)
    api = conf["api"]

    if conf.get("mode") == "paged":
        # 每页就是一次请求
        return n if n else len(tasks)

    if conf.get("mode") == "by_param":
        # 每枚举值的页数按实测总量均分 + 每个值一次收尾调用
        page = conf.get("page_size", 2000)
        rows = DAILY_ROWS.get(api, 0)
        if rows and n:
            pages_total = -(-rows // page)      # 向上取整
            return pages_total + n              # + 收尾
        return n

    if api in BACKFILL_PAGE_CONF:
        page, _ = BACKFILL_PAGE_CONF[api]
        rows = DAILY_ROWS.get(api, page)        # 未知则按 1 页
        pages = max(1, -(-rows // page))        # 向上取整
        n = n * pages
    return n


def expected_task_count(name, conf, today=None):
    """按"应有任务数"口径返回 (expect_list, mode)，供体检(dbg-audit)缺口分析复用。

    与 build_tasks 的差别: 此处确保结果可复现（不因 dry_run 差异而变），
    并统一把 by_range 的当年端点处理为"今天"。
    """
    mode = conf["mode"]
    today = today or datetime.now().strftime("%Y%m%d")

    if mode == "by_date":
        start = conf.get("start", "20160101")
        cal = conf.get("calendar", "SSE")
        freq = conf.get("freq", "day")
        if freq == "week":
            # ⚠ 必须与 build_tasks 同口径（否则 R12 缺口分析会漂移）
            return truncate_to_today(week_last_trade_dates(
                start, calendar=cal,
                anchor=conf.get("week_anchor", "last_trade"))), "date"
        if freq == "month":
            return truncate_to_today(
                [d for _, d in month_last_trade_dates(start, calendar=cal)]), "date"
        return truncate_to_today(open_dates_by_market(cal, start_date=start)), "date"

    if mode == "by_range":
        # ⚠ 当年端点用"今天"（见模块 docstring 纪律 2）
        start = conf.get("start", "20160101")
        if conf.get("single_range"):
            # ⚠ 必须与 build_tasks 同口径（否则 R12 缺口分析会漂移）
            return [f"{start}|{today}"], "range"
        y0, yt = int(start[:4]), int(today[:4])
        out = []
        for y in range(y0, yt + 1):
            s = f"{y}0101"
            e = today if y == yt else f"{y}1231"
            out.append(f"{s}|{e}")
        return out, "range"

    if mode == "by_period":
        start = conf.get("start", "20160101")
        return [d for d in report_periods(start) if d <= today], "period"

    # by_code / by_month / once 交给 build_tasks（它们不依赖日期截断）
    tasks, kind = build_tasks(name, conf, dry_run=True)
    if kind == "need_meta" or tasks is None:
        return None, kind
    if kind == "month":
        return [f"{t[0]}|{t[1]}" for t in tasks], kind
    if kind == "code":
        return [str(t) for t in tasks], kind
    if kind == "once":
        return ["once"], kind
    if kind == "param":
        return [str(t) for t in tasks], kind
    if kind == "paged":
        return [str(t) for t in tasks], kind
    return tasks, kind
