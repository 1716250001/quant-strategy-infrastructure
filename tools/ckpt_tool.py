# -*- coding: utf-8 -*-
"""
tools/ckpt_tool.py — 断点管理
==============================
把补数过程中反复手写的两类断点修复沉淀为正式命令。

背景（两个实测问题）:
  ① **断点虚记**：断点把"调用成功但返空"也记为完成（三态纪律的设计行为），
     导致"断点说完成但磁盘无数据" → `backfill` 静默跳过，缺口永不补。
     实证：23 表 / 5551 项。
  ② **断点滞后**：by_date 模式的 `cp.save()` 只在主流程末尾调用，
     进程被超时 kill 时"数据已落盘但断点未保存" → 续跑重拉已完成日期。
     实证：eco_cal 磁盘 1863 个日期 vs 断点 1123。

三个动作:
  --show    显示断点 vs 磁盘的交叉核对（只读）
  --fix     按磁盘实际数据**回填**断点（幂等，只增不减）
  --clear   清除"断点已记但磁盘无数据"的条目（使续跑能重新拉取）

用法:
  python main.py ckpt --show
  python main.py ckpt --show --table eco_cal,margin
  python main.py ckpt --fix --table eco_cal                 # 干跑
  python main.py ckpt --fix --table eco_cal --execute       # 执行
  python main.py ckpt --clear --table dividend --dry-run
"""
import glob
import json
import os
import shutil
import sys
from datetime import datetime

import pandas as pd

from config import (MARKET_DATA_DIR, BACKFILL_TARGETS, DAILY_UPDATE_EXTRA,
                    TABLE_DATE_COL)

CKPT = os.path.join(MARKET_DATA_DIR, ".backfill_checkpoint.json")

# 本工具能交叉核对的取数模式。
# 其余模式（once / paged / by_param / by_period / by_range / by_month）的
# 断点键是"页号/枚举值/报告期/区间"等，缺少与磁盘一一对应的比对口径，
# 故不做核对 —— 但必须在输出里**显式标注**，否则用户会误以为
# "没报问题 = 该表没问题"（2026-09-19 独立审查 I5）。
COVERED_MODES = ("by_date", "by_code")


def _load():
    if not os.path.exists(CKPT):
        return {}
    return json.load(open(CKPT, encoding="utf-8"))


def _backup_and_save(cp):
    bak = CKPT + f".bak_{datetime.now():%Y%m%d_%H%M%S}"
    shutil.copy2(CKPT, bak)
    with open(CKPT, "w", encoding="utf-8") as f:
        json.dump(cp, f, ensure_ascii=False, indent=1)
    return bak


def _conf_of(name):
    return BACKFILL_TARGETS.get(name) or DAILY_UPDATE_EXTRA.get(name)


def _disk_dates(name, date_col):
    """磁盘上实际有数据的日期集合（用于 by_date/by_range 类）"""
    d = os.path.join(MARKET_DATA_DIR, name)
    if not os.path.isdir(d) or not date_col:
        return None
    out = set()
    for f in os.listdir(d):
        if not f.endswith(".parquet"):
            continue
        try:
            s = pd.read_parquet(os.path.join(d, f), columns=[date_col])[date_col]
            out |= set(s.astype(str).unique())
        except Exception:
            continue
    return out


def _disk_codes(name):
    """磁盘上实际有数据的代码集合（用于 by_code 类）"""
    from common import reader
    reader.clear_cache(name)
    try:
        return set(reader.codes_from_store(name, root=MARKET_DATA_DIR))
    except Exception:
        return set()


def _recent_trade_dates(n=1):
    """最近 n 个已收盘交易日（用于排除"T日数据源未发布"导致的误报）。

    为什么需要: 两融/港股通等接口当晚才更新，T 日断点记"完成"属合理，
    若把这类差异都算虚记，会每天产生稳定的假报警（"狼来了"效应）。
    与 db_audit 的"排除基准日当天"口径一致。
    """
    try:
        from common.calendar import open_dates
        today = datetime.now().strftime("%Y%m%d")
        ds = [d for d in open_dates(start_date="20200101") if d <= today]
        return set(ds[-n:]) if ds else set()
    except Exception:
        return set()


def _tasks_of(name, conf):
    """应有的任务集合（复用 backfill 的构建逻辑，保证口径一致）"""
    try:
        from fetch.backfill_spec import build_tasks, expected_task_count
    except Exception:
        return None, None
    try:
        exp, kind = expected_task_count(name, conf)
        return (set(str(x) for x in exp) if exp else None), kind
    except Exception:
        return None, None


