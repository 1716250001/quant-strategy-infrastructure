# -*- coding: utf-8 -*-
"""
tools/manager_profile.py — 基金经理风格画像（唯一保留的分析能力）

定位（2026-09-16 冻结）
--------------------
本范式仅保留一种用途：**判断基金经理"像谁"（风格/跟踪指数）并给出 β**。
α 测量已冻结（口径依赖过强，不可靠）。

输入：基金代码 或 经理姓名
输出：
  1. 风格画像 —— 与各候选指数的相关系数排序（"像谁"）
  2. 跟踪指数 —— 优先用基金自身业绩基准；主动基金用最高相关因子推断
  3. β        —— 对主导指数的暴露

用法
----
    python -m tools.manager_profile --code 004685
    python -m tools.manager_profile --manager 缪玮彬
    python -m tools.manager_profile --code 004685 --json
"""
import os, re, glob, json, argparse
import pandas as pd
import numpy as np

from common import reader

DATA = r"D:\全量数据\market_data"
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BRIDGE_DIR = os.path.join(_ROOT, "数据")
NAV = os.path.join(DATA, "fund_nav")
IDX = os.path.join(DATA, "index_daily")

# ---------- 候选指数池（扩充版：含风格/行业/微盘） ----------
CANDS = {
    "沪深300R": "H00300.CSI", "中证500R": "H00905.CSI", "中证1000R": "H00852.CSI",
    "中证2000R": "932000CNY010.CSI", "创业板R": "399606.SZ", "科创50": "000688.SH",
    "中证红利R": "H00922.CSI", "300价值": "000919.CSI", "300成长": "000918.CSI",
    "300波动": "000803.CSI", "CS质量成长": "930855.CSI", "300动量R": "H20260.CSI",
    "800医药R": "H00841.CSI", "800消费R": "H00932.CSI", "800信息R": "H00935.CSI",
    "800金融R": "H20086.CSI", "800能源R": "H00928.CSI", "800材料R": "H00929.CSI",
}


def _load_index(code):
    """读指数日线收益率序列。

    index_daily 已迁移为按年分区，不能再按 {code}.parquet 定位文件
    （旧写法会静默返回 None，导致风格/β 全部算不出）。
    """
    d = reader.read_code("index_daily", code,
                         columns=["trade_date", "close"], root=DATA)
    if d is None or d.empty:
        return None
    d = d.dropna()
    d["trade_date"] = d["trade_date"].astype(str)
    s = d.sort_values("trade_date").set_index("trade_date")["close"].pct_change().dropna()
    return s[s.abs() < 0.25]


def _load_micro():
    p = os.path.join(DATA, "custom_index", "micro_monthly.parquet")
    if not os.path.exists(p):
        return None
    d = pd.read_parquet(p)
    d["trade_date"] = d["trade_date"].astype(str)
    s = d.set_index("trade_date")["ret"]
    return s[s.abs() < 0.25]


def load_factor_panel():
    F, meta = {}, {}
    for k, c in CANDS.items():
        s = _load_index(c)
        if s is not None:
            F[k] = s; meta[k] = c
    m = _load_micro()
    if m is not None:
        F["微盘(自建)"] = m; meta["微盘(自建)"] = "custom_index/micro_monthly"
    return pd.DataFrame(F).dropna(), meta


def load_nav(code):
    """读取基金净值序列并返回日收益率。

    fund_nav 已改为按年分区，无法再按 \"{code}.parquet\" 定位文件。
    改为先从 fund_basic 元数据查出完整 ts_code（如 004685.OF），
    再用布局无关的 reader 读取。
    """
    code = str(code).split(".")[0][:6]
    try:
        fb = pd.read_parquet(os.path.join(DATA, "metadata", "fund_basic.parquet"))
        m = fb[fb["ts_code"].astype(str).str.startswith(code)]["ts_code"]
    except Exception:
        m = []
    if len(m) == 0:
        return None, code
    ts_code = str(m.iloc[0])
    d = reader.read_code("fund_nav", ts_code,
                         columns=["nav_date", "adj_nav", "unit_nav"], root=DATA)
    if d is None or d.empty:
        return None, code
    d = d.dropna(subset=["nav_date"])
    col = "adj_nav" if d["adj_nav"].notna().sum() > 20 else "unit_nav"
    d["dt"] = d["nav_date"].astype(str)
    s = d.dropna(subset=[col]).set_index("dt")[col]
    s = s[s > 0].sort_index()
    r = s.pct_change().dropna()
    return r[r.abs() < 0.25], code


