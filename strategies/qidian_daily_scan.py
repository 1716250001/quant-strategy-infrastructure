# -*- coding: utf-8 -*-
"""
qidian_daily_scan.py — 奇点战法每日双重信号扫描
=================================================
每日扫描41个ETF及其对应指数的周线KDJ/CCI，
ETF+指数双重确认买卖点后通过PushPlus推送。

2026-09-02 新增: XAU（伦敦金现, XAUUSD.FXCM）单标的监控:
  无对应场内ETF（A股黄金ETF为人民币金价口径，非美元伦敦金），
  不参与双重确认，周线KDJ/CCI单标的直接评估，信号独立推送。

信号规则（周线级别）:
  买入: J <= -5 或 CCI <= -150
  卖出: J >= 110 或 CCI >= 150
  双重确认: ETF和对应指数均触发同一方向信号才推送

数据源:
  ETF日线: tushare pro.fund_daily (优先) → 本地parquet (回退)
  指数日线: tushare pro.index_daily
  外汇/贵金属: tushare pro.fx_daily (XAUUSD.FXCM, bid价口径)
  周线聚合: ISO周(W-FRI)

用法:
  python qidian_daily_scan.py              # 扫描+推送
  python qidian_daily_scan.py --dry-run    # 仅扫描不推送
  python qidian_daily_scan.py --report     # 生成HTML报告
"""

import argparse
import os
import time
from datetime import datetime
from typing import Dict, List

from common.paths import DOC_DIR
from config import PUSHPLUS_TOKEN, PUSHPLUS_CHANNEL
from fetch.base import get_latest_trade_date
from indicators import calc_kdj_standard

# ── 信号层（标的池/取数/指标/信号判断）──
from strategies.qidian import (
    QIDIAN_POOL, XAU_WATCHLIST,
    BUY_J_THRESHOLD, BUY_CCI_THRESHOLD, SELL_J_THRESHOLD, SELL_CCI_THRESHOLD,
    KDJ_N, KDJ_M1, KDJ_M2, CCI_N,
    fetch_etf_daily, fetch_index_daily, fetch_fx_daily,
    aggregate_to_weekly, calc_cci, check_signal_detail,
)

# ============================================================
# 本文件职责：扫描编排 + 信号推送 + HTML 报告 + CLI
# 标的池 / 取数 / 指标 / 信号判断已抽取至 strategies/qidian.py
# ============================================================

# ============================================================
# 8.5 转债温度计（W2 · 2026-09-25 接入）
# 万得自编转债指数三件套，本地库读取，观察级信号（不参与双重确认、
# 不产生交易指令）——等权口径比 511180 灵敏（副官 2026-09-23 结论）。
# ⚠ wind_index 库当前停更于 20260921（日更分层方案 W3 待批复），
#   结果必须携带 data_asof，推送时标注数据日期。
# ✅ Q-G 决议（2026-09-25 老大令"批准阶段2、3、4执行"）：**保留观察级**——
#   Wind 研究暂停背景下不批 W3 日更、不停用温度计；as_of 标注为唯一防线，
#   W 系列重启时再议数据新鲜度。
# ============================================================
WIND_INDEX_DAILY_DIR = r"D:\全量数据\market_data\wind_index\daily"

WIND_CB_THERMOMETER = {
    "889033": {"name": "万得转债等权(温度计)", "role": "情绪温度计·等权口径"},
    "889047": {"name": "万得转债双低(策略基准)", "role": "双低策略基准"},
    "889043": {"name": "万得转债低价(防守锚)", "role": "低价防守锚"},
}


def fetch_wind_index_daily(code6: str, end_date: str, lookback_days: int = 2500) -> "pd.DataFrame":
    """读本地 wind_index 日线 parquet → 与 aggregate_to_weekly 兼容的 DataFrame。
    应用 DR-006 约定：从 close>0 首日起算（概念指数发布首年 OHLC 全 0 的源端特性）。"""
    import pandas as pd
    fp = os.path.join(WIND_INDEX_DAILY_DIR, f"{code6}.parquet")
    if not os.path.isfile(fp):
        return pd.DataFrame()
    df = pd.read_parquet(fp)
    if df.empty:
        return df
    df = df.rename(columns={"date": "trade_date", "volume": "vol"})
    for col in ("open", "high", "low", "close", "vol"):
        if col not in df.columns:
            return pd.DataFrame()
    df["trade_date"] = df["trade_date"].astype(str)
    df = df[df["trade_date"] <= str(end_date)]
    # DR-006: 从 close>0 首日起算
    pos = df["close"].astype(float)
    if (pos > 0).any():
        first_pos = pos[pos > 0].index[0]
        df = df.loc[first_pos:]
    else:
        return pd.DataFrame()
    return df.tail(lookback_days)


