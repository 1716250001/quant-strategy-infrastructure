# -*- coding: utf-8 -*-
"""
tools/consumer_coverage.py — 决策侧消费标的覆盖体检（2026-09-25 新增）
========================================================================
为什么需要它（511180/511380 教训）：
  2026-09-25 发现两只 ETF 在 fund_daily 年表内仅有 29 行——当年 by_code 建库时
  标的清单漏了它们，**表级日期覆盖检查（freshness/check_coverage）完全测不出
  这种"单标的空洞"**，直到 qidian 扫描降级才暴露。本工具按「决策侧实际消费的
  标的清单」逐标的验证行数与日期范围，补上这个盲区。

消费清单（与运行拓扑对齐）：
  1. A2 奇点扫描：QIDIAN_POOL 全部 ETF(fund_daily) + 池内指数(index_daily)
     + XAU_WATCHLIST(fx_daily) + 转债温度计(wind_index，已知停更→INFO)
  2. 作战包固定消费：L0 两指数 / 中证银行(触发位) / HS300(估值定锚)
  3. 作战包动态消费：rules/current-holdings.md 持仓标的（股票→daily，ETF→fund_daily）

判定：
  - 行数 ≥ min_rows（默认 250 ≈ 1 年）→ PASS
  - 起始日 ≥ 550 天前（次新品，起始=上市日即完备）→ PASS(次新)
  - 其余 → WARN（人工核对：真缺口 or 上市晚）
  - 温度计 wind_index 停更为已知决议（2026-09-25 Q-G 观察级）→ INFO

用法:
  python main.py check-consumer              # 体检（退出码 0=全过 / 2=有 WARN）
"""
import os
import re
import sys

import pandas as pd
import pyarrow.parquet as pq

MARKET_DATA_DIR = os.environ.get("MARKET_DATA_DIR", r"D:\全量数据\market_data")
HOLDINGS_FP = r"D:\量化策略\赤潮\rules\current-holdings.md"

# 次新判定：起始日在该天数内的标的视为次新（起始=上市日即完备，需人工确认上市日）
RECENT_DAYS = 550
MIN_ROWS = 250


def _rows(table, code):
    d = os.path.join(MARKET_DATA_DIR, table)
    if not os.path.isdir(d):
        return 0, None, None
    total, dmin, dmax = 0, None, None
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".parquet"):
            continue
        try:
            tbl = pq.read_table(os.path.join(d, fn),
                                filters=[("ts_code", "=", code)],
                                columns=["trade_date"])
        except Exception:
            continue
        if tbl.num_rows:
            s = tbl.to_pandas()["trade_date"].astype(str)
            total += len(s)
            dmin = s.min() if dmin is None else min(dmin, s.min())
            dmax = s.max() if dmax is None else max(dmax, s.max())
    return total, dmin, dmax


def _today():
    return pd.Timestamp.now().strftime("%Y%m%d")


def _classify(table, code, name, min_rows, kind="normal"):
    n, dmin, dmax = _rows(table, code)
    if kind == "wind":
        return {"table": table, "code": code, "name": name, "rows": n,
                "range": f"{dmin}~{dmax}", "status": "INFO",
                "note": "wind_index 停更为已知决议(Q-G 观察级)"}
    if n >= min_rows:
        status, note = "PASS", ""
    elif dmin and (pd.Timestamp(_today()) - pd.Timestamp(dmin)).days <= RECENT_DAYS:
        status, note = "PASS", f"次新（起始 {dmin}≈上市日，{n} 行为上市以来全量）"
    else:
        status, note = "WARN", f"仅 {n} 行（阈值 {min_rows}）——需人工核对真缺口/上市晚"
    return {"table": table, "code": code, "name": name, "rows": n,
            "range": f"{dmin}~{dmax}", "status": status, "note": note}