def declared_benchmark(code):
    fb = pd.read_parquet(os.path.join(DATA, "metadata", "fund_basic.parquet"))
    r = fb[fb["ts_code"].astype(str).str.startswith(code)]
    if len(r) == 0:
        return "", ""
    return str(r.iloc[0].get("benchmark", "")), str(r.iloc[0].get("name", ""))


def analyze(ret, F):
    j = pd.concat([ret.rename("f"), F], axis=1, join="inner").dropna()
    if len(j) < 250:
        return None
    fc = list(F.columns)
    cors = {k: float(j["f"].corr(j[k])) for k in fc}
    # 单因子回归（β + R²）
    single = {}
    for k in fc:
        b = np.polyfit(j[k].values, j["f"].values, 1)
        pred = np.polyval(b, j[k].values)
        single[k] = dict(beta=float(b[0]), r2=float(1 - np.var(j["f"].values - pred) / np.var(j["f"].values)))
    # 多因子（非正交，β 可解释）
    X = np.column_stack([np.ones(len(j))] + [j[k].values for k in fc])
    cf, *_ = np.linalg.lstsq(X, j["f"].values, rcond=None)
    r2m = 1 - np.var(j["f"].values - X @ cf) / np.var(j["f"].values)
    return dict(n=len(j), cors=cors, single=single,
                betas={k: float(cf[1+i]) for i, k in enumerate(fc)}, r2_multi=float(r2m),
                begin=j.index[0], end=j.index[-1])


def _finish_end(s):
    return "2026-09-16" if str(s).strip() == "至今" else str(s).strip()


def _merge_tenure(B):
    """合并同一 (基金, 经理) 的连续任职段。
    天天基金按'共管组合变化'切段，导致同一人连续任职被拆开。
    仅在段间无空档时合并，有空档（离任后再任）则保留分离。
    """
    out = []
    for (fc, mgr), g in B.groupby(["fund_code", "manager"]):
        g = g.copy()
        g["b"] = pd.to_datetime(g["begin"], errors="coerce")
        g["e"] = pd.to_datetime(g["end"].map(_finish_end), errors="coerce")
        g = g.sort_values("b")
        cur_b, cur_e = g.iloc[0]["b"], g.iloc[0]["e"]
        cur_ret = g.iloc[0]["ret"]; cur_joint = g.iloc[0]["joint"]
        for _, r in g.iloc[1:].iterrows():
            gap = (r["b"] - cur_e).days
            if -1 <= gap <= 3:          # 连续（含相邻/微重叠）
                cur_e = max(cur_e, r["e"])
                cur_joint = cur_joint if "False" not in str(r["joint"]) else cur_joint
            else:                        # 有真实空档 → 结转
                out.append(dict(fund_code=fc, manager=mgr, company=g.iloc[0]["company"],
                                fund_name=g.iloc[0]["fund_name"], begin=cur_b,
                                end="至今" if cur_e >= pd.Timestamp("2026-09-15") else cur_e.strftime("%Y-%m-%d"),
                                ret=cur_ret, joint=cur_joint))
                cur_b, cur_e, cur_ret, cur_joint = r["b"], r["e"], r["ret"], r["joint"]
        out.append(dict(fund_code=fc, manager=mgr, company=g.iloc[0]["company"],
                        fund_name=g.iloc[0]["fund_name"], begin=cur_b,
                        end="至今" if cur_e >= pd.Timestamp("2026-09-15") else cur_e.strftime("%Y-%m-%d"),
                        ret=cur_ret, joint=cur_joint))
    return pd.DataFrame(out)