# ============================================================
# --show：交叉核对
# ============================================================
def _do_show(tables=None, verbose=True):
    cp = _load()
    names = tables or sorted(cp.keys())
    names = [n for n in names if n in BACKFILL_TARGETS or n in DAILY_UPDATE_EXTRA]

    if verbose:
        print("=" * 100)
        print(f"  断点 × 磁盘 交叉核对（{CKPT}）")
        print("=" * 100)
        print(f"  {'表名':18s} {'模式':9s} {'断点':>8s} {'磁盘':>8s} "
              f"{'虚记':>7s} {'滞后':>7s}  说明")
        print("-" * 100)

    rows = []
    for name in names:
        conf = _conf_of(name)
        if not conf:
            continue
        mode = conf.get("mode")
        done = set(str(x) for x in cp.get(name, {}).get("completed", []))
        dc = conf.get("date_col") or TABLE_DATE_COL.get(name) or "trade_date"

        # 磁盘口径
        if mode in ("by_date",):
            disk = _disk_dates(name, dc)
        elif mode == "by_code":
            disk = _disk_codes(name)
        else:
            disk = None      # 其余模式无对应口径（见 COVERED_MODES 说明）

        # 应有任务
        exp, kind = _tasks_of(name, conf)

        fake = stale = None
        note = ""
        recent = _recent_trade_dates(1)      # 排除 T 日（数据源可能未发布）
        if disk is not None and exp:
            if mode == "by_code":
                fake = len([x for x in exp if x not in disk and x in done])
                stale = len([x for x in disk if x not in done])
            else:
                known = exp
                # 虚记：断点已记、应有任务、磁盘无数据 —— 但排除最近交易日
                fake = len([x for x in known
                            if x not in disk and x in done and x not in recent])
                disk_in = {x for x in disk if x in known}
                stale = len([x for x in disk_in if x not in done])
            if fake:
                note = "⚠ 疑似虚记（断点已记但磁盘无数据，需核验）"
            if stale:
                note += ("  " if note else "") + "· 断点滞后（磁盘有数据未记）"
        # ⚠ 必须在 disk 分支**之外**：未覆盖模式的 disk 恒为 None，
        #   若写在分支内则永远不会执行（实测踩过）。
        if mode not in COVERED_MODES:
            note += ("  " if note else "") + "· 本工具不核对该模式"

        n_disk = len(disk) if disk is not None else -1
        if verbose:
            f_show = f"{fake:,}" if fake is not None else "-"
            s_show = f"{stale:,}" if stale is not None else "-"
            print(f"  {name:18s} {str(mode):9s} {len(done):8,d} "
                  f"{n_disk if n_disk >= 0 else '—':>8} {f_show:>7s} {s_show:>7s}  {note}")
        rows.append({"name": name, "mode": mode, "done": len(done),
                     "disk": n_disk, "fake": fake, "stale": stale})

    if verbose:
        tf = sum(x["fake"] for x in rows if x["fake"])
        ts = sum(x["stale"] for x in rows if x["stale"])
        print("-" * 100)
        print(f"  合计: 疑似虚记 {tf:,} 项 | 断点滞后 {ts:,} 项")

        # 未覆盖模式汇总（避免"没报=没问题"的误解 —— I5）
        unc = {}
        for x in rows:
            if x.get("mode") not in COVERED_MODES:
                unc.setdefault(x["mode"], []).append(x["name"])
        if unc:
            n_t = sum(len(v) for v in unc.values())
            print()
            print(f"  ⚠ 以下 {len(unc)} 种取数模式（共 {n_t} 张表）**本工具不做交叉核对**，")
            print(f"     其\"—\"不代表正常，仅表示缺少比对口径（任务数少、代价低）：")
            for m in sorted(unc, key=lambda x: str(x)):
                names = unc[m]
                print(f"       {str(m):10s} {len(names):3d} 张  {names[:4]}"
                      + (" ..." if len(names) > 4 else ""))

        print()
        print("  ⚠ 关于\"疑似虚记\"：断点已记但磁盘无数据。**这不必然是故障** ——")
        print("     源端确实无数据的日期（如港股通休市日：耶稣受难日/圣诞/香港回归日）")
        print("     也符合该特征。实测 hk_hold 的 72 个差异日期现拉全部返 0 行，")
        print("     属三态纪律的正常设计行为。判定真伪需现拉核验。")
        print()
        print("  修复:  --fix    按磁盘回填滞后（幂等，只增不减）")
        print("         --clear  清疑似虚记（续跑会重拉这些任务以核验）")
        print("         --execute 才真正写入，默认干跑")
    return rows


