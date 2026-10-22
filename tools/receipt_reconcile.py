#!/usr/bin/env python3
"""
receipt_reconcile.py — 回执对账断言工具（§64.4 15-B）
纪律: 回执前必须跑此脚本，把输出原样贴入回执，不接受人工统计

CLI:
  python -m tools.receipt_reconcile --new <parquet> [--old <parquet>] --key <col> [--dims col1,col2,...]

输出:
  ① 行数 / 唯一键数
  ② 集合差（仅旧 / 仅新，带样本）
  ③ 每个 --dims 列的变更数
  ④ 空值计数（每列）

退出码:
  0 = 正常输出
  2 = 输入/列名错误

依赖: pandas + pyarrow（跟随主 venv），不引第三方
"""
import argparse, sys, os, hashlib
import pandas as pd
import numpy as np

def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()

def load(path):
    if not os.path.exists(path):
        print(f"❌ 路径不存在: {path}")
        sys.exit(2)
    return pd.read_parquet(path)

def main():
    p = argparse.ArgumentParser(description="回执对账断言工具")
    p.add_argument("--old", help="旧 parquet 路径（可省略表示仅统计新表）")
    p.add_argument("--new", required=True, help="新 parquet 路径")
    p.add_argument("--key", required=True, help="唯一键列（如 ts_code / index_code+con_code）")
    p.add_argument("--dims", default="", help="要比较变更的列（逗号分隔，如 l1_code,l2_code）")
    p.add_argument("--samples", type=int, default=10, help="集合差样本数")
    args = p.parse_args()

    new = load(args.new)
    old = load(args.old) if args.old else None

    # 列名检查
    key_cols = [c.strip() for c in args.key.split(',')]
    for c in key_cols:
        if c not in new.columns:
            print(f"❌ new 表无列: {c}（可用: {list(new.columns)}）")
            sys.exit(2)
        if old is not None and c not in old.columns:
            print(f"❌ old 表无列: {c}")
            sys.exit(2)

    dim_cols = [c.strip() for c in args.dims.split(',') if c.strip()]
    for c in dim_cols:
        if c not in new.columns:
            print(f"❌ new 表无 dim 列: {c}")
            sys.exit(2)

    print(f"{'='*72}")
    print(f"receipt_reconcile — §64.4 回执对账断言工具")
    print(f"{'='*72}")

    # ① 行数 + 唯一键数
    print(f"\n{'─'*72}")
    print(f"① 行数 / 唯一键数")
    print(f"{'─'*72}")
    print(f"  new 行数:          {len(new):,}")
    print(f"  new 唯一键数:      {new[key_cols].drop_duplicates().shape[0]:,}")
    for c in key_cols:
        dup = len(new) - new[c].nunique()
        print(f"    {c} 重复:        {dup}")
    print(f"  new 文件 sha256:   {sha256(args.new)[:16]}...")
    if old is not None:
        print(f"  old 行数:          {len(old):,}")
        print(f"  old 唯一键数:      {old[key_cols].drop_duplicates().shape[0]:,}")
        print(f"  old 文件 sha256:   {sha256(args.old)[:16]}...")
        print(f"  行数差 (new-old):  {len(new)-len(old):+,}")

    # ② 集合差
    if old is not None:
        print(f"\n{'─'*72}")
        print(f"② 集合差（按 {key_cols} 唯一键）")
        print(f"{'─'*72}")
        old_keys = set(old[key_cols].agg(tuple, axis=1) if len(key_cols)>1 else old[key_cols[0]].tolist())
        new_keys = set(new[key_cols].agg(tuple, axis=1) if len(key_cols)>1 else new[key_cols[0]].tolist())
        only_old = sorted(old_keys - new_keys)
        only_new = sorted(new_keys - old_keys)
        common = old_keys & new_keys
        print(f"  仅旧有 (removed):   {len(only_old):,}")
        print(f"  仅新有 (added):     {len(only_new):,}")
        print(f"  共有 (common):      {len(common):,}")
        if only_old:
            print(f"  仅旧样本（{min(args.samples, len(only_old))}）: {only_old[:args.samples]}")
        if only_new:
            print(f"  仅新样本（{min(args.samples, len(only_new))}）: {only_new[:args.samples]}")

    # ③ dims 列变更
    if old is not None and dim_cols:
        print(f"\n{'─'*72}")
        print(f"③ 共有行中各列变更（按 {key_cols} 对齐）")
        print(f"{'─'*72}")
        old_map = old.set_index(key_cols) if len(key_cols) > 1 else old.set_index(key_cols[0])
        new_map = new.set_index(key_cols) if len(key_cols) > 1 else new.set_index(key_cols[0])
        common_keys = sorted(common)
        for c in dim_cols:
            if c not in old_map.columns:
                print(f"  {c}: (old 表无此列)")
                continue
            diff_count = 0
            nan_nan = 0
            for k in common_keys:
                ov = old_map.loc[k, c]
                nv = new_map.loc[k, c]
                if pd.isna(ov) and pd.isna(nv):
                    nan_nan += 1
                    continue
                if pd.isna(ov) != pd.isna(nv):
                    diff_count += 1
                    continue
                if isinstance(ov, (int, float)) and isinstance(nv, (int, float)):
                    if ov != nv:
                        diff_count += 1
                else:
                    if str(ov) != str(nv):
                        diff_count += 1
            print(f"  {c}: 变更 {diff_count:,} / 共有 {len(common_keys):,} (nan→nan {nan_nan})")
            if diff_count and diff_count <= 20:
                changed_samples = []
                shown = 0
                for k in common_keys:
                    ov = old_map.loc[k, c]
                    nv = new_map.loc[k, c]
                    if not ((pd.isna(ov) and pd.isna(nv)) or (not pd.isna(ov) and not pd.isna(nv) and str(ov)==str(nv))):
                        changed_samples.append(f"    {k}: {ov} → {nv}")
                        shown += 1
                        if shown >= args.samples:
                            break
                for s in changed_samples:
                    print(s)

    # ④ 空值计数
    print(f"\n{'─'*72}")
    print(f"④ 每列空值计数")
    print(f"{'─'*72}")
    for col in new.columns:
        n_total = len(new)
        n_null = new[col].isna().sum()
        non_null = n_total - n_null
        print(f"  {col}: {non_null:,} 非空 / {n_null:,} 空 ({100*non_null/n_total:.1f}%)")

    print(f"\n{'='*72}")
    print(f"完成 (RC=0)")
    print(f"{'='*72}")

if __name__ == "__main__":
    main()
