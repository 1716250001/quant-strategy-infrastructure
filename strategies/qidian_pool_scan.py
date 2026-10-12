# -*- coding: utf-8 -*-
"""qidian_pool_scan.py — 奇点战法 · A 类观察池信号扫描（新池）

池来源：文档/策略体系/奇点投资框架/奇点观察池A类配置.json
    A1 池（8 指数）：自然起点与 2016 起点两口径都入池 + 大比例产品达标
    A2 池（17 指数）：仅 2016 口径入池 + 大比例产品达标
    合计 25 指数 / 54 ETF（B 类已剔除；300SNLV 因源头无 OHLC 剔除）

四维声明（老大 2026-10-01 要求，每条推送必须标注）：
    1 方向　　：买入 / 卖出
    2 触发源　：仅指数触发 / 仅ETF触发 / 指数-ETF双重触发
    3 指标源　：仅CCI触发 / 仅KDJ触发 / CCI+KDJ双重触发
    4 池别　　：A1 池 / A2 池

关键口径：
    - ETF 价格一律经 tushare fund_adj 前复权（消除份额折算假跳变；
      本地缓存 D:/量化策略/数据/fund_adj，在线优先、缓存回退）
    - 指标引擎用 strategies.qidian_engine，与全量回测（btf `strategy/qidian.py`）
      **口径一致、容差级等价**——依据两条（2026-10-11 三轮架构审查 H-05 订正，
        原文「与全量回测口径逐位一致，已验证 diff=0」属**过强表述**）：
        ① 一次性**人工对账**：研究报告记录 diff=0.00000（**5 位显示精度**，
           非"逐位"，且为一次性动作、非常驻测试）；
        ② 两侧浮点写法存在 IEEE754 末位差（1 ulp 量级），故口径等价性应按
           **数值容差**陈述；btf 侧常驻测试取容差 **1e-9**
           （见 `tests/integration/test_qidian_l3.py` 等锚点）。
      ⇒ 交易信号级一致（差异远小于阈值判定的影响），但**不得**据此宣称"逐位相同"。
    - 交易日闸门：非交易日不扫描、不推送

用法：
    python -m strategies.qidian_pool_scan                 # 扫描+推送
    python -m strategies.qidian_pool_scan --dry-run       # 仅扫描不推送
    python -m strategies.qidian_pool_scan --date 20260930 # 指定日期（绕过闸门）
    python -m strategies.qidian_pool_scan --force         # 强制运行（非交易日也跑）
"""
import argparse
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from common.paths import DOC_DIR
from config import PUSHPLUS_TOKEN, PUSHPLUS_CHANNEL, PUSHPLUS_TEMPLATE
from fetch.base import get_pro, get_latest_trade_date, is_trading_day
from strategies.qidian import (
    fetch_etf_daily, fetch_index_daily, aggregate_to_weekly,
    BUY_J_THRESHOLD, BUY_CCI_THRESHOLD, SELL_J_THRESHOLD, SELL_CCI_THRESHOLD,
    KDJ_N, KDJ_M1, KDJ_M2, CCI_N,
)
from strategies.qidian_engine import calc_kdj, calc_cci

POOL_JSON = Path(DOC_DIR) / "策略体系" / "奇点投资框架" / "奇点观察池A类配置.json"
ADJ_CACHE = Path(r"D:\量化策略\数据\fund_adj")


# ============================================================
# 池配置
# ============================================================
def audit_pool_listing(cfg: dict) -> list:
    """核验池内 ETF 的上市状态，返回非在市清单。

    背景（2026-10-09 教训）：池构建基于历史回测，未检查"当前是否还能买到"——
    结果 A1 的 CS京津冀 唯一 ETF（512780.SH，2023-01-12 退市）已消失却仍在池内。
    这与幸存者偏差方向相反：用了"已消失的标的"。故增设本检查。
    """
    from fetch.base import get_pro
    pro = get_pro()
    bad = []
    for k in ("A1", "A2"):
        for x in cfg.get(k, []):
            for e in x.get("etfs", []):
                try:
                    d = pro.fund_basic(ts_code=e)
                    if d is None or not len(d):
                        bad.append((k, x["index_name"], e, "查询无记录", ""))
                        continue
                    r = d.iloc[0]
                    if str(r.get("status")) != "L":
                        bad.append((k, x["index_name"], e,
                                    f"status={r.get('status')}",
                                    str(r.get("delist_date") or "")))
                except Exception as ex:
                    bad.append((k, x["index_name"], e, f"查询异常:{str(ex)[:30]}", ""))
                time.sleep(0.12)
    return bad


def load_pool() -> dict:
    if not POOL_JSON.exists():
        raise FileNotFoundError(f"池配置不存在: {POOL_JSON}")
    cfg = json.loads(POOL_JSON.read_text(encoding="utf-8"))
    return cfg