def profile(code=None, manager=None, topn=8, current_only=True, min_days=250):
    F, meta = load_factor_panel()
    # 选目标基金（含任期区间，画像必须限定在任期内）
    targets = []   # list of (code, begin, end, company)
    bp = os.path.join(BRIDGE_DIR, "基金经理桥表_任期段.csv")
    if code:
        targets = [(str(code).split(".")[0][:6], None, None, "")]
    elif manager:
        if not os.path.exists(bp):
            raise FileNotFoundError(f"桥表不存在: {bp}")
        B = pd.read_csv(bp, encoding="utf-8-sig", dtype=str)
        B = B[B["manager"] == manager]
        if len(B) == 0:
            print(f"未找到经理: {manager}")
            return
        Bm = _merge_tenure(B)                 # ★ 合并连续任职段
        if current_only:
            Bc = Bm[Bm["end"] == "至今"]
            if len(Bc):
                Bm = Bc
        Bm = Bm.assign(_span=(pd.to_datetime(Bm["end"].map(_finish_end), errors="coerce")
                              - pd.to_datetime(Bm["begin"], errors="coerce")).dt.days)
        Bm = Bm.sort_values("_span", ascending=False)
        targets = list(zip(Bm["fund_code"], Bm["begin"], Bm["end"], Bm["company"]))
        print(f"【{manager}】命中 {len(targets)} 只{'(仅当前在任)' if current_only else ''}"
              f"，公司: {sorted(set(Bm['company']))}")
        print(f"  画像窗口严格裁剪至各自连续任期\n")
    else:
        raise ValueError("需提供 code 或 manager")

    results = []
    for c, t_begin, t_end, company in targets:
        ret, code6 = load_nav(c)
        if ret is None:
            continue
        # ★ 关键：裁剪到任职区间，避免用离任后数据画像
        if t_begin:
            ret = ret[ret.index >= str(t_begin).replace("-", "")]
        if t_end and str(t_end).strip() != "至今":
            ret = ret[ret.index <= str(t_end).replace("-", "")]
        a = analyze(ret, F)
        if a is None or a["n"] < min_days:
            continue
        bm, name = declared_benchmark(code6)
        dom = max(a["cors"], key=lambda k: a["cors"][k])
        results.append(dict(code=code6, name=name, bench=bm, company=company,
                            tenure=f"{t_begin or '?'} ~ {t_end or '?'}", **a, dom=dom))

    if not results:
        print("无可用数据"); return

    # 输出
    for r in results:
        print("=" * 84)
        print(f"【{r['code']}】{r['name']}")
        print(f"  任职期: {r['tenure']}   |   画像样本 {r['n']} 日 ({r['begin']} ~ {r['end']})")
        if r["company"]:
            print(f"  所属公司: {r['company']}")
        print(f"  声明基准: {r['bench'][:70] if r['bench'] else '(无)'}")
        print(f"  \n  ● 像谁（相关系数 TOP8）")
        for k in sorted(r["cors"], key=lambda x: -r["cors"][x])[:8]:
            s = r["single"][k]
            bar = "█" * int(max(0, r["cors"][k]) * 30)
            print(f"    {k:<12} r={r['cors'][k]:+.3f}  β={s['beta']:+.3f}  R²={s['r2']:.3f}  {bar}")
        print(f"\n  ● 主导指数: {r['dom']}  （相关 {r['cors'][r['dom']]:.3f}）")
        print(f"  ● 多因子 R²: {r['r2_multi']:.3f}")

    # 经理汇总
    if manager and len(results) > 1:
        print("\n" + "=" * 84)
        print(f"【{manager} 汇总】{len(results)} 只基金")
        from collections import Counter
        cnt = Counter(r["dom"] for r in results)
        print("  主导指数分布:")
        for k, v in cnt.most_common():
            print(f"    {k:<12} {v} 只")
        avg = {k: np.mean([r["cors"][k] for r in results]) for k in F.columns}
        print("\n  平均相关 TOP5:")
        for k in sorted(avg, key=lambda x: -avg[x])[:5]:
            print(f"    {k:<12} {avg[k]:+.3f}")
        ab = {k: np.mean([r["betas"][k] for r in results]) for k in F.columns}
        print("\n  平均 β TOP5:")
        for k in sorted(ab, key=lambda x: -abs(ab[x]))[:5]:
            print(f"    {k:<12} {ab[k]:+.3f}")
    return results


def main(argv=None):
    """CLI 入口。

    argv: 参数列表（None 则取 sys.argv[1:]），便于从统一入口 main.py 调用。
    """
    ap = argparse.ArgumentParser(
        description="基金经理风格画像（风格/跟踪指数/β；α 测量已冻结）")
    ap.add_argument("--code", default=None, help="基金代码，如 004685")
    ap.add_argument("--manager", default=None, help="经理姓名，如 缪玮彬")
    ap.add_argument("--topn", type=int, default=8)
    ap.add_argument("--all", action="store_true", help="含历史任期（默认仅当前在任）")
    ap.add_argument("--min-days", type=int, default=250, help="任期内最少样本日")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    res = profile(a.code, a.manager, a.topn, current_only=not a.all, min_days=a.min_days)
    if a.json and res:
        print("\n" + json.dumps([{k: v for k, v in r.items() if k in ("code","name","n","dom","r2_multi")} for r in res],
                                ensure_ascii=False, indent=2))
    return res


if __name__ == "__main__":
    main()
