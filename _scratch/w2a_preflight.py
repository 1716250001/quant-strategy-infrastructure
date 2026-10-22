"""
W-2a 闸口 1 + 闸口 2：迁移前评估 + 全量快照（§70.5）
迁移目标:
  源: market_data\\wind_index\\ (daily 4,142 parquet + spx + tasi_weekly + ibov + enum_probe)
  目标: wind_data\\norm\\series\\ + wind_data\\norm\\snap\\

引用文件清单（22 个）: 见 §70.5 表格
"""
import os, json, hashlib, glob, datetime as dt

MARKET_ROOT = r"D:\全量数据\market_data\wind_index"
SERIES_TARGET = r"D:\全量数据\wind_data\norm\series"
SNAP_TARGET = r"D:\全量数据\wind_data\norm\snap"
BACKUP_DIR = r"D:\全量数据\wind_data\_meta"

# ── 1. 目录结构实测 ────────────────────────────────────────
print("=== 闸口 1: 源目录结构 ===")
subdirs = [d for d in os.listdir(MARKET_ROOT)
           if os.path.isdir(os.path.join(MARKET_ROOT, d))]
print(f"  wind_index 下子目录: {subdirs}")
for sd in subdirs:
    full = os.path.join(MARKET_ROOT, sd)
    files = [f for f in os.listdir(full) if os.path.isfile(os.path.join(full, f))]
    size = sum(os.path.getsize(os.path.join(full, f)) for f in files)
    print(f"    {sd}: {len(files)} 个文件 / {size/1024/1024:.1f} MB")

# ── 2. 四道闸口：文件级快照 ──────────────────────────────
print("\n=== 闸口 2: 全量快照（文件数 + 逐文件 size + mtime + sha256）===")
snapshot = {}
total_files = 0
for root, dirs, files in os.walk(MARKET_ROOT):
    for f in files:
        fp = os.path.join(root, f)
        rel = os.path.relpath(fp, MARKET_ROOT)
        stat = os.stat(fp)
        h = hashlib.sha256()
        with open(fp, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        snapshot[rel] = {
            "size": stat.st_size,
            "mtime": dt.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            "sha256": h.hexdigest(),
        }
        total_files += 1
        if total_files % 500 == 0:
            print(f"  ... {total_files} files")

# 统计
total_size = sum(s["size"] for s in snapshot.values())
print(f"\n  快照完成: {total_files} 文件 / {total_size/1024/1024:.1f} MB")

# 落盘
os.makedirs(BACKUP_DIR, exist_ok=True)
snap_path = os.path.join(BACKUP_DIR, "migration_snapshot_W-2a_20261022.json")
with open(snap_path, "w", encoding="utf-8") as f:
    json.dump({
        "ts": dt.datetime.now().isoformat(timespec="seconds"),
        "source_root": MARKET_ROOT,
        "target_root": "D:\\全量数据\\wind_data\\norm",
        "total_files": total_files,
        "total_bytes": total_size,
        "files": snapshot,
    }, f, indent=2)
print(f"  快照已存: {snap_path} ({os.path.getsize(snap_path)/1024:.0f} KB)")

# ── 3. 引用文件 grep 预演 ────────────────────────────────
print("\n=== 闸口 1 续: 22 引用文件 grep 预演 ===")
REF_FILES = [
    # 生产代码 2
    r"D:\量化策略\代码\strategies\qidian_daily_scan.py",
    r"D:\量化策略\代码\tools\consumer_coverage.py",
    # 文档同步 3
    r"D:\量化策略\代码\_scratch\doc_sync_root.py",
    r"D:\量化策略\代码\_scratch\doc_sync_mdf.py",
    r"D:\量化策略\代码\_scratch\doc_sync_mdf2.py",
    # 采集 5
    r"D:\量化策略\代码\_scratch\wind-研究暂存\ohlc_expand.py",
    r"D:\量化策略\代码\_scratch\wind-研究暂存\p0_to_parquet.py",
    r"D:\量化策略\代码\_scratch\wind-研究暂存\backfill_turnover_rate.py",
    r"D:\量化策略\代码\_scratch\wind-研究暂存\resume3.py",
    r"D:\量化策略\代码\_scratch\wind-研究暂存\probe_basicinfo.py",
    # 研究 5（§70.5 列的 wind_study 系列）
    r"D:\量化策略\代码\_scratch\wind_study\inventory.py",
    r"D:\量化策略\代码\_scratch\wind_study\probe.py",
    r"D:\量化策略\代码\_scratch\wind_study\engine.py",
    r"D:\量化策略\代码\_scratch\wind_spx_pipeline_20261006.py",
    r"D:\量化策略\代码\_scratch\wind_fetch_tasi_20261006.py",
    r"D:\量化策略\代码\_scratch\wind_fetch_idx_weekly_20261006.py",
    r"D:\量化策略\代码\_scratch\wind_enum_probe_20261006.py",
    # 回测 3
    r"D:\量化策略\代码\_scratch\backtest_batch7_20261006.py",
    r"D:\量化策略\代码\_scratch\backtest_br_ibov_20261006.py",
    r"D:\量化策略\代码\_scratch\analyze_br_ibov_20261006.py",
    # 赤潮侧 4
    r"D:\量化策略\赤潮\代码\_scratch\four_markets_vs_spx_20261016.py",
    r"D:\量化策略\赤潮\代码\_scratch\ai_era_who_wins_20261016.py",
    r"D:\量化策略\赤潮\代码\_scratch\geopol_tasi_20261015.py",
    r"D:\量化策略\赤潮\代码\_scratch\saudi_fx_leverage_20261015.py",
]

# 旧路径模式（派单里典型引用）
OLD_PATTERNS = [
    r"market_data\\wind_index",
    r"market_data/wind_index",
    r"D:\\全量数据\\market_data\\wind_index",
    r"D:/全量数据/market_data/wind_index",
    r"wind_index\\daily",
    r"wind_index/daily",
    r"MARKET_DATA_DIR.*wind_index",
    r"WIND_INDEX",
]

hits_by_file = {}
for fp in REF_FILES:
    if not os.path.exists(fp):
        print(f"  ⚠️ 文件不存在: {fp}")
        continue
    try:
        content = open(fp, "r", encoding="utf-8", errors="ignore").read()
    except Exception as e:
        print(f"  ⚠️ 读失败 {fp}: {e}")
        continue
    hits = []
    for p in OLD_PATTERNS:
        idx = content.find(p)
        if idx >= 0:
            line = content[:idx].count("\n") + 1
            context = content[max(0, idx-30):idx+60].replace("\n", " ")
            hits.append((p, line, context))
    hits_by_file[fp] = hits
    if hits:
        print(f"  ✅ {os.path.relpath(fp, r'D:\量化策略')} → {len(hits)} 处命中")
        for p, ln, ctx in hits[:3]:
            print(f"      L{ln}: ...{ctx}...")
    else:
        print(f"  ⚠️ 未命中旧路径（或路径抽象）: {os.path.basename(fp)}")

print(f"\n=== 闸口 1+2 完成 ===")
print(f"  快照: {snap_path}")
print(f"  引用预演: {len(hits_by_file)} 个文件")
