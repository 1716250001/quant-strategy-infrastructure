# -*- coding: utf-8 -*-
"""
tools/db_clean.py — 存量数据清理（版本冗余压缩）
==================================================
场景: 同一主键存有多个版本（历史脚本未按主键去重、直接追加所致），
      需压成"一期一行"，同时不丢任何字段的有效值。

目标清单在 config.DB_CLEAN_TARGETS。

用法:
  python main.py db-clean --dry-run            # 干跑（默认），只报告不改动
  python main.py db-clean --apply              # 执行（自动备份 + 逐文件校验）
  python main.py db-clean --only fina_indicator --apply
  python main.py db-clean --list               # 列出清理目标

安全设计:
  1. 默认 dry-run，必须显式 --apply 才写入
  2. --apply 时自动备份整个目录到 D:\\全量数据\\_backup_<name>_<日期>
  3. 逐文件写入临时文件 → 读回校验行数 → 才替换原文件
  4. 清理前后对比"监控字段非空率"，任一字段下降超阈值即整体告警
  5. 冲突组（组内两行都有值且不等）单独统计并提示核实版本策略

⚠ 策略选择纪律:
  哪个版本是"当前服务端口径"必须实测，不能假设"后写入=新版"。
  fina_indicator 实测 40/40 冲突组匹配"先写入"那版 → keep_first_nonnull。
"""
import os
import shutil
import time
from datetime import datetime

import pandas as pd

from config import MARKET_DATA_DIR, DB_CLEAN_TARGETS
from common.paths import ensure_dir

BACKUP_ROOT = os.path.join(os.path.dirname(MARKET_DATA_DIR.rstrip("\\/")), "_backup")
# 非空率下降超过该阈值(pct)即判定为"字段丢失"
WATCH_TOLERANCE_PCT = 0.5


# ============================================================
# 合并策略
# ============================================================
def merge_period(df, key, strategy="keep_first_nonnull"):
    """按 key 把多行压成一行。

    keep_first_nonnull: 组内 bfill 后取首行 = 每列取第一个非空值
        行1=NaN,行2=100 → bfill:100,100 → 取首行 100  (取到有效值)
        行1=50, 行2=NaN → bfill:50,NaN  → 取首行 50   (保留有值)
        两行都 NaN      → 仍 NaN        → 取首行 NaN
    keep_last_nonnull:  组内 ffill 后取末行 = 每列取最后一个非空值
    plain_first:        直接按主键取首行(不补空, 慎用)
    """
    if len(df) <= 1:
        return df
    d = df.copy()
    if not d.duplicated(subset=key).any():
        return d

    if strategy == "plain_first":
        return d.drop_duplicates(subset=key, keep="first").reset_index(drop=True)

    value_cols = [c for c in d.columns if c not in key]
    if strategy == "keep_last_nonnull":
        d[value_cols] = d.groupby(key, sort=False)[value_cols].ffill()
        return d.drop_duplicates(subset=key, keep="last").reset_index(drop=True)

    # 默认 keep_first_nonnull
    d[value_cols] = d.groupby(key, sort=False)[value_cols].bfill()
    return d.drop_duplicates(subset=key, keep="first").reset_index(drop=True)