def load_consumers():
    """组装消费清单 [(table, code, name, min_rows, kind), ...]。"""
    items = []
    sys.path.insert(0, r"D:\量化策略\代码")
    from strategies.qidian import QIDIAN_POOL, XAU_WATCHLIST

    # 1) A2 池
    for etf, info in QIDIAN_POOL.items():
        items.append(("fund_daily", etf, info["name"], MIN_ROWS))
    for ix in sorted({i["code"] for info in QIDIAN_POOL.values() for i in info["indices"]}):
        items.append(("index_daily", ix, "", MIN_ROWS))
    for fx, info in XAU_WATCHLIST.items():
        nm = info["name"] if isinstance(info, dict) else str(info)
        items.append(("fx_daily", fx, nm, 2500))

    # 2) 作战包固定消费
    items += [
        ("index_daily", "000001.SH", "上证(L0)", 4000),
        ("index_daily", "932000.CSI", "中证2000(L0)", 2000),
        ("index_daily", "399986.SZ", "中证银行(触发位)", 1250),
        ("index_dailybasic", "000300.SH", "HS300(估值定锚)", 2000),
    ]

    # 3) 转债温度计（wind，INFO 口径）
    try:
        from strategies.qidian_daily_scan import WIND_CB_THERMOMETER
        for c, info in WIND_CB_THERMOMETER.items():
            items.append(("wind_index", c, info["name"], 0, "wind"))
    except Exception:
        pass

    # 4) 持仓标的（单一事实源 rules/current-holdings.md）
    if os.path.isfile(HOLDINGS_FP):
        text = open(HOLDINGS_FP, encoding="utf-8").read()
        codes = sorted(set(re.findall(r"\b(\d{6}\.(?:SH|SZ))\b", text)))
        for c in codes:
            head = c[:6]
            if re.match(r"(51|56|58)\d{4}", head) or head.startswith("159") or head.startswith("501"):
                table = "fund_daily"        # 场内基金段：51x/56x/58x/159xxx/501xxx
            elif head.startswith("399") or head.startswith("880"):
                table = "index_daily"       # 深证指数段 399xxx（如中证银行 399986.SZ 触发位监控）
            else:
                table = "daily"             # 股票（60x/00x/30x/68x/8xx 北交所）
            items.append((table, c, "持仓", MIN_ROWS))
    return items


def main():
    print("=" * 76)
    print("  决策侧消费标的覆盖体检（A2 池 + 作战包 + 持仓）")
    print(f"  库根: {MARKET_DATA_DIR} | 基准日: {_today()}")
    print("=" * 76)

    items = load_consumers()
    results = []
    for it in items:
        table, code = it[0], it[1]
        name = it[2] if len(it) > 2 else ""
        min_rows = it[3] if len(it) > 3 else MIN_ROWS
        kind = it[4] if len(it) > 4 else "normal"
        results.append(_classify(table, code, name, min_rows, kind))

    n_pass = sum(1 for r in results if r["status"] == "PASS")
    n_info = sum(1 for r in results if r["status"] == "INFO")
    n_warn = sum(1 for r in results if r["status"] == "WARN")

    print(f"\n  {'状态':6s} {'表':16s} {'代码':12s} {'名称':18s} {'行数':>7s}  范围/备注")
    print("  " + "-" * 72)
    for r in results:
        mark = {"PASS": "✓", "INFO": "ℹ", "WARN": "⚠"}[r["status"]]
        tail = r["note"] or r["range"]
        nm = r["name"][:8] if r["name"] else ""
        print(f"  {mark} {r['table']:16s} {r['code']:12s} {nm:18s} {r['rows']:7d}  {tail}")

    print("  " + "-" * 72)
    print(f"  合计 {len(results)} 项: PASS {n_pass} | INFO {n_info} | WARN {n_warn}")
    if n_warn:
        print("  ⚠ WARN 项需人工核对（真缺口 → backfill/单标的补；上市晚 → 忽略）")
        return 2
    print("  结论: 消费标的覆盖完备 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
