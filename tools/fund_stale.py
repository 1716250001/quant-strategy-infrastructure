# -*- coding: utf-8 -*-
"""基金 source_empty 注册表对账（CC-FIX-1 / §320 F1）。

判定**与 R17/R18 同源**：直接调 `check_coverage.check_fund_history` 的
`stale_registered`（注册为「源端空」但库内已有数据），不另写一套规则。

用法：
  python main.py db stale-fix            # 干跑：只列 stale
  python main.py db stale-fix --apply    # 清理并同步 evidence / count（自备份）
"""
import os, json, shutil, datetime

KG_PATH = os.path.join(os.path.dirname(__file__), "known_gaps.json")
TABLES = ("fund_daily", "fund_nav", "fund_adj", "fund_share")


def detect():
    """返回 [{table, stale:[code...], stale_late:[code...]}]（stale 为可清理项）。"""
    from tools.check_coverage import check_fund_history

    out = []
    for t in TABLES:
        rc, det = check_fund_history(strict=True, return_detail=True, scope=t)
        d = det.get(t, {})
        out.append({"table": t,
                    "stale": list(d.get("stale_registered") or []),
                    "stale_late": list(d.get("stale_late") or [])})
    return out


def _ev_codes(ev):
    """evidence 两种形态：dict{code:...}（fund_daily/nav/adj）与 list[{ts_code:...}]（fund_share）。"""
    if isinstance(ev, dict):
        return set(ev.keys())
    return {str(x.get("ts_code") or x.get("code")) for x in ev}


def _sync_invariants(kg, table):
    """单表不变量：source_empty 与 evidence 逐码相等；count（若有）等于长度。"""
    reg = set(map(str, kg[table].get("source_empty", [])))
    assert reg == _ev_codes(kg[table].get("source_empty_evidence", {})), \
        f"{table} 注册表/证据错位"
    if "source_empty_count" in kg[table]:
        assert kg[table]["source_empty_count"] == len(reg), \
            f"{table} source_empty_count={kg[table]['source_empty_count']} != {len(reg)}"


def apply_fix(det, backup_dir=None, verbose=True):
    """按 detect 结果清理；先备份，原子写入；返回回执 dict。"""
    kg = json.load(open(KG_PATH, encoding="utf-8"))
    changed = {x["table"]: x["stale"] for x in det if x["stale"]}
    if not changed:
        return {"changed": False, "backup": None, "tables": {}}

    backup_dir = backup_dir or os.path.join(
        r"D:\量化策略\备份\归档文件",
        "bak-%s-pre-stale-fix" % datetime.datetime.now().strftime("%Y%m%d"))
    os.makedirs(backup_dir, exist_ok=True)
    bak = os.path.join(backup_dir, "known_gaps.json")
    shutil.copy2(KG_PATH, bak)

    info = {}
    for t, codes in changed.items():
        _sync_invariants(kg, t)
        before = len(kg[t].get("source_empty", []))
        drop = set(map(str, codes))
        kg[t]["source_empty"] = [c for c in kg[t]["source_empty"] if str(c) not in drop]
        ev = kg[t].get("source_empty_evidence", {})
        if isinstance(ev, dict):
            for c in drop:
                ev.pop(c, None)
        else:
            kg[t]["source_empty_evidence"] = [
                x for x in ev if str(x.get("ts_code") or x.get("code")) not in drop]
        if "source_empty_count" in kg[t]:
            kg[t]["source_empty_count"] = len(kg[t]["source_empty"])
        _sync_invariants(kg, t)
        info[t] = {"removed": sorted(drop), "before": before,
                   "after": len(kg[t]["source_empty"])}

    tmp = KG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(kg, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, KG_PATH)
    if verbose:
        for t, v in info.items():
            print(f"  {t}: {v['before']} → {v['after']}（删 {len(v['removed'])}: "
                  f"{','.join(v['removed'][:5])}{'…' if len(v['removed']) > 5 else ''}）")
        print(f"  备份: {bak}")
    return {"changed": True, "backup": bak, "tables": info}
