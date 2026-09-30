# -*- coding: utf-8 -*-
"""
tools/db_schema.py — 数据库结构扫描器
======================================
扫描全量库全部目录，输出**机器可读的 schema JSON**（逐表字段类型/行数/日期范围/主键）。
供《数据库结构报告》引用，也供 AI 智能体直接消费。

与其他库维护工具的配合:
  tools/db_audit.py    体检（断点/重复/缺口/规模）—— 关注"健康度"
  tools/db_schema.py   结构扫描（本文件）          —— 关注"有哪些数据、怎么读"
  tools/db_clean.py    清理（版本冗余压缩）        —— 关注"修正"

行数统计用 `pyarrow.parquet` 元数据精确读取（不读数据本体），
比 pandas 快几个数量级；全部 57 表 / 15 万文件约 6 分钟。

用法:
  python main.py db-schema                      # 扫描并写 文档/数据与工程/db_schema.json
  python main.py db-schema --out PATH           # 指定输出路径
  python main.py db-schema --quiet              # 只写文件，不打印明细
"""
import os
import json
import argparse
from datetime import datetime

import pandas as pd

from config import (
    MARKET_DATA_DIR, META_DIR, DOC_DIR, BACKFILL_TARGETS,
    DB_AUDIT_PKEYS, API_RATE_LIMIT, BACKFILL_PAGE_CONF,
)

# 默认输出：文档/数据与工程/db_schema.json（与结构报告同目录）
DEFAULT_OUT = os.path.join(DOC_DIR, "数据与工程", "db_schema.json")

# ── 各表的语义元信息（人工维护；报告里最有价值的部分）──
# 格式: 表名 -> (类别, 中文说明, 注意事项)
SEMANTICS = {
    # ---- 元数据 ----
    "metadata":        ("元数据", "证券清单与交易日历",
                        "stock_basic/fund_basic/cb_basic/index_basic/trade_cal/hk_basic/fx_obasic"),
    "hk_basic":        ("元数据", "港股基础信息", "港股清单"),
    "fx_obasic":       ("元数据", "外汇基础信息", ""),
    # ---- 行情 ----
    "daily":           ("行情", "个股日线（不复权）", "OHLC/成交量额；配 adj_factor 复权"),
    "daily_basic":     ("行情", "个股每日指标", "PE/PB/PS/股息率/换手率/总市值/流通市值"),
    "fund_daily":      ("行情", "ETF/LOF 日线", "仅场内；场外 .OF 无此数据"),
    "fund_nav":        ("行情", "基金净值", "场外 .OF + 场内 ETF/LOF"),
    "index_daily":     ("行情", "指数日线", "含沪深300/中证系列/申万等"),
    "cb_daily":        ("行情", "可转债日线", "转债行情；配 cb_basic"),
    "adj_factor":      ("行情", "复权因子", "与 daily 相乘得前/后复权价"),
    "stk_limit":       ("行情", "每日涨跌停价", "含主板/创业板/科创板"),
    "fx_daily":        ("行情", "外汇日线", "含 BRLCNY（巴西ETF证伪条件用）"),
    "fund_adj":        ("行情", "基金复权因子", "宽基ETF轮动必须用复权价"),
    # ---- 财务 ----
    "income":          ("财务", "利润表", "多版本由 report_type 区分（合并/单季/调整）"),
    "balancesheet":    ("财务", "资产负债表", "同上；152列，只取需要列可提速"),
    "cashflow":        ("财务", "现金流量表", "同上"),
    "fina_indicator":  ("财务", "财务指标", "108列；含 ROE/毛利率/自由现金流 fcff"),
    "fina_mainbz":     ("财务", "主营业务构成", "分部收入/成本/利润"),
    "fina_audit":      ("财务", "财务审计意见", "非标意见=红旗"),
    "forecast":        ("财务", "业绩预告", "涛涛战法业绩雷规避用"),
    "express":         ("财务", "业绩快报", ""),
    "dividend":        ("财务", "分红送股", "div_proc 有9种进度状态，同报告期天然多行"),
    "disclosure_date": ("财务", "财报披露日期表", "用 pre_date/actual_date；ann_date 是占位值"),
    # ---- 资金面 ----
    "margin":          ("资金", "融资融券汇总", "按交易所；单日2行"),
    "margin_detail":   ("资金", "融资融券明细", "按标的"),
    "margin_secs":     ("资金", "两融标的清单", "哪些标的可两融"),
    "moneyflow":       ("资金", "个股资金流向", "大中小单买卖量额"),
    "hk_hold":         ("资金", "沪深股通持股明细", "北向持仓；港股通休市日无数据"),
    "hsgt_top10":      ("资金", "陆股通十大成交股", "外资当日偏好"),
    "ggt_top10":       ("资金", "港股通十大成交股", ""),
    "fund_share":      ("资金", "基金份额规模", "汇金ETF化趋势判断口径"),
    # ---- 事件面 ----
    "top_list":        ("事件", "龙虎榜每日统计", "同股同日可多行（多上榜原因）"),
    "top_inst":        ("事件", "龙虎榜机构席位", "同股同日多营业部，单日最多40行"),
    "block_trade":     ("事件", "大宗交易", "折价率/买卖方；上游自带重复"),
    "suspend_d":       ("事件", "每日停复牌", ""),
    "new_share":       ("事件", "IPO新股上市", "次新规避用；⚠无trade_date列"),
    "namechange":      ("事件", "股票曾用名", "ST/借壳识别"),
    "share_float":     ("事件", "限售股解禁", "解禁压力预判"),
    "stk_holdernumber":("事件", "股东户数", "筹码集中度代理"),
    "stk_holdertrade": ("事件", "股东增减持", ""),
    "pledge_stat":     ("事件", "股权质押统计", "爆仓风险"),
    "repurchase":      ("事件", "股票回购", ""),
    "broker_recommend":("事件", "券商月度金股", "按 month 参数拉"),
    "stock_company":   ("事件", "公司基本信息", ""),
    # ---- 指数衍生 ----
    "index_weight":    ("指数衍生", "指数成分与权重", "按月；单月多成分股"),
    "index_dailybasic":("指数衍生", "指数每日指标", "PE/PB/股息率——奇点估值锚点数据源"),
    "market_state":    ("指数衍生", "市场状态", "赤潮派生；按年分文件"),
    "custom_index":    ("指数衍生", "自建指数", "micro-index 产出的微盘指数"),
    # ---- 跨品种 ----
    "fut_daily":       ("跨品种", "期货日线", "IF/IC/IM 等"),
    "cb_issue":        ("跨品种", "可转债发行", ""),
    "cb_share":        ("跨品种", "可转债份额", "剩余规模"),
    "cb_rating":       ("跨品种", "可转债评级", ""),
    "repo_daily":      ("跨品种", "债券回购日行情", ""),
    "shibor":          ("跨品种", "Shibor", "⚠区间参数，非trade_date"),
    "us_tycr":         ("跨品种", "美国国债收益率", "⚠区间参数"),
    # ---- 宏观 ----
    "cn_cpi":          ("宏观", "CPI", ""),
    "cn_ppi":          ("宏观", "PPI", ""),
    "cn_m":            ("宏观", "货币供应", ""),
    "cn_pmi":          ("宏观", "PMI", "65列，含分项"),
    "cn_gdp":          ("宏观", "GDP", ""),
    "sf_month":        ("宏观", "社融", ""),
    "gz_index":        ("宏观", "广州民间利率", "tier C 默认不跑"),
    "wz_index":        ("宏观", "温州民间利率", "tier C 默认不跑"),
}

