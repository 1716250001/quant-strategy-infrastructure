# -*- coding: utf-8 -*-
"""补拉落盘校验 —— 清单内每个码是否**真的**进了库（不采信「成功=N」）。

为什么需要（2026-11-18 cb_issue 实证）
--------------------------------------
`main.py backfill --codes-file ...` 输出 `成功=1 空=0 失败=0`，但该码库内
**0 行、文件 mtime 都没变**。根因是源端 `ann_date=None`：按年分区取不到年份，
`groupby(dropna=True)` 把整组丢掉，返回 `{rows:1, partitions:0, fail:0}`。
调用方只检查 `fail==0` 就记断点 ⇒ **报成功、不落盘、永久跳过**。

⇒ 补拉验收必须落到"库里有没有这个码"，不能只看引擎自报的成功数。

用法
----
    python scripts/verify_landed.py --table cb_issue --codes-file _scratch/c.txt
    python scripts/verify_landed.py --table fund_share --codes-file c.txt --json

退出码：0=全部落盘 / 1=有码未落盘 / 2=参数或环境错误
"""
import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(os.path.dirname(_ROOT), "赤潮", "docs")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)


def main():
    ap = argparse.ArgumentParser(description="补拉落盘校验")
    ap.add_argument("--table", required=True)
    ap.add_argument("--codes-file", required=True)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if not os.path.isfile(a.codes_file):
        print(f"[FATAL] 清单不存在: {a.codes_file}", file=sys.stderr)
        return 2
    codes = [ln.split("#", 1)[0].strip()
             for ln in open(a.codes_file, encoding="utf-8")]
    codes = [c for c in codes if c]
    if not codes:
        print("[FATAL] 清单为空", file=sys.stderr)
        return 2

    from common import reader  # 延迟导入，避免 --help 也走重依赖
    from config import MARKET_DATA_DIR

    reader.clear_cache(a.table)
    try:
        present = set(reader.codes_from_store(a.table, root=MARKET_DATA_DIR))
    except Exception as exc:
        print(f"[FATAL] 读库内码集失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    missing = [c for c in codes if c not in present]
    ok = len(codes) - len(missing)

    if a.json:
        print(json.dumps({"table": a.table, "total": len(codes), "landed": ok,
                          "missing": missing}, ensure_ascii=False))
    else:
        print(f"[落盘校验] {a.table}: 清单 {len(codes)} 只，"
              f"已落盘 {ok}，未落盘 {len(missing)}")
        if missing:
            print(f"  未落盘样例（前 20）: {missing[:20]}")
            print("  ⛔ 报成功但库里没有 —— 查引擎落盘路径（空日期？去重吞行？）")
        else:
            print("  ✅ 全部落盘")
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