# ============================================================
# ETF 复权
# ============================================================
def fetch_fund_adj(code: str, end_date: str, lookback_days: int = 4200):
    """取 ETF 复权因子（在线优先，本地缓存回退）。"""
    start = (datetime.strptime(end_date, "%Y%m%d")
             - timedelta(days=lookback_days)).strftime("%Y%m%d")
    try:
        pro = get_pro()
        d = pro.fund_adj(ts_code=code, start_date=start, end_date=end_date)
        if d is not None and len(d):
            d = d.copy()
            d["trade_date"] = d["trade_date"].astype(str)
            return d.sort_values("trade_date")
    except Exception as e:
        print(f"    [WARN] fund_adj 在线获取失败 {code}: {e}")
    f = ADJ_CACHE / f"{code}.csv"
    if f.exists():
        d = pd.read_csv(f, dtype={"trade_date": str, "adj_factor": float})
        return d.sort_values("trade_date")
    return None


def apply_adj(df: pd.DataFrame, code: str, end_date: str):
    """ETF 前复权：adj_price = price × adj_factor ÷ adj_factor_latest。

    返回 (DataFrame, 是否成功复权)。失败时原样返回并标记，供消息中提示。
    """
    adj = fetch_fund_adj(code, end_date)
    if adj is None or not len(adj):
        return df, False
    last = float(adj["adj_factor"].iloc[-1])
    if last == 0:
        return df, False
    ratio_map = dict(zip(adj["trade_date"], adj["adj_factor"] / last))
    r = df["trade_date"].astype(str).map(ratio_map).astype("float64")
    r = r.bfill().ffill().fillna(1.0).values
    out = df.copy()
    for col in ("open", "high", "low", "close"):
        if col in out.columns:
            out[col] = out[col].values * r
    return out, True


# ============================================================
# 周线指标
# ============================================================
def weekly_metrics(daily: pd.DataFrame):
    """日线 → 周线 → 最新一周的 J / CCI / 周区间。

    OHLC 缺失（部分中证指数早期只发布收盘点位）时，用该周 close 回填，
    避免 KDJ 的 K/D 递推被 NaN 断链。
    """
    if daily is None or daily.empty or len(daily) < 30:
        return None
    wk = aggregate_to_weekly(daily)
    if wk is None or wk.empty or len(wk) < 25:
        return None
    o = wk["open"].values.astype(float)
    h = wk["high"].values.astype(float)
    l = wk["low"].values.astype(float)
    c = wk["close"].values.astype(float)
    if np.isnan(c).any():
        idx = np.where(~np.isnan(c))[0]
        if not len(idx):
            return None
        c = np.interp(np.arange(len(c)), idx, c[idx])
    for arr in (o, h, l):
        m = np.isnan(arr)
        if m.any():
            arr[m] = c[m]
    j = float(calc_kdj(h, l, c, n=KDJ_N, m1=KDJ_M1, m2=KDJ_M2)[-1])
    cci = float(calc_cci(h, l, c, n=CCI_N)[-1])
    last_wk = str(wk["week_end_date"].iloc[-1]) if "week_end_date" in wk.columns else ""
    return {"J": j, "CCI": cci, "close": float(c[-1]), "week": last_wk,
            "n_weeks": len(c)}


# ============================================================
# 信号判定
# ============================================================
def judge(j: float, cci: float):
    """返回 (方向, 指标源)。方向 None = 无信号。"""
    buy = (j <= BUY_J_THRESHOLD) or (cci <= BUY_CCI_THRESHOLD)
    sell = (j >= SELL_J_THRESHOLD) or (cci >= SELL_CCI_THRESHOLD)
    if buy:
        direction = "买入"
    elif sell:
        direction = "卖出"
    else:
        return None, None

    kdj_hit = (j <= BUY_J_THRESHOLD) if direction == "买入" else (j >= SELL_J_THRESHOLD)
    cci_hit = (cci <= BUY_CCI_THRESHOLD) if direction == "买入" else (cci >= SELL_CCI_THRESHOLD)
    if kdj_hit and cci_hit:
        src = "CCI+KDJ双重触发"
    elif kdj_hit:
        src = "仅KDJ触发"
    else:
        src = "仅CCI触发"
    return direction, src


def merge_indicator(srcs: set) -> str:
    if not srcs:
        return "—"
    if "CCI+KDJ双重触发" in srcs:
        return "CCI+KDJ双重触发"
    has_c = any("CCI" in s for s in srcs)
    has_k = any("KDJ" in s for s in srcs)
    if has_c and has_k:
        return "CCI+KDJ双重触发"
    return "仅CCI触发" if has_c else "仅KDJ触发"


