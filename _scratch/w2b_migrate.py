"""
W-2b: raw 搬迁 + config.py L49 回写
"""
import os, shutil, hashlib, json, datetime as dt

RAW_SRC = r"D:\量化策略\数据\wind指数库\raw"
RAW_DST = r"D:\全量数据\wind_data\raw"
CATALOG_SRC = r"D:\量化策略\数据\wind指数库"
CATALOG_DST = r"D:\全量数据\wind_data\_catalog"
LOGS_SRC = os.path.join(CATALOG_SRC, "logs")
LOGS_DST = r"D:\全量数据\wind_data\_logs"

print("=== W-2b 闸口 1+2: 源实测 + 快照 ===")
# 快照 raw
def dir_size(root):
    total = 0
    for _, _, files in os.walk(root):
        total += sum(os.path.getsize(os.path.join(root, f)) for f in files if os.path.isfile(os.path.join(root, f)))
    return total

raw_before = dir_size(RAW_SRC) if os.path.exists(RAW_SRC) else 0
print(f"  raw 源: {os.path.exists(RAW_SRC)} / {raw_before/1024/1024:.1f} MB")

# ── raw 搬迁 ──────────────────────────────────────
print("\n=== W-2b 闸口 3: raw 搬迁 ===")
os.makedirs(os.path.dirname(RAW_DST), exist_ok=True)
if os.path.exists(RAW_DST):
    print(f"  ⚠️ 目标已存在 {len(os.listdir(RAW_DST))} 文件 — 先合并")
    # 合并（不覆盖已有）
    for f in os.listdir(RAW_SRC):
        src = os.path.join(RAW_SRC, f)
        dst = os.path.join(RAW_DST, f)
        if not os.path.exists(dst):
            os.rename(src, dst)
    os.rmdir(RAW_SRC)
else:
    os.rename(RAW_SRC, RAW_DST)
print(f"  ✅ raw 已搬到 {RAW_DST} / {dir_size(RAW_DST)/1024/1024:.1f} MB")

# ── 清单 CSV 搬迁 ──────────────────────────────────
print("\n=== W-2b 闸口 3: CSV / logs 搬迁 ===")
os.makedirs(CATALOG_DST, exist_ok=True)
os.makedirs(LOGS_DST, exist_ok=True)
csv_count = 0
for f in os.listdir(CATALOG_SRC):
    fp = os.path.join(CATALOG_SRC, f)
    if os.path.isfile(fp) and f.endswith((".csv", ".json")):
        shutil.copy2(fp, os.path.join(CATALOG_DST, f))
        csv_count += 1
print(f"  CSV 清单复制: {csv_count} → {CATALOG_DST}")
# logs
if os.path.exists(LOGS_SRC):
    log_count = sum(1 for f in os.listdir(LOGS_SRC) if os.path.isfile(os.path.join(LOGS_SRC, f)))
    for f in os.listdir(LOGS_SRC):
        shutil.copy2(os.path.join(LOGS_SRC, f), os.path.join(LOGS_DST, f))
    print(f"  logs 复制: {log_count} → {LOGS_DST}")

# ── 闸口 4: raw 字节对账 ────────────────────────────
raw_after = dir_size(RAW_DST)
print(f"\n=== W-2b 闸口 4: raw 字节对账 ===")
print(f"  迁移前: {raw_before/1024/1024:.1f} MB")
print(f"  迁移后: {raw_after/1024/1024:.1f} MB")
ok = abs(raw_before - raw_after) < 1024
print(f"  {'✅ 一致' if ok else '❌ 不一致'}")

# ── config.py L49 回写（§70.6）────────────────────────
print("\n=== W-2b 闸口续: config.py L49 回写 ===")
cfg = r"D:\量化策略\代码\config.py"
with open(cfg, "r", encoding="utf-8") as f:
    content = f.read()

old_line = "    wind_index_enum.py   Wind 自编指数枚举（隔离：产出目录与 tushare 库分离）"
new_line = "    _scratch\\wind-研究暂存\\Wind 系列采集与研究脚本（原始响应已迁至 wind_data\\raw，目录暂存）"
if old_line in content:
    content = content.replace(old_line, new_line)
    with open(cfg, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  ✅ L49 已回写：")
    print(f"    旧: wind_index_enum.py (fetch\\下不存在，失效登记)")
    print(f"    新: _scratch\\wind-研究暂存\\... (实体位置)")
else:
    print(f"  ⚠️ 未找到原行")

# config_check
print(f"\n  config_check:")
import subprocess
r = subprocess.run(["D:/量化策略/代码/_venv/Scripts/python.exe", "tools/config_check.py"],
                   cwd=r"D:\量化策略\代码", capture_output=True, text=True, timeout=30)
for line in r.stdout.split("\n")[-3:]:
    print(f"    {line.strip()}")

print(f"\n{'✅ W-2b 全过' if ok else '❌ W-2b 未过'}")
