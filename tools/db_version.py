# -*- coding: utf-8 -*-
"""tools/db_version.py — 库版本 `db_version.json` 写入口（EE-D11-1 动作 A；08 §13.5 V1.1 冻结规范）
================================================================================================

规范依据：`代码架构/全量数据库架构/08-可复现性与数据版本.md §13.5`（v1.1 定版已冻结）——

    版本标识：`MD-YYYYMMDD.N`（N = 当日第 N 次成功日更/变更，**同日递增**）
    唯一落点：`D:\\全量数据\\market_data\\metadata\\db_version.json`
    必填字段：`version` / `generated_at` / `tables_changed` / `checkpoint_rev`
    （`manifest` 为 to-be，依赖 PoC-2 快照层，本工具**不写**）
    触发时机：**每次成功日更写入一次；补数与回补同样写**；`--rebuild` 类
    整表重建视同**结构性变更**（08 §13.5 轮转表版本记录）。

接线落点（cb 按规范布线；全部 best-effort——版本写失败**不阻断**数据主流程，
但 doctor 会把"版本落后/缺失"显示出来，可被体检发现）：
    1. `fetch/daily_update.py`   成功日更收尾（含 Step 5 周期通道的表合并为**一次**记录）
    2. `fetch/backfill.py`       补数/回补完成（完成项 > 0 才记）
    3. `fetch/periodic_update.py 周期通道**独立运行**时（被日更内嵌时由 1 统一记，防同日双计）
    4. `tools/db_schema_align.py 跨分区 schema 对齐（结构性变更语义；n_fixed>0 才记）

口径说明（规范未覆盖处，cb 实现并在回执报请审查方确认）：
    `checkpoint_rev` ＝ `.backfill_checkpoint.json` 各表 `completed` 计数之和
    （**单调可比较**的库态代理：补数/回补只增不减；日更不改变它）。
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta

_CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)

from config import MARKET_DATA_DIR      # noqa: E402

TZ8 = timezone(timedelta(hours=8))
CKPT_NAME = ".backfill_checkpoint.json"


def version_path(root=None):
    return os.path.join(root or MARKET_DATA_DIR, "metadata", "db_version.json")


def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _checkpoint_rev(root=None):
    """断点总完成项数（单调代理；缺失/损坏 ⇒ 0，不阻断）。"""
    p = os.path.join(root or MARKET_DATA_DIR, CKPT_NAME)
    try:
        with open(p, "r", encoding="utf-8") as f:
            cp = json.load(f)
        return sum(len(v.get("completed", []))
                   for v in cp.values() if isinstance(v, dict))
    except Exception:
        return 0


def next_version(root=None, today=None):
    """→ (下一个版本号, 现有记录)。同日已有 ⇒ N+1；否则 N=1。"""
    cur = _read(version_path(root))
    today = today or datetime.now(TZ8).strftime("%Y%m%d")
    ver = str(cur.get("version", ""))
    n = 0
    if ver.startswith(f"MD-{today}."):
        try:
            n = int(ver.split(".", 1)[1])
        except ValueError:
            n = 0
    return f"MD-{today}.{n + 1}", cur


def record(tables_changed, note=None, structural=False, root=None):
    """写一条库版本（原子写：.tmp → fsync → os.replace）。返回新记录 dict。

    tables_changed: 表名序列（str）；轮转表按 08 §13.5 用 "表(覆盖/总数)" 字符串。
    structural:     结构性变更标记（--rebuild / schema 对齐等）——记入 note 前缀
                    （08 §13.5 "视同结构性变更" 的留痕口径；字段集不变）。
    """
    if isinstance(tables_changed, str):
        tables_changed = [tables_changed]
    tables_changed = [str(t) for t in (tables_changed or [])]
    root = root or MARKET_DATA_DIR
    ver, cur = next_version(root)
    rec = {
        "version": ver,
        "generated_at": datetime.now(TZ8).isoformat(timespec="seconds"),
        "tables_changed": tables_changed,
        "checkpoint_rev": _checkpoint_rev(root),
    }
    if structural:
        rec["note"] = f"[结构性变更] {note}" if note else "[结构性变更]"
    elif note:
        rec["note"] = note
    # 历史轮转信息保留在文件尾部（08 §13.5 只冻结四必填字段；历史追加不违反唯一落点）
    hist = cur.get("history")
    if isinstance(hist, list):
        hist.append({k: cur.get(k) for k in
                     ("version", "generated_at", "tables_changed", "checkpoint_rev")
                     if cur.get(k) is not None})
        if len(hist) > 90:                       # 上限防膨胀（≈90 次变更）
            hist = hist[-90:]
        rec["history"] = hist
    elif cur:
        rec["history"] = [{k: cur.get(k) for k in
                           ("version", "generated_at", "tables_changed", "checkpoint_rev")
                           if cur.get(k) is not None}]
    p = version_path(root)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)
    return rec


def show(root=None):
    """→ (记录 | None)：文件缺失返回 None（消费侧按「未生成」提示，不报错）。"""
    return _read(version_path(root)) or None


# ============================================================
# CLI
# ============================================================
def main(argv=None):
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    p = argparse.ArgumentParser(
        description="库版本 db_version.json 写入口（08 §13.5；EE-D11-1 动作 A）")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record", help="写一条版本（同日自动递增 N）")
    r.add_argument("--tables", default="",
                   help="变更表名，逗号分隔（轮转表可用 '表(覆盖/总数)' 形式）")
    r.add_argument("--note", default=None)
    r.add_argument("--structural", action="store_true",
                   help="标记为结构性变更（--rebuild / schema 对齐类）")
    r.add_argument("--root", default=None, help="库根（默认 MARKET_DATA_DIR；沙盒测试用）")
    s = sub.add_parser("show", help="显示当前库版本（缺失 ⇒ 提示未生成）")
    s.add_argument("--root", default=None)
    n = sub.add_parser("next", help="预览下一个版本号（零写入）")
    n.add_argument("--root", default=None)
    a = p.parse_args(argv)

    if a.cmd == "record":
        tables = [t.strip() for t in a.tables.split(",") if t.strip()]
        rec = record(tables, note=a.note, structural=a.structural, root=a.root)
        print(f"[ok] 库版本已写入: {rec['version']}"
              f" | tables_changed={rec['tables_changed'] or ['（接线基线/无表变更）']}"
              f" | checkpoint_rev={rec['checkpoint_rev']}")
        print(f"     {version_path(a.root)}")
        return 0
    rec = show(a.root)
    if a.cmd == "next":
        v, cur = next_version(a.root)
        print(f"当前: {cur.get('version', '（无记录）')} → 下一个: {v}")
        return 0
    if not rec:
        print("db_version: 未生成（08 §13.5；接线后由日更/回补/结构性变更自动写入）")
        return 0
    print(f"库版本        : {rec.get('version')}")
    print(f"生成时间      : {rec.get('generated_at')}")
    print(f"变更表        : {', '.join(rec.get('tables_changed') or []) or '（无）'}")
    print(f"checkpoint_rev: {rec.get('checkpoint_rev')}")
    if rec.get("note"):
        print(f"备注          : {rec['note']}")
    hist = rec.get("history") or []
    if hist:
        print(f"历史（近 {len(hist)} 条）: {' → '.join(h.get('version', '?') for h in hist[-6:])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
