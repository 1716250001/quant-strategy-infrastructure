"""
W-2a: 迁移 wind_index（闸口 3 执行 + 闸口 4 复核）
"""
import os, json, hashlib, shutil, datetime as dt

SRC = r"D:\全量数据\market_data\wind_index"
META = r"D:\全量数据\wind_data\_meta"
SNAP_PATH = os.path.join(META, "migration_snapshot_W-2a_20261022.json")

with open(SNAP_PATH, "r") as f:
    snap = json.load(f)
total_files = snap["total_files"]
total_bytes = snap["total_bytes"]
print(f"闸口 3 开始: {total_files} 文件 / {total_bytes/1024/1024:.1f} MB")

# ── 迁移映射（os.rename = 同盘原子移动）────────────────────
MOVES = [
    ("daily",       os.path.join("D:\\全量数据\\wind_data\\norm\\series\\wind_index_daily")),
    ("spx",         os.path.join("D:\\全量数据\\wind_data\\norm\\series\\spx_weekly")),
    ("tasi_weekly", os.path.join("D:\\全量数据\\wind_data\\norm\\series\\tasi_weekly")),
    ("ibov",        os.path.join("D:\\全量数据\\wind_data\\norm\\series\\ibov_weekly")),
    ("enum_probe",  os.path.join("D:\\全量数据\\wind_data\\norm\\snap\\enum_probe")),
]

# 确保目标父目录存在
for _, dst in MOVES:
    os.makedirs(os.path.dirname(dst), exist_ok=True)

for src_name, dst in MOVES:
    src_full = os.path.join(SRC, src_name)
    if os.path.exists(dst):
        print(f"  ⚠️ 目标已存在: {dst} — 跳过")
        continue
    if not os.path.exists(src_full):
        print(f"  ❌ 源不存在: {src_full}")
        continue
    try:
        os.rename(src_full, dst)
        print(f"  ✅ move {src_name} → {os.path.basename(os.path.dirname(dst))}/{os.path.basename(dst)}")
    except Exception as e:
        print(f"  ❌ move {src_name} 失败: {e}")

# ── 闸口 4: 文件数 + 总字节 + sha256 抽样 ──────────────
print("\n=== 闸口 4: 复核 ===")

# 统计迁移后的文件
all_new = []
for dst_sub in [m[1] for m in MOVES]:
    if not os.path.exists(dst_sub):
        continue
    for root, _, files in os.walk(dst_sub):
        for f in files:
            all_new.append(os.path.join(root, f))

new_files = len(all_new)
new_bytes = sum(os.path.getsize(f) for f in all_new)
print(f"  迁移后: {new_files} 文件 / {new_bytes/1024/1024:.1f} MB")

# 源 wind_index 还剩什么？
remaining = os.listdir(SRC) if os.path.exists(SRC) else []
print(f"  源 wind_index 剩余: {remaining}")

ok_files = new_files == total_files
ok_bytes = abs(new_bytes - total_bytes) < 1024  # 误差 < 1KB
print(f"  文件数 {'✅' if ok_files else '❌'} {new_files}/{total_files}")
print(f"  总字节 {'✅' if ok_bytes else '❌'} {new_bytes/1024/1024:.1f} vs {total_bytes/1024/1024:.1f} MB")

# sha256 抽样：前 50 个文件
print(f"\n  sha256 抽样（前 50 个）:")
checked = 0
mismatch = 0
for full in sorted(all_new)[:50]:
    rel = os.path.relpath(full, "D:\\全量数据\\wind_data")
    # 映射回旧相对路径
    # 旧: market_data\wind_index\{subdir}\xxx.parquet
    # 新: wind_data\norm\series\wind_index_daily\xxx.parquet 等
    old_rel = None
    for subdir, dst in MOVES:
        prefix_new = os.path.relpath(dst, "D:\\全量数据\\wind_data")
        if rel.startswith(prefix_new):
            suffix = rel[len(prefix_new)+1:]
            old_rel = os.path.join(subdir, suffix)
            break
    if old_rel and old_rel in snap["files"]:
        h = hashlib.sha256()
        with open(full, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        got = h.hexdigest()
        expect = snap["files"][old_rel]["sha256"]
        if got != expect:
            print(f"    ❌ {rel}: sha256 不一致!")
            mismatch += 1
        checked += 1

print(f"  抽样 sha256: {checked} 件 / 不一致 {mismatch}")
print(f"\n{'✅ 闸口 3+4 迁移完成' if ok_files and ok_bytes and mismatch == 0 else '❌ 复核未过'}")
