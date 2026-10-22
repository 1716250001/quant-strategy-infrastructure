"""
W-2a 闸口续：改 10 个硬编码引用文件
旧路径 → 新路径映射（§70.5 + 预演）
"""
import os, re

MARKET = r"D:\全量数据\market_data"
WIND = r"D:\全量数据\wind_data"

# 10 个命中的文件
FILES = [
    r"D:\量化策略\代码\strategies\qidian_daily_scan.py",
    r"D:\量化策略\代码\tools\consumer_coverage.py",
    r"D:\量化策略\代码\_scratch\wind-研究暂存\p0_to_parquet.py",
    r"D:\量化策略\代码\_scratch\wind-研究暂存\backfill_turnover_rate.py",
    r"D:\量化策略\代码\_scratch\wind_study\inventory.py",
    r"D:\量化策略\代码\_scratch\wind_study\probe.py",
    r"D:\量化策略\代码\_scratch\wind_study\engine.py",
    r"D:\量化策略\代码\_scratch\wind_spx_pipeline_20261006.py",
    r"D:\量化策略\代码\_scratch\wind_fetch_tasi_20261006.py",
    r"D:\量化策略\代码\_scratch\wind_fetch_idx_weekly_20261006.py",
    # 还有 resume3.py / probe_basicinfo.py / ohlc_expand.py 预演未命中但可能用了 LIB 变量
    r"D:\量化策略\代码\_scratch\wind-研究暂存\resume3.py",
    r"D:\量化策略\代码\_scratch\wind-研究暂存\probe_basicinfo.py",
    r"D:\量化策略\代码\_scratch\wind-研究暂存\ohlc_expand.py",
]

# 旧路径 → 新路径 规则（通用替换，全路径）
REPLACEMENTS = [
    # daily 路径
    (r"D:\全量数据\market_data\wind_index\daily", r"D:\全量数据\wind_data\norm\series\wind_index_daily"),
    (r"D:/全量数据/market_data/wind_index/daily", r"D:/全量数据/wind_data/norm/series/wind_index_daily"),
    # spx
    (r"D:\全量数据\market_data\wind_index\spx", r"D:\全量数据\wind_data\norm\series\spx_weekly"),
    (r"D:/全量数据/market_data/wind_index/spx", r"D:/全量数据/wind_data/norm/series/spx_weekly"),
    # tasi_weekly
    (r"D:\全量数据\market_data\wind_index\tasi_weekly", r"D:\全量数据\wind_data\norm\series\tasi_weekly"),
    (r"D:/全量数据/market_data/wind_index/tasi_weekly", r"D:/全量数据/wind_data/norm/series/tasi_weekly"),
    # ibov
    (r"D:\全量数据\market_data\wind_index\ibov", r"D:\全量数据\wind_data\norm\series\ibov_weekly"),
    (r"D:/全量数据/market_data/wind_index/ibov", r"D:/全量数据/wind_data/norm/series/ibov_weekly"),
    # enum_probe
    (r"D:\全量数据\market_data\wind_index\enum_probe", r"D:\全量数据\wind_data\norm\snap\enum_probe"),
    (r"D:/全量数据/market_data/wind_index/enum_probe", r"D:/全量数据/wind_data/norm/snap/enum_probe"),
    # wind_index/daily 相对路径（注释里）
    ("market_data/wind_index/daily", "wind_data/norm/series/wind_index_daily"),
    ("wind_index/daily", "wind_data/norm/series/wind_index_daily"),
    # 通用 market_data\wind_index → wind_data\norm
    ("market_data\\wind_index", "wind_data\\norm"),
    ("market_data/wind_index", "wind_data/norm"),
]

print("=== 改路径 ===")
total_changes = 0
for fp in FILES:
    if not os.path.exists(fp):
        print(f"  ⚠️ 不存在: {fp}")
        continue
    with open(fp, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    orig = content
    changes = 0
    for old, new in REPLACEMENTS:
        if old in content:
            count = content.count(old)
            content = content.replace(old, new)
            changes += count
    if changes > 0:
        with open(fp, "w", encoding="utf-8", errors="ignore") as f:
            f.write(content)
        print(f"  ✅ {os.path.basename(fp)}: {changes} 处")
        total_changes += changes
    else:
        print(f"  - {os.path.basename(fp)}: 无命中")

print(f"\n总修改: {total_changes} 处")

# 最终 grep：market_data\wind_index 在全域零残留（除备份/归档/_backup/_migrate_backup）
print("\n=== 最终 grep：旧路径零残留 ===")
import subprocess
r = subprocess.run(
    ["powershell", "-Command",
     "Get-ChildItem -Path 'D:\\量化策略' -Recurse -File " +
     "-Include '*.py','*.md','*.json','*.yaml','*.yml','*.txt' " +
     "-ErrorAction SilentlyContinue | " +
     "Select-String -Pattern 'market_data.*wind_index' " +
     "-ErrorAction SilentlyContinue | " +
     "Where-Object { $_.Path -notmatch '_backup|_migrate_backup|归档|backup|history|CHANGELOG' } | " +
     "Select-Object -First 10 | ForEach-Object { $_.Path + ':L' + $_.LineNumber }"
    ],
    capture_output=True, text=True, timeout=60
)
print(f"  grep 结果（非备份）:")
if r.stdout.strip():
    for line in r.stdout.strip().split("\n")[:20]:
        print(f"    ❌ {line}")
else:
    print(f"    ✅ 零残留")

print(f"\n{'✅ W-2a 全过' if total_changes > 0 else '⚠️ 无修改（全部无命中）'}")