def scan_wind_thermometer(end_date: str) -> List[Dict]:
    """转债温度计三件套扫描：周线 KDJ/CCI + check_signal_detail → 观察级结果。"""
    results = []
    for code6, info in WIND_CB_THERMOMETER.items():
        r = {
            "wind_code": code6, "wind_name": info["name"], "wind_role": info["role"],
            "wind_j": None, "wind_cci": None, "wind_signal": None, "wind_triggers": [],
            "data_asof": None, "status": "ok", "error": None,
        }
        try:
            daily = fetch_wind_index_daily(code6, end_date)
            if daily.empty:
                r["status"] = "error"
                r["error"] = f"本地无数据: {code6}"
                results.append(r)
                continue
            r["data_asof"] = str(daily["trade_date"].iloc[-1])
            weekly = aggregate_to_weekly(daily)
            if len(weekly) < CCI_N:
                r["status"] = "error"
                r["error"] = f"周线数据不足({len(weekly)}根): {code6}"
                results.append(r)
                continue
            h = weekly["high"].astype(float).values
            l = weekly["low"].astype(float).values
            c = weekly["close"].astype(float).values
            _k, _d, j = calc_kdj_standard(h, l, c, n=KDJ_N, m1=KDJ_M1, m2=KDJ_M2)
            cci = calc_cci(h, l, c)
            sig = check_signal_detail(j, cci)
            r["wind_j"], r["wind_cci"] = round(j, 2), round(cci, 2)
            r["wind_signal"], r["wind_triggers"] = sig["signal"], sig["triggers"]
        except Exception as e:
            r["status"] = "error"
            r["error"] = str(e)[:120]
        results.append(r)
    return results



# ============================================================
# 8. 单标的扫描
# ============================================================
def scan_single(etf_code: str, etf_name: str, index_code: str, index_name: str,
                end_date: str) -> Dict:
    """
    扫描单个ETF+指数对，返回信号结果
    """
    result = {
        "etf_code": etf_code,
        "etf_name": etf_name,
        "index_code": index_code,
        "index_name": index_name,
        "etf_j": None, "etf_cci": None, "etf_signal": None, "etf_triggers": [],
        "idx_j": None, "idx_cci": None, "idx_signal": None, "idx_triggers": [],
        "dual_signal": None,       # 双重确认信号
        "dual_triggers": [],        # 双重确认触发原因
        "status": "ok",
        "error": None,
    }

    # ── 获取ETF周线 ──
    etf_daily = fetch_etf_daily(etf_code, end_date)
    if etf_daily.empty:
        result["status"] = "error"
        result["error"] = f"ETF数据获取失败: {etf_code}"
        return result

    etf_weekly = aggregate_to_weekly(etf_daily)
    if len(etf_weekly) < CCI_N:
        result["status"] = "error"
        result["error"] = f"ETF周线数据不足({len(etf_weekly)}根): {etf_code}"
        return result

    # ── 获取指数周线 ──
    idx_daily = fetch_index_daily(index_code, end_date)
    if idx_daily.empty:
        result["status"] = "error"
        result["error"] = f"指数数据获取失败: {index_code}"
        return result

    idx_weekly = aggregate_to_weekly(idx_daily)
    if len(idx_weekly) < CCI_N:
        result["status"] = "error"
        result["error"] = f"指数周线数据不足({len(idx_weekly)}根): {index_code}"
        return result

    # ── 计算ETF KDJ/CCI ──
    etf_h = etf_weekly["high"].astype(float).values
    etf_l = etf_weekly["low"].astype(float).values
    etf_c = etf_weekly["close"].astype(float).values

    etf_k, etf_d, etf_j = calc_kdj_standard(etf_h, etf_l, etf_c, n=KDJ_N, m1=KDJ_M1, m2=KDJ_M2)
    etf_cci = calc_cci(etf_h, etf_l, etf_c)

    etf_sig = check_signal_detail(etf_j, etf_cci)
    result["etf_j"] = round(etf_j, 2)
    result["etf_cci"] = round(etf_cci, 2)
    result["etf_signal"] = etf_sig["signal"]
    result["etf_triggers"] = etf_sig["triggers"]

    # ── 计算指数 KDJ/CCI ──
    idx_h = idx_weekly["high"].astype(float).values
    idx_l = idx_weekly["low"].astype(float).values
    idx_c = idx_weekly["close"].astype(float).values

    idx_k, idx_d, idx_j = calc_kdj_standard(idx_h, idx_l, idx_c, n=KDJ_N, m1=KDJ_M1, m2=KDJ_M2)
    idx_cci = calc_cci(idx_h, idx_l, idx_c)

    idx_sig = check_signal_detail(idx_j, idx_cci)
    result["idx_j"] = round(idx_j, 2)
    result["idx_cci"] = round(idx_cci, 2)
    result["idx_signal"] = idx_sig["signal"]
    result["idx_triggers"] = idx_sig["triggers"]

    # ── 双重确认 ──
    if etf_sig["signal"] is not None and etf_sig["signal"] == idx_sig["signal"]:
        result["dual_signal"] = etf_sig["signal"]
        result["dual_triggers"] = [
            f"ETF: {', '.join(etf_sig['triggers'])}",
            f"指数: {', '.join(idx_sig['triggers'])}",
        ]

    return result