# ============================================================
# --fix：按磁盘回填断点（幂等，只增不减）
# ============================================================
def _do_fix(tables=None, execute=False, verbose=True, details_out=None):
    cp = _load()
    names = tables or sorted(cp.keys())
    if verbose:
        print("=" * 100)
        print(f"  断点回填（按磁盘实际数据）| {'正式执行' if execute else 'DRY-RUN 预演'}")
        print("=" * 100)

    total = 0
    for name in names:
        conf = _conf_of(name)
        if not conf or conf.get("mode") != "by_date":
            continue
        dc = conf.get("date_col") or TABLE_DATE_COL.get(name) or "trade_date"
        disk = _disk_dates(name, dc)
        if not disk:
            continue
        done = set(str(x) for x in cp.get(name, {}).get("completed", []))
        add = sorted(disk - done)
        if not add:
            continue
        total += len(add)
        if verbose:
            print(f"\n  [{name}] 断点 {len(done):,} → 磁盘 {len(disk):,} "
                  f"（补记 {len(add):,}）")
            print(f"      样例: {add[:5]}")
        if details_out is not None:
            details_out.append({"table": name, "checkpoint": len(done),
                                "disk": len(disk), "added": len(add),
                                "samples": [str(x) for x in add[:5]]})
        if execute:
            cp.setdefault(name, {})["completed"] = sorted(done | disk)

    if verbose:
        print()
        if not total:
            print("  ✓ 无需回填（断点已与磁盘一致）")
            return 0
        if execute:
            bak = _backup_and_save(cp)
            print(f"  已回填 {total:,} 项")
            print(f"  备份: {os.path.basename(bak)}")
        else:
            print(f"  [DRY-RUN] 将回填 {total:,} 项，未写入")
            print(f"  执行: python main.py ckpt --fix --execute"
                  + (f" --table {','.join(tables)}" if tables else ""))
    return total


# ============================================================
# --clear：清虚记（断点已记但磁盘无数据）
# ============================================================
def _do_clear(tables=None, execute=False, verbose=True, details_out=None):
    cp = _load()
    names = tables or sorted(cp.keys())
    if verbose:
        print("=" * 100)
        print(f"  清除疑似虚记（断点已记但磁盘无数据）| "
              f"{'正式执行' if execute else 'DRY-RUN 预演'}")
        print("=" * 100)
        print("  说明: 清除后续跑会重拉这些任务以核验。若源端确实无数据，")
        print("        重拉会再次记断点（该流程幂等，但会消耗等量请求）。")
        print()

    total = 0
    recent = _recent_trade_dates(1)          # 排除 T 日（数据源可能未发布）
    for name in names:
        conf = _conf_of(name)
        if not conf:
            continue
        mode = conf.get("mode")
        done = set(str(x) for x in cp.get(name, {}).get("completed", []))
        if not done:
            continue
        exp, kind = _tasks_of(name, conf)

        if mode == "by_code":
            disk = _disk_codes(name)
            if not disk:
                continue
            fake = sorted(x for x in done if (not exp or x in exp) and x not in disk)
        elif mode == "by_date":
            dc = conf.get("date_col") or TABLE_DATE_COL.get(name) or "trade_date"
            disk = _disk_dates(name, dc)
            if disk is None or not exp:
                continue
            fake = sorted(x for x in done
                          if x in exp and x not in disk and x not in recent)
        else:
            continue

        if not fake:
            continue
        total += len(fake)
        if verbose:
            print(f"\n  [{name}] 清除 {len(fake):,} 项（断点 {len(done):,}）")
            print(f"      样例: {fake[:5]}")
        if details_out is not None:
            details_out.append({"table": name, "cleared": len(fake),
                                "checkpoint": len(done),
                                "samples": [str(x) for x in fake[:5]]})
        if execute:
            cp.setdefault(name, {})["completed"] = sorted(done - set(fake))

    if verbose:
        print()
        if not total:
            print("  ✓ 无虚记条目")
            return 0
        if execute:
            bak = _backup_and_save(cp)
            print(f"  已清除 {total:,} 项 → 续跑将重新拉取这些任务")
            print(f"  备份: {os.path.basename(bak)}")
        else:
            print(f"  [DRY-RUN] 将清除 {total:,} 项，未写入")
            print(f"  执行: python main.py ckpt --clear --execute"
                  + (f" --table {','.join(tables)}" if tables else ""))
    return total


# ============================================================
# 入口
# ============================================================
def run_ckpt(show=False, fix=False, clear=False, table=None,
             dry_run=False, execute=False, as_json=False):
    """断点管理（show=交叉核对, fix=回填, clear=清虚记）。

    as_json: stdout 输出结果 JSON（show 明细 + fix/clear 统计与明细）
    """
    tables = [x.strip() for x in table.split(",")] if table else None
    if not (show or fix or clear):
        show = True                      # 默认展示

    # --dry-run 显式给出时不写入；--execute 才写入
    do_write = bool(execute) and not dry_run

    show_rows = fix_info = clear_info = None
    did = False
    if show:
        show_rows = _do_show(tables=tables, verbose=not as_json)
        did = True
    if fix:
        if did and not as_json:
            print()
        det = [] if as_json else None
        fix_info = {"total": _do_fix(tables=tables, execute=do_write,
                                     verbose=not as_json, details_out=det),
                    "execute": do_write, "details": det or []}
        did = True
    if clear:
        if did and not as_json:
            print()
        det = [] if as_json else None
        clear_info = {"total": _do_clear(tables=tables, execute=do_write,
                                         verbose=not as_json, details_out=det),
                      "execute": do_write, "details": det or []}
    if as_json:
        from common.jsonio import print_json
        out = {"dry_run": not do_write}
        if show_rows is not None:
            out["show"] = show_rows
        if fix_info is not None:
            out["fix"] = fix_info
        if clear_info is not None:
            out["clear"] = clear_info
        print_json(out, indent=1)
    return 0