# ============================================================
# 扫描
# ============================================================
def scan(end_date: str) -> pd.DataFrame:
    cfg = load_pool()
    rows = []
    for pool_key in ("A1", "A2"):
        for item in cfg.get(pool_key, []):
            icode, iname = item["index_code"], item["index_name"]
            etfs = item.get("etfs", []) or []
            print(f"  [{pool_key}] {iname}({icode}) + {len(etfs)} ETF")

            idx_m = weekly_metrics(fetch_index_daily(icode, end_date))
            if idx_m is None:
                print("     → 指数数据不足，跳过")
                continue
            i_dir, i_src = judge(idx_m["J"], idx_m["CCI"])

            etf_hits, adj_fail = [], []
            for e in etfs:
                try:
                    d = fetch_etf_daily(e, end_date)
                except Exception as ex:
                    print(f"     ETF {e} 取数失败: {ex}")
                    continue
                if d is None or d.empty:
                    continue
                d, ok_adj = apply_adj(d, e, end_date)
                if not ok_adj:
                    adj_fail.append(e)
                em = weekly_metrics(d)
                if em is None:
                    continue
                e_dir, e_src = judge(em["J"], em["CCI"])
                if e_dir:
                    etf_hits.append({"code": e, "J": em["J"], "CCI": em["CCI"],
                                     "dir": e_dir, "src": e_src, "adj": ok_adj})
                time.sleep(0.02)

            e_dir_any = etf_hits[0]["dir"] if etf_hits else None
            if i_dir and e_dir_any:
                trigger = "指数-ETF双重触发"
            elif i_dir:
                trigger = "仅指数触发"
            elif e_dir_any:
                trigger = "仅ETF触发"
            else:
                trigger = None

            direction = i_dir or e_dir_any
            ind_src = None
            if direction:
                sync_srcs = set()
                if i_dir == direction:
                    sync_srcs.add(i_src)
                for h in etf_hits:
                    if h["dir"] == direction:
                        sync_srcs.add(h["src"])
                ind_src = merge_indicator(sync_srcs)

            rows.append({
                "pool": pool_key, "index_code": icode, "index_name": iname,
                "index_fit": item.get("index_fit", ""),
                "idx_J": idx_m["J"], "idx_CCI": idx_m["CCI"],
                "idx_signal": i_dir, "idx_src": i_src,
                "week": idx_m["week"],
                "etf_hits": etf_hits, "n_etf": len(etfs),
                "adj_fail": adj_fail,
                "direction": direction, "trigger": trigger, "indicator": ind_src,
            })
    return rows


# ============================================================
# 消息
# ============================================================
def build_message(rows, end_date: str) -> str:
    sig = [r for r in rows if r["direction"]]
    buys = [r for r in sig if r["direction"] == "买入"]
    sells = [r for r in sig if r["direction"] == "卖出"]
    n_a1 = sum(1 for r in rows if r["pool"] == "A1")
    n_a2 = sum(1 for r in rows if r["pool"] == "A2")

    lines = [
        f"## 奇点战法 · 周线信号（A 类观察池）",
        f"",
        f"- 数据截至：**{end_date}**",
        f"- 观察池：A1 池 {n_a1} 指数 / A2 池 {n_a2} 指数，共 {len(rows)} 指数",
        f"- 判定：买入 J≤{BUY_J_THRESHOLD:.0f} 或 CCI≤{BUY_CCI_THRESHOLD:.0f}；"
        f"卖出 J≥{SELL_J_THRESHOLD:.0f} 或 CCI≥{SELL_CCI_THRESHOLD:.0f}（周线；ETF 已复权）",
        f"",
    ]

    for label, group, icon in (("买入", buys, "🟢"), ("卖出", sells, "🔴")):
        lines.append(f"### {icon} {label}信号：{len(group)} 条")
        if not group:
            lines.append("（无）")
        for r in group:
            lines.append(f"**{r['index_name']}（{r['index_code']}）**")
            lines.append(f"- 池别：**{r['pool']} 池** ｜ 触发源：**{r['trigger']}** ｜ 指标源：**{r['indicator']}**")
            lines.append(f"- 指数：J={r['idx_J']:.1f} / CCI={r['idx_CCI']:.1f}"
                         f"（{r['idx_signal'] or '无信号'}）")
            if r["etf_hits"]:
                for h in r["etf_hits"]:
                    tag = "" if h["adj"] else " ⚠未复权"
                    lines.append(f"- ETF {h['code']}：J={h['J']:.1f} / CCI={h['CCI']:.1f}"
                                 f"（{h['dir']}·{h['src']}）{tag}")
            else:
                lines.append("- ETF：池内产品均无信号")
            if r["adj_fail"]:
                lines.append(f"- ⚠ 以下 ETF 未能获取复权因子，信号可能失真：{', '.join(r['adj_fail'])}")
            lines.append("")
        lines.append("")

    nosig = [r for r in rows if not r["direction"]]
    if nosig:
        lines.append(f"### ⚪ 无信号：{len(nosig)} 个指数")
        lines.append("、".join(f"[{r['pool']}]{r['index_name']}" for r in nosig))
        lines.append("")
    lines.append("---")
    lines.append("说明：触发源=指数/ETF/双重；指标源=CCI/KDJ/双重；池别 A1（两口径均入池）优先于 A2。")
    lines.append("")
    lines.append("⚡ **执行口径**：本信号基于**截至当日**的周线（当周未走完也照算）判定，"
                 "属回测中的 **M1 时点**——建议**次一交易日开盘价**挂单。"
                 "回测（80 标的）显示 M1 优于「等当周收盘」：平均多 +20.4pp 超额（65 胜 15 负）。"
                 "同一波可能连续多日推送，**首次触发即执行**，不必等第二次。")
    return "\n".join(lines)