# ============================================================
# 8b. 外汇/贵金属现货单标的扫描（XAU等，无双重确认）
# ============================================================
def scan_fx_single(fx_code: str, fx_name: str, end_date: str) -> Dict:
    """
    扫描单个外汇/贵金属现货标的（如伦敦金现XAUUSD.FXCM）
    无对应场内ETF，不参与双重确认，周线KDJ/CCI直接评估
    """
    result = {
        "fx_code": fx_code,
        "fx_name": fx_name,
        "fx_j": None, "fx_cci": None, "fx_signal": None, "fx_triggers": [],
        "fx_close": None,
        "status": "ok",
        "error": None,
    }

    # ── 获取周线 ──
    fx_daily = fetch_fx_daily(fx_code, end_date)
    if fx_daily.empty:
        result["status"] = "error"
        result["error"] = f"外汇数据获取失败: {fx_code}"
        return result

    fx_weekly = aggregate_to_weekly(fx_daily)
    if len(fx_weekly) < CCI_N:
        result["status"] = "error"
        result["error"] = f"外汇周线数据不足({len(fx_weekly)}根): {fx_code}"
        return result

    # ── 计算周线KDJ/CCI ──
    h = fx_weekly["high"].astype(float).values
    l = fx_weekly["low"].astype(float).values
    c = fx_weekly["close"].astype(float).values

    _, _, j = calc_kdj_standard(h, l, c, n=KDJ_N, m1=KDJ_M1, m2=KDJ_M2)
    cci = calc_cci(h, l, c)
    sig = check_signal_detail(j, cci)

    result["fx_j"] = round(j, 2)
    result["fx_cci"] = round(cci, 2)
    result["fx_signal"] = sig["signal"]
    result["fx_triggers"] = sig["triggers"]
    result["fx_close"] = round(float(c[-1]), 2)

    return result


# ============================================================
# 9. 全池扫描
# ============================================================
def scan_all(end_date: str = None) -> List[Dict]:
    """
    扫描全部30个ETF，返回结果列表
    """
    if end_date is None:
        end_date = get_latest_trade_date()

    print(f"\n{'='*70}")
    print("奇点战法每日双重信号扫描")
    print(f"扫描日期: {end_date}")
    print(f"标的数量: {len(QIDIAN_POOL)}个ETF, {sum(len(v['indices']) for v in QIDIAN_POOL.values())}个指数"
          f", {len(XAU_WATCHLIST)}个外汇/贵金属单标的")
    print(f"{'='*70}\n")

    results = []
    total = len(QIDIAN_POOL)
    etf_idx = 0

    for etf_code, etf_info in QIDIAN_POOL.items():
        etf_idx += 1
        etf_name = etf_info["name"]
        recommended = "★" if etf_info["recommended"] else " "
        print(f"[{etf_idx:2d}/{total}] {recommended} {etf_code} {etf_name}")

        for idx_info in etf_info["indices"]:
            idx_code = idx_info["code"]
            idx_name = idx_info["name"]

            r = scan_single(etf_code, etf_name, idx_code, idx_name, end_date)
            results.append(r)

            if r["status"] == "error":
                print(f"         → 指数 {idx_code} ({idx_name}): ERROR - {r['error']}")
            else:
                etf_sig_str = r["etf_signal"] or "无"
                idx_sig_str = r["idx_signal"] or "无"
                dual_sig_str = r["dual_signal"] or "无"
                print(f"         → 指数 {idx_code} ({idx_name}): "
                      f"ETF J={r['etf_j']:.1f}/CCI={r['etf_cci']:.1f}[{etf_sig_str}] "
                      f"指数 J={r['idx_j']:.1f}/CCI={r['idx_cci']:.1f}[{idx_sig_str}] "
                      f"双重=[{dual_sig_str}]")

            # tushare限流: 间隔0.35秒
            time.sleep(0.35)

        print()

    # ── 外汇/贵金属现货单标的扫描（无双重确认）──
    if XAU_WATCHLIST:
        print(f"{'-'*70}")
        print("外汇/贵金属现货单标的监控（无对应ETF，周线KDJ/CCI直接评估）\n")
        for fx_code, fx_info in XAU_WATCHLIST.items():
            r = scan_fx_single(fx_code, fx_info["name"], end_date)
            results.append(r)

            if r["status"] == "error":
                print(f"  → {fx_code} ({fx_info['name']}): ERROR - {r['error']}")
            else:
                print(f"  → {fx_code} ({fx_info['name']}): "
                      f"周线收盘={r['fx_close']} J={r['fx_j']:.1f}/CCI={r['fx_cci']:.1f} "
                      f"[{r['fx_signal'] or '无'}]")
            time.sleep(0.35)
        print()

    # ── 转债温度计（W2·观察级，本地库零API）──
    print(f"{'-'*70}")
    print("转债温度计·万得自编转债指数三件套（观察级，不参与双重确认）\n")
    for r in scan_wind_thermometer(end_date):
        results.append(r)
        if r["status"] == "error":
            print(f"  → {r['wind_code']} ({r['wind_name']}): ERROR - {r['error']}")
        else:
            print(f"  → {r['wind_code']} ({r['wind_name']}): "
                  f"J={r['wind_j']:.1f}/CCI={r['wind_cci']:.1f} [{r['wind_signal'] or '无'}] "
                  f"(as_of={r['data_asof']})")
    print()

    return results