# ============================================================
# 备份
# ============================================================
def backup_dir(dir_path, name, verbose=True):
    """整目录备份到 D:\\全量数据\\_backup\\<name>_<日期>，返回路径(失败返回None)"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    dst = os.path.join(BACKUP_ROOT, f"{name}_{stamp}")
    if os.path.exists(dst):
        if verbose:
            print(f"  [备份] 目标已存在, 复用: {dst}")
        return dst
    size = 0
    for f in os.listdir(dir_path):
        p = os.path.join(dir_path, f)
        if os.path.isfile(p):
            size += os.path.getsize(p)
    free = shutil.disk_usage(os.path.dirname(dir_path)).free
    if free < size * 1.5:
        print(f"  [备份] ⛔ 空间不足(需{size/1024**2:.0f}MB, 剩{free/1024**3:.1f}GB), 中止")
        return None
    ensure_dir(BACKUP_ROOT)
    if verbose:
        print(f"  [备份] {len(os.listdir(dir_path))} 文件 / {size/1024**2:.1f} MB → {dst}")
    shutil.copytree(dir_path, dst)
    return dst


# ============================================================
# 单目标清理
# ============================================================
def clean_target(name, conf, apply=False, backup=True, verbose=True):
    """清理单个目标。返回结果字典。"""
    dir_path = os.path.join(MARKET_DATA_DIR, name)
    if not os.path.isdir(dir_path):
        print(f"\n[{name}] ⛔ 目录不存在: {dir_path}")
        return None

    key = conf["key"]
    strategy = conf.get("strategy", "keep_first_nonnull")
    watch = conf.get("watch") or []
    date_col = conf.get("date_col")

    files = sorted(f for f in os.listdir(dir_path) if f.endswith(".parquet"))
    if not files:
        print(f"\n[{name}] 空目录, 跳过")
        return None

    mode = "执行" if apply else "干跑"
    print("\n" + "=" * 74)
    print(f"  [{name}] {conf.get('desc','')} — {mode}")
    print("=" * 74)
    print(f"  文件数: {len(files)} | 主键: {key} | 策略: {strategy}")
    if conf.get("note"):
        print(f"  成因: {conf['note']}")

    # 备份
    bk = None
    if apply and backup:
        bk = backup_dir(dir_path, name, verbose)
        if bk is None:
            print("  ⛔ 备份失败, 中止该目标")
            return None

    tot_before = tot_after = dup_full = 0
    watch_b = {c: 0 for c in watch}
    watch_a = {c: 0 for c in watch}
    conflict = {c: 0 for c in watch}
    conflict_groups = 0
    changed = 0
    bad = []
    t0 = time.time()

    for i, f in enumerate(files, 1):
        fp = os.path.join(dir_path, f)
        try:
            df = pd.read_parquet(fp)
        except Exception as e:
            bad.append((f, f"读取失败: {str(e)[:40]}"))
            continue
        if len(df) == 0:
            continue

        n0 = len(df)
        tot_before += n0
        dup_full += int(df.duplicated().sum())
        for c in watch:
            if c in df.columns:
                watch_b[c] += int(df[c].notna().sum())

        # 冲突检测: 组内某监控列两行都有值且不等
        dm = df.duplicated(subset=key, keep=False)
        if dm.any():
            for _, g in df[dm].groupby(key, sort=False):
                hit = False
                for c in watch:
                    if c in g.columns:
                        v = g[c].dropna()
                        if len(v) >= 2 and v.nunique() > 1:
                            conflict[c] += 1
                            hit = True
                if hit:
                    conflict_groups += 1

        merged = merge_period(df, key, strategy)
        n1 = len(merged)
        tot_after += n1
        for c in watch:
            if c in merged.columns:
                watch_a[c] += int(merged[c].notna().sum())
        if n1 != n0:
            changed += 1

        if apply and n1 < n0:
            tmp = fp + ".tmp"
            try:
                merged.to_parquet(tmp, index=False)
                chk = pd.read_parquet(tmp)
                if len(chk) != n1:
                    bad.append((f, f"写回校验失败 {len(chk)}!={n1}"))
                    os.remove(tmp)
                    continue
                os.replace(tmp, fp)
            except Exception as e:
                bad.append((f, f"写入失败: {str(e)[:40]}"))
                if os.path.exists(tmp):
                    os.remove(tmp)
                continue

        if verbose and (i % 1000 == 0 or i == len(files)):
            print(f"    {i:6d}/{len(files):<6d} 原={tot_before:>10,d} 后={tot_after:>10,d} "
                  f"省={tot_before-tot_after:>9,d} | {time.time()-t0:.0f}s")

    el = time.time() - t0
    red = tot_before - tot_after
    print(f"\n  原行数:     {tot_before:,}")
    print(f"  清理后:     {tot_after:,}")
    print(f"  减少:       {red:,} ({red/tot_before*100 if tot_before else 0:.1f}%)")
    print(f"  整行相同:   {dup_full:,} ({dup_full/tot_before*100 if tot_before else 0:.1f}%)")
    print(f"  受影响文件: {changed}/{len(files)} | 耗时: {el:.1f}s")

    ok = True
    if watch:
        print(f"\n  监控字段完整度（按非空率, 分母已变）:")
        print(f"    {'字段':20s} {'清前':>9s} {'清后':>9s} {'变化':>9s} 判定")
        print("    " + "-" * 62)
        for c in watch:
            rb = watch_b[c] / tot_before * 100 if tot_before else 0
            ra = watch_a[c] / tot_after * 100 if tot_after else 0
            d = ra - rb
            if d < -WATCH_TOLERANCE_PCT:
                verd = "⛔ 完整度下降"
                ok = False
            elif abs(d) <= WATCH_TOLERANCE_PCT:
                verd = "✓ 无损"
            else:
                verd = "✓ 提升"
            print(f"    {c:20s} {rb:8.2f}% {ra:8.2f}% {d:+8.2f}pct {verd}")

    if conflict_groups:
        print(f"\n  ⚠ 冲突组(两行都有值且不等): {conflict_groups:,}")
        print(f"    涉及字段: { {k: v for k, v in conflict.items() if v} }")
        print(f"    当前策略「{strategy}」保留的版本 = {conf.get('version_note','见 config 注释')}")
        print("    提示: 若未用现拉数据验证过版本归属, 请先核实再执行")

    if bad:
        print(f"\n  ⚠ 异常文件 {len(bad)}: {bad[:5]}")
        ok = False

    if apply and bk:
        print(f"\n  备份: {bk}")
    print(f"  判定: {'✓ 通过' if ok else '⚠ 需人工确认'}")

    return {"name": name, "before": tot_before, "after": tot_after,
            "reduced": red, "ok": ok, "backup": bk, "bad": len(bad)}


# ============================================================
# 主流程
# ============================================================
def run_clean(targets=None, apply=False, backup=True, verbose=True):
    """执行清理。targets=None 表示全部目标。"""
    print("=" * 74)
    print(f"  存量数据清理（版本冗余压缩）— {'执行模式' if apply else '干跑模式'}")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  存储: {MARKET_DATA_DIR}")
    if apply:
        print(f"  备份: 开启 → {BACKUP_ROOT}")
    else:
        print("  提示: 当前为干跑, 不改动任何文件; 加 --apply 才写入")
    print("=" * 74)

    sel = [(n, c) for n, c in DB_CLEAN_TARGETS.items()
           if targets is None or n in targets]
    if not sel:
        print("\n  没有匹配的清理目标")
        return []

    results = []
    for name, conf in sel:
        try:
            r = clean_target(name, conf, apply=apply, backup=backup, verbose=verbose)
            if r:
                results.append(r)
        except KeyboardInterrupt:
            print("\n  [中断] 已处理的部分已写入(逐文件提交), 可重跑续做")
            break
        except Exception as e:
            print(f"\n  [ERROR] {name}: {type(e).__name__}: {str(e)[:100]}")

    if len(results) > 1:
        print("\n" + "=" * 74)
        print("  汇总")
        print("=" * 74)
        for r in results:
            print(f"  {r['name']:20s} {r['before']:>10,d} → {r['after']:>10,d} "
                  f"(省 {r['reduced']:,})  {'✓' if r['ok'] else '⚠'}")
    return results