# ============================================================
# 主流程
# ============================================================
def run_pool_scan(date=None, dry_run=False, force=False, save=False, check_listing=False):
    """执行一轮 A 类观察池扫描。供本文件 CLI 与 main.py 的 qidian 子命令共用。

    返回 dict：{"end_date":..., "rows":[...], "n_signal":int, "skipped":str|None}
    """
    if check_listing:
        cfg = load_pool()
        print("[池健康检查] 核验池内 ETF 上市状态（约 53 次接口调用）...")
        bad = audit_pool_listing(cfg)
        if bad:
            print(f"  ⚠ 发现 {len(bad)} 个非在市/异常 ETF：")
            for k, inm, e, st, dl in bad:
                print(f"    [{k}] {inm} → {e}  {st}  退市日={dl}")
            print("  ⇒ 请修正池配置后重跑；本次扫描将继续，但上述标的信号不可交易。")
        else:
            print("  ✓ 全部在市")

    # 交易日闸门
    if date is None and not force:
        ok, why = is_trading_day()
        if not ok:
            print(f"\n{'='*70}")
            print("奇点战法 A 类池信号扫描 — 已跳过")
            print(f"  交易日判定：{why}")
            print("  非交易日不扫描、不推送。如需强制运行加 --force。")
            print(f"{'='*70}")
            return {"end_date": None, "rows": [], "n_signal": 0, "skipped": why}
        print(f"[交易日闸门] {why} → 继续扫描")

    end_date = date or get_latest_trade_date()
    print(f"\n{'='*70}")
    print(f"奇点战法 · A 类观察池信号扫描　基准日：{end_date}")
    print(f"{'='*70}")

    rows = scan(end_date)
    msg = build_message(rows, end_date)
    print("\n" + "=" * 70)
    print(msg)
    print("=" * 70)

    n_buy = sum(1 for r in rows if r["direction"] == "买入")
    n_sell = sum(1 for r in rows if r["direction"] == "卖出")
    n_sig = n_buy + n_sell
    print(f"\n汇总：扫描 {len(rows)} 指数 | 信号 {n_sig} 条（买入 {n_buy} / 卖出 {n_sell}）")

    if save:
        out = Path(DOC_DIR) / "策略体系" / "奇点投资框架" / f"奇点信号_{end_date}.md"
        out.write_text(msg, encoding="utf-8")
        print(f"已保存：{out}")

    if dry_run:
        print("[DRY-RUN] 不推送")
        return {"end_date": end_date, "rows": rows, "n_signal": n_sig, "skipped": None}

    if n_sig == 0:
        print("[推送] 当日无信号，按约定不推送")
        return {"end_date": end_date, "rows": rows, "n_signal": 0, "skipped": None}

    from push.pushplus import PushPlus
    title = f"奇点信号 A类池 {end_date}（买{n_buy}/卖{n_sell}）"
    pp = PushPlus(token=PUSHPLUS_TOKEN, channel=PUSHPLUS_CHANNEL, template=PUSHPLUS_TEMPLATE)
    res = pp.send(title=title, content=msg)
    print(f"[推送] {res}")
    return {"end_date": end_date, "rows": rows, "n_signal": n_sig, "skipped": None}


def main():
    ap = argparse.ArgumentParser(description="奇点战法 · A 类观察池信号扫描")
    ap.add_argument("--dry-run", action="store_true", help="仅扫描不推送")
    ap.add_argument("--date", type=str, default=None, help="扫描日期 YYYYMMDD（默认最近交易日）")
    ap.add_argument("--force", action="store_true", help="跳过交易日闸门")
    ap.add_argument("--save", action="store_true", help="保存消息到文档目录")
    ap.add_argument("--check-listing", action="store_true",
                    help="扫描前核验池内 ETF 上市状态（发现已退市标的）")
    args = ap.parse_args()
    run_pool_scan(date=args.date, dry_run=args.dry_run, force=args.force,
                  save=args.save, check_listing=args.check_listing)


if __name__ == "__main__":
    main()