# ============================================================
# 10. 推送
# ============================================================
def push_signals(results: List[Dict], dry_run: bool = False,
                   push_single_buy: bool = False, push_single_sell: bool = False) -> Dict:
    """
    从扫描结果中提取信号，通过PushPlus分级推送

    推送三档:
      A（默认）: 仅双重确认信号
      B（push_single_buy=True）: 加推单边买入信号，带【观察】标记+纪律提示
      C（push_single_sell=True）: 加推单边卖出信号，带【仅观察】特别标注
    """
    # 分离外汇/贵金属单标的结果（含fx_code键为单标结果标识）
    etf_results = [r for r in results if "fx_code" not in r and "wind_code" not in r]
    fx_results = [r for r in results if "fx_code" in r]
    wind_results = [r for r in results if "wind_code" in r]

    # 提取双重确认信号
    dual_signals = [r for r in etf_results if r["dual_signal"] is not None and r["status"] == "ok"]
    # 提取单边信号（ETF或指数单独触发，作为观察信号）
    single_signals = [r for r in etf_results
                      if r["dual_signal"] is None and r["status"] == "ok"
                      and (r["etf_signal"] is not None or r["idx_signal"] is not None)]
    # 提取外汇/贵金属单标信号（XAU等，无双重确认口径，直接作为该标的最终信号）
    fx_signals = [r for r in fx_results if r["status"] == "ok" and r["fx_signal"] is not None]
    # 转债温度计（W2·观察级：触发才随单边/双重同批推送，无信号不推）
    wind_signals = [r for r in wind_results
                    if r["status"] == "ok" and r["wind_signal"] is not None]

    # 拆分单边信号为买入/卖出
    single_buy_signals = [r for r in single_signals
                          if r["etf_signal"] == "买入" or r["idx_signal"] == "买入"]
    single_sell_signals = [r for r in single_signals
                           if r["etf_signal"] == "卖出" or r["idx_signal"] == "卖出"]

    # 方案A: 只推双重确认
    # 方案B: 加推单边买入（带观察标记）
    # 方案C: 加推单边卖出（带仅观察标注）

    if not dual_signals and not fx_signals and not single_signals and not wind_signals:
        print("\n[推送] 无任何信号，不推送")
        return {"success": False, "msg": "无信号"}

    # 判断是否需要推送单边信号
    push_single = push_single_buy or push_single_sell
    if not dual_signals and not fx_signals and not wind_signals \
            and not (push_single and single_signals):
        print("\n[推送] 无双重确认/XAU单标/温度计信号，且未开启单边推送，不推送")
        return {"success": False, "msg": "无信号（方案A: 仅双重确认+XAU单标+温度计观察）"}

    # ── 构造推送信号列表 ──
    push_items = []

    # 双重确认信号（始终推送）
    for r in dual_signals:
        signal_type = r["dual_signal"]
        recommended = "★推荐池" if QIDIAN_POOL.get(r["etf_code"], {}).get("recommended", False) else ""

        detail = (
            f"ETF {r['etf_code']}({r['etf_name']}): "
            f"J={r['etf_j']}, CCI={r['etf_cci']} → {'/'.join(r['etf_triggers'])}\n"
            f"指数 {r['index_code']}({r['index_name']}): "
            f"J={r['idx_j']}, CCI={r['idx_cci']} → {'/'.join(r['idx_triggers'])}\n"
            f"双重确认: ETF+指数同时触发{signal_type}"
        )

        push_items.append({
            "symbol": f"{r['etf_name']}({r['etf_code']}) {recommended}",
            "type": f"奇点战法-{signal_type}",
            "period": "周线",
            "detail": detail,
            "level": "强",
            "label": "长线",
            "group": "dual",
        })

    # 外汇/贵金属单标信号（XAU等，无ETF双重确认，作为该标的最终信号）
    for r in fx_signals:
        signal_type = r["fx_signal"]
        detail = (
            f"{r['fx_code']}({r['fx_name']}): "
            f"周线收盘={r['fx_close']}, J={r['fx_j']}, CCI={r['fx_cci']} → {'/'.join(r['fx_triggers'])}\n"
            f"单标的信号: 无对应场内ETF，不适用双重确认口径，周线直接触发\n"
            f"提示: XAU为美元伦敦金现，国内无直接场内工具"
            f"（黄金ETF 518880/沪金AU.SHF为人民币金价口径，仅参考）"
        )
        push_items.append({
            "symbol": f"{r['fx_name']}({r['fx_code']})",
            "type": f"奇点战法-XAU-{signal_type}",
            "period": "周线",
            "detail": detail,
            "level": "强",
            "label": "长线",
            "group": "fx",
        })

    # 转债温度计信号（W2·观察级：万得自编转债指数，触发才推，不构成交易指令）
    for r in wind_signals:
        signal_type = r["wind_signal"]
        detail = (
            f"{r['wind_code']}({r['wind_name']}) [{r['wind_role']}]: "
            f"周线 J={r['wind_j']}, CCI={r['wind_cci']} → {'/'.join(r['wind_triggers'])}\n"
            f"观察级: 转债资产类温度计（等权口径比转债ETF灵敏），非交易信号\n"
            f"⚠ 数据 as_of={r['data_asof']}（wind指数库，非实时；引用须标日期）"
        )
        push_items.append({
            "symbol": f"{r['wind_name']}({r['wind_code']})",
            "type": f"转债温度计-{signal_type}·观察",
            "period": "周线",
            "detail": detail,
            "level": "观察",
            "label": "观察",
            "group": "wind_thermometer",
        })

    # 单边买入信号（方案B）
    if push_single_buy:
        for r in single_buy_signals:
            parts = []
            if r["etf_signal"]:
                parts.append(f"ETF {r['etf_name']}: J={r['etf_j']}, CCI={r['etf_cci']} [{'/'.join(r['etf_triggers'])}]")
            if r["idx_signal"]:
                parts.append(f"指数 {r['index_name']}: J={r['idx_j']}, CCI={r['idx_cci']} [{'/'.join(r['idx_triggers'])}]")

            detail = "\n".join(parts) + (
                "\n【观察】单边买入触发，未双重确认\n"
                "纪律: 不追、等回调、轻仓试探或不动\n"
                "单边1-2周期望为负，8周才转正，方向对但时机模糊"
            )

            recommended = "★推荐池" if QIDIAN_POOL.get(r["etf_code"], {}).get("recommended", False) else ""
            push_items.append({
                "symbol": f"{r['etf_name']}({r['etf_code']}) {recommended}",
                "type": "奇点战法-观察买入",
                "period": "周线",
                "detail": detail,
                "level": "弱",
                "label": "长线",
                "group": "single",
            })

    # 单边卖出信号（方案C）
    if push_single_sell:
        for r in single_sell_signals:
            parts = []
            if r["etf_signal"]:
                parts.append(f"ETF {r['etf_name']}: J={r['etf_j']}, CCI={r['etf_cci']} [{'/'.join(r['etf_triggers'])}]")
            if r["idx_signal"]:
                parts.append(f"指数 {r['index_name']}: J={r['idx_j']}, CCI={r['idx_cci']} [{'/'.join(r['idx_triggers'])}]")

            detail = "\n".join(parts) + (
                "\n【仅观察】单边卖出触发，未双重确认\n"
                "注意: 卖出方向单边推送可能导致提前下车\n"
                "建议: 等双重确认或手动确认趋势后再操作"
            )

            recommended = "★推荐池" if QIDIAN_POOL.get(r["etf_code"], {}).get("recommended", False) else ""
            push_items.append({
                "symbol": f"{r['etf_name']}({r['etf_code']}) {recommended}",
                "type": "奇点战法-仅观察卖出",
                "period": "周线",
                "detail": detail,
                "level": "弱",
                "label": "长线",
                "group": "single",
            })

    # ── 推送 ──
    if dry_run:
        sb_count = len(single_buy_signals) if push_single_buy else 0
        ss_count = len(single_sell_signals) if push_single_sell else 0
        print(f"\n[DRY-RUN] 双重确认信号 {len(dual_signals)} 条, XAU单标信号 {len(fx_signals)} 条"
              f"{f', 观察买入 {sb_count} 条' if push_single_buy else ''}"
              f"{f', 仅观察卖出 {ss_count} 条' if push_single_sell else ''}")
        for item in push_items:
            print(f"  → {item['symbol']} | {item['type']} | {item['level']}")
            print(f"    {item['detail']}")
        return {"success": False, "msg": "dry-run", "data": push_items}

    # 实际推送
    try:
        from push.pushplus import PushPlus
        pp = PushPlus(token=PUSHPLUS_TOKEN, channel=PUSHPLUS_CHANNEL, template="markdown")

        # 双重确认信号单独推送（高优先级）
        dual_items = [item for item in push_items if item.get("group") == "dual"]
        if dual_items:
            result = pp.send_signals(dual_items, title_prefix="奇点战法双重确认信号")
            print(f"\n[推送] 双重确认信号: {result.get('msg', 'unknown')}")
            time.sleep(2)

        # 外汇/贵金属单标信号推送（XAU等，高优先级独立推送）
        fx_push_items = [item for item in push_items if item.get("group") == "fx"]
        if fx_push_items:
            result = pp.send_signals(fx_push_items, title_prefix="奇点战法贵金属信号")
            print(f"[推送] 贵金属单标信号: {result.get('msg', 'unknown')}")
            time.sleep(2)

        # 单边观察信号单独推送（低优先级）
        single_items = [item for item in push_items if item.get("group") == "single"]
        if single_items:
            result = pp.send_signals(single_items, title_prefix="奇点战法观察信号")
            print(f"[推送] 观察信号: {result.get('msg', 'unknown')}")

        return {"success": True, "dual_count": len(dual_items),
                "fx_count": len(fx_push_items),
                "observe_count": len(single_items)}
    except Exception as e:
        print(f"[推送] 失败: {e}")
        return {"success": False, "msg": str(e)}