DATE_COL_PREF = ["trade_date", "date", "nav_date", "end_date", "ann_date",
                 "ipo_date", "float_date", "rating_date", "publish_date",
                 "month", "start_date", "cal_date"]


# ⚠ 布局判定已收敛（2026-09-19）：
#   此前本文件用硬编码后缀表（CODE_SUFFIXES）自行判定，与 reader.detect_layout /
#   backfill_io.table_storage_layout 构成**三套独立实现**，其中只有本套无断言守卫。
#   风险：一旦出现后缀表未覆盖的代码格式，本处会静默误判，导致《数据库结构报告》
#   失真 —— 而该报告正是人工判断库状态的依据。
#   现统一改调 reader.inspect_layout()：单一真源，且能识别"混合布局"这一异常状态。


def scan(root=None, verbose=True):
    """扫描全库，返回 schema 字典。"""
    import pyarrow.parquet as pq
    root = root or MARKET_DATA_DIR

    schema = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "root": root,
        "total_dirs": 0,
        "tables": {},
    }
    tot_files = tot_bytes = tot_rows = 0

    dirs = sorted(e.name for e in os.scandir(root)
                  if e.is_dir() and not e.name.startswith("."))
    schema["total_dirs"] = len(dirs)

    if verbose:
        print("=" * 92)
        print("  数据库结构扫描")
        print("=" * 92)

    for name in dirs:
        d = os.path.join(root, name)
        files = sorted(f for f in os.listdir(d) if f.endswith(".parquet"))
        if not files:
            continue

        # 采样中间那个文件读列结构（避开首尾差异）
        samp = files[len(files) // 2]
        try:
            df = pd.read_parquet(os.path.join(d, samp))
        except Exception as ex:
            if verbose:
                print(f"  [{name}] 读取失败: {str(ex)[:50]}")
            continue

        # 行数 + 大小：pyarrow 元数据精确读取（不读数据本体）
        rows = 0
        size = 0
        for f in files:
            p = os.path.join(d, f)
            try:
                size += os.path.getsize(p)
            except OSError:
                pass
            try:
                rows += pq.ParquetFile(p).metadata.num_rows
            except Exception:
                pass

        # 布局：单一真源（reader），并识别混合布局异常
        try:
            from common import reader
            info = reader.inspect_layout(name, root=root)
            layout = info["display"]
            npart = info["npart"]
            if info["is_mixed"]:
                layout = (f"mixed(年度{info['year_files']}+其他"
                          f"{info['files'] - info['year_files']})")
        except Exception:
            layout, npart = "unknown", len(files)

        # 日期列与范围
        # ⚠ 口径统一（2026-09-19）：dmin/dmax 与 unique_dates 均**抽样**计算。
        #   此前 unique_dates 把全部文件的日期列读一遍（daily 37 文件 0.59s），
        #   而 dmin/dmax 只采样 6 个文件（0.09s）—— 同段代码两种口径，
        #   且前者是 db-schema 耗时的主要来源。改为统一抽样，字段名加 _sampled 标注。
        date_col = next((c for c in DATE_COL_PREF if c in df.columns), None)
        dmin = dmax = None
        ndays = 0
        SAMP_N = 6
        if date_col:
            try:
                samp = files[:3] + files[-3:] if len(files) > SAMP_N else files
                us = set()
                for f in samp:
                    s = pd.read_parquet(os.path.join(d, f), columns=[date_col])[date_col]
                    us.update(s.astype(str).unique())
                if us:
                    dmin, dmax = min(us), max(us)
                    ndays = len(us)
            except Exception:
                pass

        cat, desc, note = SEMANTICS.get(name, ("未分类", "", ""))
        schema["tables"][name] = {
            "category": cat,
            "description": desc,
            "note": note,
            "layout": layout,
            "partitions": npart,
            "files": len(files),
            "rows": rows,
            "size_mb": round(size / 1024 / 1024, 2),
            "date_col": date_col,
            "date_min": dmin,
            "date_max": dmax,
            "unique_dates": ndays,
            "date_sampled": len(files) > SAMP_N,
            "date_sample_files": min(len(files), SAMP_N),
            "columns": [{"name": c, "dtype": str(t)}
                        for c, t in zip(df.columns, df.dtypes)],
            "n_columns": len(df.columns),
            "pk": DB_AUDIT_PKEYS.get(name),
            "sample_file": samp,
        }
        tot_files += len(files)
        tot_bytes += size
        tot_rows += rows

        if verbose:
            print(f"  {name:20s} {len(files):6,d}文件 {rows:>13,d}行 "
                  f"{size/1024/1024:>8.1f}MB {layout:9s} {len(df.columns):3d}列")

    schema.update({
        "total_files": tot_files,
        "total_bytes": tot_bytes,
        "total_rows": tot_rows,
        "total_gb": round(tot_bytes / 1024 ** 3, 2),
        "config": {
            "api_rate_limit": API_RATE_LIMIT,
            "page_conf": {k: list(v) for k, v in BACKFILL_PAGE_CONF.items()},
            "backfill_modes": {k: v["mode"] for k, v in BACKFILL_TARGETS.items()},
        },
    })

    if verbose:
        print("=" * 92)
        print(f"  目录 {len(schema['tables'])} | 文件 {tot_files:,} | "
              f"行 {tot_rows:,} | {tot_bytes/1024**3:.2f} GB")
    return schema


def run_schema(out=None, verbose=True, quiet=False, as_json=False, announce=True):
    """扫描并写出 JSON。

    as_json=True: stdout 输出回执 JSON（schema 文件照写；CLI --json）
    announce=False: 静音"已保存"回执（供 db-report --refresh 内部调用）
    """
    out = out or DEFAULT_OUT
    schema = scan(verbose=verbose and not quiet and not as_json)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    json.dump(schema, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if as_json:
        from common.jsonio import print_json
        print_json({"out": out, "tables": len(schema["tables"]),
                    "total_files": schema["total_files"],
                    "total_rows": schema["total_rows"],
                    "total_gb": schema["total_gb"]})
    elif announce:
        print(f"\n已保存: {out}")
        print(f"  {len(schema['tables'])} 表 / {schema['total_files']:,} 文件 / "
              f"{schema['total_rows']:,} 行 / {schema['total_gb']} GB")
    return schema


def main(argv=None):
    ap = argparse.ArgumentParser(description="数据库结构扫描（输出机器可读 schema JSON）")
    ap.add_argument("--out", default=None, help=f"输出路径（默认 {DEFAULT_OUT}）")
    ap.add_argument("--quiet", action="store_true", help="只写文件，不打印明细")
    ap.add_argument("--json", action="store_true", help="stdout 输出回执 JSON（文件照写）")
    a = ap.parse_args(argv)
    run_schema(out=a.out, verbose=not a.quiet, quiet=a.quiet, as_json=a.json)


if __name__ == "__main__":
    main()