# ============================================================
# 11. HTML报告生成
# ============================================================
def generate_html_report(results: List[Dict], end_date: str) -> str:
    """
    生成HTML扫描报告
    """
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report_date = datetime.now().strftime("%Y%m%d")

    # 分离外汇/贵金属单标的结果
    etf_results = [r for r in results if "fx_code" not in r]
    fx_results = [r for r in results if "fx_code" in r]

    # 统计
    total = len(results)
    ok_count = sum(1 for r in results if r["status"] == "ok")
    error_count = sum(1 for r in results if r["status"] == "error")
    dual_buy = sum(1 for r in etf_results if r["dual_signal"] == "买入")
    dual_sell = sum(1 for r in etf_results if r["dual_signal"] == "卖出")
    fx_buy = sum(1 for r in fx_results if r["status"] == "ok" and r["fx_signal"] == "买入")
    fx_sell = sum(1 for r in fx_results if r["status"] == "ok" and r["fx_signal"] == "卖出")
    single_buy = sum(1 for r in etf_results if r["dual_signal"] is None and r["status"] == "ok"
                     and (r["etf_signal"] == "买入" or r["idx_signal"] == "买入"))
    single_sell = sum(1 for r in etf_results if r["dual_signal"] is None and r["status"] == "ok"
                      and (r["etf_signal"] == "卖出" or r["idx_signal"] == "卖出"))

    # 表格行（ETF+指数部分）
    rows_html = []
    for r in etf_results:
        etf_code = r["etf_code"]
        recommended = "★" if QIDIAN_POOL.get(etf_code, {}).get("recommended", False) else ""

        def signal_badge(sig):
            if sig == "买入":
                return '<span style="background:#e74c3c;color:#fff;padding:2px 8px;border-radius:3px;font-size:11px;">买入</span>'
            elif sig == "卖出":
                return '<span style="background:#27ae60;color:#fff;padding:2px 8px;border-radius:3px;font-size:11px;">卖出</span>'
            else:
                return '<span style="color:#999;">—</span>'

        def j_color(j):
            if j is None: return "#999"
            if j <= BUY_J_THRESHOLD: return "#e74c3c"
            if j >= SELL_J_THRESHOLD: return "#27ae60"
            if j <= 0: return "#e67e22"
            if j >= 90: return "#f39c12"
            return "#333"

        def cci_color(cci):
            if cci is None: return "#999"
            if cci <= BUY_CCI_THRESHOLD: return "#e74c3c"
            if cci >= SELL_CCI_THRESHOLD: return "#27ae60"
            if cci <= -100: return "#e67e22"
            if cci >= 100: return "#f39c12"
            return "#333"

        dual_str = signal_badge(r["dual_signal"])

        rows_html.append(f"""
        <tr>
            <td style="text-align:center;">{recommended}</td>
            <td>{r['etf_code']}<br><span style="font-size:11px;color:#666;">{r['etf_name']}</span></td>
            <td>{r['index_code']}<br><span style="font-size:11px;color:#666;">{r['index_name']}</span></td>
            <td style="text-align:right;color:{j_color(r['etf_j'])};font-weight:bold;">{r['etf_j'] if r['etf_j'] is not None else '—'}</td>
            <td style="text-align:right;color:{cci_color(r['etf_cci'])};font-weight:bold;">{r['etf_cci'] if r['etf_cci'] is not None else '—'}</td>
            <td style="text-align:center;">{signal_badge(r['etf_signal'])}</td>
            <td style="text-align:right;color:{j_color(r['idx_j'])};font-weight:bold;">{r['idx_j'] if r['idx_j'] is not None else '—'}</td>
            <td style="text-align:right;color:{cci_color(r['idx_cci'])};font-weight:bold;">{r['idx_cci'] if r['idx_cci'] is not None else '—'}</td>
            <td style="text-align:center;">{signal_badge(r['idx_signal'])}</td>
            <td style="text-align:center;font-weight:bold;">{dual_str}</td>
            <td style="font-size:11px;color:#999;">{r['error'] or '正常'}</td>
        </tr>""")

    # 外汇/贵金属单标表格行
    fx_rows_html = []
    for r in fx_results:
        fx_sig = r["fx_signal"]
        fx_badge = signal_badge(fx_sig)
        fx_rows_html.append(f"""
        <tr>
            <td>{r['fx_code']}<br><span style="font-size:11px;color:#666;">{r['fx_name']}</span></td>
            <td style="text-align:right;">{r['fx_close'] if r['fx_close'] is not None else '—'}</td>
            <td style="text-align:right;color:{j_color(r['fx_j'])};font-weight:bold;">{r['fx_j'] if r['fx_j'] is not None else '—'}</td>
            <td style="text-align:right;color:{cci_color(r['fx_cci'])};font-weight:bold;">{r['fx_cci'] if r['fx_cci'] is not None else '—'}</td>
            <td style="text-align:center;font-weight:bold;">{fx_badge}</td>
            <td style="font-size:11px;color:#999;">{r['error'] or '正常'}</td>
        </tr>""")

    fx_table_html = ""
    if fx_results:
        fx_table_html = f"""
<h2 style="font-size:18px;color:#333;margin-top:25px;">外汇/贵金属现货单标监控（无双重确认口径）</h2>
<table>
<thead>
<tr>
    <th>标的代码/名称</th>
    <th>周线收盘</th>
    <th>J</th>
    <th>CCI</th>
    <th>信号</th>
    <th>状态</th>
</tr>
</thead>
<tbody>
{"".join(fx_rows_html)}
</tbody>
</table>
<p style="font-size:11px;color:#999;">注: XAU为美元伦敦金现(FXCM口径, tushare fx_daily)，无对应场内ETF，周线KDJ/CCI单标的直接评估。</p>
"""

    # 信号汇总区
    sig_summary = ""
    dual_signals = [r for r in etf_results if r["dual_signal"] is not None and r["status"] == "ok"]
    single_signals = [r for r in etf_results
                      if r["dual_signal"] is None and r["status"] == "ok"
                      and (r["etf_signal"] is not None or r["idx_signal"] is not None)]
    fx_signals = [r for r in fx_results if r["status"] == "ok" and r["fx_signal"] is not None]

    if dual_signals:
        sig_summary += '<div style="background:#fff3cd;border:1px solid #ffc107;padding:12px;border-radius:5px;margin:15px 0;">'
        sig_summary += '<h3 style="color:#856404;margin:0 0 10px;">双重确认信号</h3>'
        for r in dual_signals:
            rec = "★推荐池" if QIDIAN_POOL.get(r["etf_code"], {}).get("recommended", False) else ""
            sig_summary += f'<p style="margin:5px 0;"><b>{r["etf_name"]}({r["etf_code"]}) {rec}</b> — {r["dual_signal"]}<br>'
            sig_summary += f'<span style="font-size:12px;color:#666;">ETF: {" ".join(r["etf_triggers"])} | 指数: {" ".join(r["idx_triggers"])}</span></p>'
        sig_summary += '</div>'

    if single_signals:
        sig_summary += '<div style="background:#e8f5e9;border:1px solid #4caf50;padding:12px;border-radius:5px;margin:15px 0;">'
        sig_summary += '<h3 style="color:#2e7d32;margin:0 0 10px;">单边观察信号</h3>'
        for r in single_signals:
            parts = []
            if r["etf_signal"]:
                parts.append(f"ETF: {' '.join(r['etf_triggers'])}")
            if r["idx_signal"]:
                parts.append(f"指数: {' '.join(r['idx_triggers'])}")
            sig_summary += f'<p style="margin:5px 0;"><b>{r["etf_name"]}({r["etf_code"]})</b> — {r["etf_signal"] or r["idx_signal"]}<br>'
            sig_summary += f'<span style="font-size:12px;color:#666;">{" | ".join(parts)}</span></p>'
        sig_summary += '</div>'

    if fx_signals:
        sig_summary += '<div style="background:#fff8e1;border:1px solid #ffb300;padding:12px;border-radius:5px;margin:15px 0;">'
        sig_summary += '<h3 style="color:#b8860b;margin:0 0 10px;">贵金属单标信号（XAU，无双重确认口径）</h3>'
        for r in fx_signals:
            sig_summary += f'<p style="margin:5px 0;"><b>{r["fx_name"]}({r["fx_code"]})</b> — {r["fx_signal"]}<br>'
            sig_summary += f'<span style="font-size:12px;color:#666;">周线收盘={r["fx_close"]} | J={r["fx_j"]} | CCI={r["fx_cci"]} | {" ".join(r["fx_triggers"])}</span></p>'
        sig_summary += '</div>'

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>奇点战法每日双重信号扫描报告_{report_date}</title>
<style>
    body {{ font-family: 'Microsoft YaHei', sans-serif; margin: 20px; background: #f5f5f5; }}
    h1 {{ color: #333; font-size: 22px; }}
    .summary {{ display: flex; gap: 15px; margin: 15px 0; flex-wrap: wrap; }}
    .card {{ background: white; padding: 15px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); min-width: 120px; text-align: center; }}
    .card .num {{ font-size: 28px; font-weight: bold; }}
    .card .label {{ font-size: 12px; color: #666; margin-top: 5px; }}
    table {{ border-collapse: collapse; width: 100%; background: white; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
    th, td {{ border: 1px solid #ddd; padding: 8px; }}
    th {{ background: #4a76a8; color: white; font-size: 13px; }}
    tr:nth-child(even) {{ background: #f9f9f9; }}
    tr:hover {{ background: #f0f4f8; }}
    .footer {{ margin-top: 20px; color: #999; font-size: 11px; text-align: center; }}
</style>
</head>
<body>
<h1>奇点战法每日双重信号扫描报告</h1>
<p style="color:#666;">扫描日期: {end_date} | 生成时间: {now_str}</p>

<div class="summary">
    <div class="card"><div class="num" style="color:#333;">{total}</div><div class="label">总扫描数</div></div>
    <div class="card"><div class="num" style="color:#27ae60;">{ok_count}</div><div class="label">成功</div></div>
    <div class="card"><div class="num" style="color:#e74c3c;">{error_count}</div><div class="label">错误</div></div>
    <div class="card"><div class="num" style="color:#e74c3c;">{dual_buy}</div><div class="label">双重买入</div></div>
    <div class="card"><div class="num" style="color:#27ae60;">{dual_sell}</div><div class="label">双重卖出</div></div>
    <div class="card"><div class="num" style="color:#b8860b;">{fx_buy + fx_sell}</div><div class="label">XAU单标</div></div>
    <div class="card"><div class="num" style="color:#f39c12;">{single_buy + single_sell}</div><div class="label">单边观察</div></div>
</div>

{sig_summary}

<table>
<thead>
<tr>
    <th>推荐</th>
    <th>ETF代码/名称</th>
    <th>指数代码/名称</th>
    <th>ETF J</th>
    <th>ETF CCI</th>
    <th>ETF信号</th>
    <th>指数 J</th>
    <th>指数 CCI</th>
    <th>指数信号</th>
    <th>双重确认</th>
    <th>状态</th>
</tr>
</thead>
<tbody>
{"".join(rows_html)}
</tbody>
</table>

{fx_table_html}

<div class="footer">
    信号规则: 买入 J≤-5或CCI≤-150 | 卖出 J≥110或CCI≥150 | 双重确认=ETF+指数同向触发 | XAU单标直接评估<br>
    数据源: tushare pro.fund_daily + pro.index_daily + pro.fx_daily(XAUUSD.FXCM bid口径) | ISO周聚合(W-FRI)<br>
    KDJ参数: N={KDJ_N}, M1={KDJ_M1}, M2={KDJ_M2} | CCI周期: {CCI_N}<br>
    生成时间: {now_str}
</div>
</body>
</html>"""

    # 保存
    report_path = os.path.join(DOC_DIR, f"奇点战法每日信号扫描_{report_date}.html")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n[报告] 已保存: {report_path}")
    return report_path


# ============================================================
# 12. 主入口
# ============================================================
def run_scan_pipeline(date=None, dry_run=False, report=False,
                      push_single_buy=False, push_single_sell=False):
    """执行一轮奇点战法扫描：扫描 → 汇总打印 → 推送（可选报告）。

    供本文件 CLI 与统一入口 main.py 的 qidian 子命令共用，避免重复解析参数。
    返回 {"results": [...]}，生成报告时额外带 "report" 路径。
    """
    # 扫描
    results = scan_all(date)

    # 打印汇总
    ok_count = sum(1 for r in results if r["status"] == "ok")
    error_count = sum(1 for r in results if r["status"] == "error")
    etf_results = [r for r in results if "fx_code" not in r and "wind_code" not in r]
    fx_results = [r for r in results if "fx_code" in r]
    wind_results = [r for r in results if "wind_code" in r]
    dual_buy = [r for r in etf_results if r["dual_signal"] == "买入"]
    dual_sell = [r for r in etf_results if r["dual_signal"] == "卖出"]
    fx_signals = [r for r in fx_results if r["status"] == "ok" and r["fx_signal"] is not None]
    wind_signals = [r for r in wind_results
                    if r["status"] == "ok" and r["wind_signal"] is not None]
    single_signals = [r for r in etf_results
                      if r["dual_signal"] is None and r["status"] == "ok"
                      and (r["etf_signal"] is not None or r["idx_signal"] is not None)]

    print(f"\n{'='*70}")
    print(f"扫描完成: 成功{ok_count} / 失败{error_count}")
    print(f"双重确认买入: {len(dual_buy)} | 双重确认卖出: {len(dual_sell)} | "
          f"单边观察: {len(single_signals)} | XAU单标: {len(fx_signals)} | "
          f"转债温度计触发: {len(wind_signals)}(观察级)")
    print(f"{'='*70}")

    if dual_buy:
        print("\n*** 双重确认买入信号 ***")
        for r in dual_buy:
            rec = "★推荐池" if QIDIAN_POOL.get(r["etf_code"], {}).get("recommended", False) else ""
            print(f"  {r['etf_name']}({r['etf_code']}) {rec} → 指数 {r['index_name']}")
            print(f"    ETF: J={r['etf_j']}, CCI={r['etf_cci']} [{', '.join(r['etf_triggers'])}]")
            print(f"    指数: J={r['idx_j']}, CCI={r['idx_cci']} [{', '.join(r['idx_triggers'])}]")

    if dual_sell:
        print("\n*** 双重确认卖出信号 ***")
        for r in dual_sell:
            rec = "★推荐池" if QIDIAN_POOL.get(r["etf_code"], {}).get("recommended", False) else ""
            print(f"  {r['etf_name']}({r['etf_code']}) {rec} → 指数 {r['index_name']}")
            print(f"    ETF: J={r['etf_j']}, CCI={r['etf_cci']} [{', '.join(r['etf_triggers'])}]")
            print(f"    指数: J={r['idx_j']}, CCI={r['idx_cci']} [{', '.join(r['idx_triggers'])}]")

    if single_signals:
        print("\n--- 单边观察信号 ---")
        for r in single_signals:
            parts = []
            if r["etf_signal"]:
                parts.append(f"ETF[{r['etf_signal']}]")
            if r["idx_signal"]:
                parts.append(f"指数[{r['idx_signal']}]")
            print(f"  {r['etf_name']}({r['etf_code']}) → {', '.join(parts)}")

    if fx_signals:
        print("\n*** 贵金属/外汇单标信号（XAU）***")
        for r in fx_signals:
            print(f"  {r['fx_name']}({r['fx_code']}) → {r['fx_signal']}")
            print(f"    周线收盘={r['fx_close']}, J={r['fx_j']}, CCI={r['fx_cci']} "
                  f"[{', '.join(r['fx_triggers'])}]")

    # 推送
    print(f"\n{'='*70}")
    if push_single_buy and push_single_sell:
        print("[推送模式] 方案C: 双重确认 + 单边买入 + 单边卖出")
    elif push_single_buy:
        print("[推送模式] 方案B: 双重确认 + 单边买入")
    elif push_single_sell:
        print("[推送模式] 方案B+: 双重确认 + 单边卖出")
    else:
        print("[推送模式] 方案A: 仅双重确认（默认）")

    if not dry_run:
        push_signals(results, dry_run=False,
                     push_single_buy=push_single_buy,
                     push_single_sell=push_single_sell)
    else:
        print("[DRY-RUN] 不推送")
        push_signals(results, dry_run=True,
                     push_single_buy=push_single_buy,
                     push_single_sell=push_single_sell)

    # 报告
    if report:
        report_path = generate_html_report(results, date or get_latest_trade_date())
        return {"results": results, "report": report_path}

    return {"results": results}


def main():
    parser = argparse.ArgumentParser(description="奇点战法每日双重信号扫描")
    parser.add_argument("--dry-run", action="store_true", help="仅扫描不推送")
    parser.add_argument("--report", action="store_true", help="生成HTML报告")
    parser.add_argument("--date", type=str, default=None, help="扫描日期 YYYYMMDD (默认最近交易日)")
    # 推送分级开关（默认关 = 方案A: 仅双重确认）
    parser.add_argument("--push-single-buy", action="store_true",
                        help="方案B: 加推单边买入信号（带观察标记+纪律提示）")
    parser.add_argument("--push-single-sell", action="store_true",
                        help="方案C: 加推单边卖出信号（带仅观察标注）")
    args = parser.parse_args()

    run_scan_pipeline(
        date=args.date,
        dry_run=args.dry_run,
        report=args.report,
        push_single_buy=args.push_single_buy,
        push_single_sell=args.push_single_sell,
    )


if __name__ == "__main__":
    main()
