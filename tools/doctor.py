# -*- coding: utf-8 -*-
"""
tools/doctor.py — 环境自检
============================
一键说清"这台机器/这套数据环境当前是否正常"，用于排查问题前的第一站。

检查项（默认全部离线，不消耗 API 额度）:
  1. 运行环境    Python 版本 / 关键依赖（pandas / pyarrow / tushare / akshare）
  2. 路径       项目目录 / 库根 / 元数据目录 的存在性与可写性
  3. 磁盘       库根所在盘剩余空间
  4. 断点文件   可解析性 + 条目数（损坏会直接导致续跑异常）
  5. 数据速览   各目录表数、最新日期（复用 freshness 能力）
  6. API 凭据   tushare / pushplus token 是否已配置（只查配置，不联网）
  7. 配置完整性 config.py 条目是否被误删（与回归 R11 共用判据）
  8. 定时任务   列出已登记的定时任务（若可访问）

用法:
  python main.py doctor                  # 全部检查（离线）
  python main.py doctor --network        # 追加 tushare 连通性实测（消耗 1 次调用）
  python main.py doctor --json           # 输出 JSON（供程序消费）
"""
import argparse
import io
import json
import os
import shutil
import sys
from datetime import datetime

from config import (
    CODE_DIR, PROJECT_DIR, MARKET_DATA_DIR, META_DIR, DOC_DIR, TS_TOKEN,
    PUSHPLUS_TOKEN,
)

OK = "✓"
WARN = "⚠"
FAIL = "✗"


class Report:
    """收集检查结果（便于同时支持文本/JSON 输出）"""

    def __init__(self):
        self.items = []
        self.n_fail = 0
        self.n_warn = 0

    def add(self, group, name, status, detail=""):
        self.items.append({"group": group, "name": name,
                           "status": status, "detail": str(detail)})
        if status == FAIL:
            self.n_fail += 1
        elif status == WARN:
            self.n_warn += 1

    def ok(self, g, n, d=""):
        self.add(g, n, OK, d)

    def warn(self, g, n, d=""):
        self.add(g, n, WARN, d)

    def fail(self, g, n, d=""):
        self.add(g, n, FAIL, d)

    def group(self, g):
        return [x for x in self.items if x["group"] == g]


# ============================================================
# 1. 运行环境
# ============================================================
def check_env(r):
    py = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info >= (3, 9):
        r.ok("环境", "Python 版本", py)
    else:
        r.warn("环境", "Python 版本", f"{py}（建议 >= 3.9）")

    deps = [("pandas", "数据帧"), ("pyarrow", "parquet 引擎"),
            ("tushare", "数据源"), ("akshare", "兜底数据源"),
            ("numpy", "数值计算")]
    for mod, desc in deps:
        try:
            m = __import__(mod)
            v = getattr(m, "__version__", "?")
            r.ok("环境", f"{mod}", f"{v} ({desc})")
        except Exception as e:
            # tushare/akshare 缺失只告警（非全流程必需）
            fn = r.warn if mod in ("tushare", "akshare") else r.fail
            fn("环境", f"{mod}", f"未安装 ({desc}) — {str(e)[:40]}")


# ============================================================
# 2. 路径
# ============================================================
def check_paths(r):
    for name, p, need_write in [
        ("代码目录", CODE_DIR, False),
        ("项目目录", PROJECT_DIR, False),
        ("库根", MARKET_DATA_DIR, True),
        ("元数据目录", META_DIR, False),
        ("文档目录", DOC_DIR, False),
    ]:
        if not os.path.isdir(p):
            r.fail("路径", name, f"不存在: {p}")
            continue
        if need_write:
            if os.access(p, os.W_OK):
                r.ok("路径", name, p)
            else:
                r.fail("路径", name, f"不可写: {p}")
        else:
            r.ok("路径", name, p)

    # 临时目录（可不存在，能创建即可）
    from config import TEMP_DIR
    try:
        os.makedirs(TEMP_DIR, exist_ok=True)
        r.ok("路径", "临时目录", TEMP_DIR)
    except Exception as e:
        r.warn("路径", "临时目录", f"无法创建: {str(e)[:40]}")


# ============================================================
# 3. 磁盘
# ============================================================
def check_disk(r):
    try:
        drive = os.path.splitdrive(MARKET_DATA_DIR)[0] + "\\"
        total, used, free = shutil.disk_usage(drive)
        free_gb = free / 1024 ** 3
        pct = used / total * 100
        d = (f"{drive} 剩余 {free_gb:.1f}GB / 共 {total/1024**3:.1f}GB "
             f"(已用 {pct:.0f}%)")
        if free_gb < 5:
            r.fail("磁盘", "剩余空间", d + " —— 空间紧张，写入可能失败")
        elif free_gb < 20:
            r.warn("磁盘", "剩余空间", d)
        else:
            r.ok("磁盘", "剩余空间", d)
    except Exception as e:
        r.warn("磁盘", "剩余空间", f"无法读取: {str(e)[:50]}")


# ============================================================
# 4. 断点文件
# ============================================================
def check_checkpoints(r):
    if not os.path.isdir(MARKET_DATA_DIR):
        r.fail("断点", "库根", "不存在，无法检查断点")
        return
    found = False
    for fn in [".checkpoint.json", ".cb_checkpoint.json",
               ".backfill_checkpoint.json"]:
        p = os.path.join(MARKET_DATA_DIR, fn)
        if not os.path.exists(p):
            continue
        found = True
        try:
            data = json.load(open(p, encoding="utf-8"))
            total = sum(len(v.get("completed", [])) if isinstance(v, dict) else 0
                        for v in data.values())
            r.ok("断点", fn,
                 f"{len(data)} 个接口 / {total:,} 条完成记录 / "
                 f"{os.path.getsize(p)/1024:.0f}KB")
        except Exception as e:
            r.fail("断点", fn, f"⛔ JSON 损坏: {str(e)[:60]}")
    if not found:
        r.warn("断点", "断点文件", "未找到任何断点文件（首次运行属正常）")


# ============================================================
# 5. 数据速览
# ============================================================
def check_data(r, top_n=8):
    if not os.path.isdir(MARKET_DATA_DIR):
        r.fail("数据", "库根", "不存在")
        return
    from common import reader

    tables = []
    for e in os.scandir(MARKET_DATA_DIR):
        if not e.is_dir():
            continue
        files = [f for f in os.listdir(e.path) if f.endswith(".parquet")]
        if not files:
            continue
        tables.append((e.name, len(files)))

    r.ok("数据", "表数量", f"{len(tables)} 张表")

    # 混合布局检测（异常状态）
    mixed = []
    for name, _ in tables:
        try:
            info = reader.inspect_layout(name)
            if info["is_mixed"]:
                mixed.append(name)
        except Exception:
            pass
    if mixed:
        r.fail("数据", "混合布局", f"⛔ {len(mixed)} 张表布局混乱: {mixed[:5]}")
    else:
        r.ok("数据", "布局一致性", "无混合布局")

    # 核心表最新日期
    core = ["daily", "daily_basic", "fund_daily", "cb_daily", "index_daily",
            "moneyflow", "margin", "adj_factor", "stk_limit"]
    latest = []
    for name in core:
        if not any(t[0] == name for t in tables):
            continue
        try:
            reader.clear_cache(name)
            d = reader.latest_date(name)
            if d:
                latest.append((name, d))
        except Exception:
            pass
    if latest:
        mx = max(d for _, d in latest)
        stale = [f"{n}={d}" for n, d in latest if d < mx]
        if stale:
            r.warn("数据", "核心表新鲜度",
                   f"最新 {mx}；落后: {', '.join(stale[:6])}")
        else:
            r.ok("数据", "核心表新鲜度", f"全部 {mx}")


# ============================================================
# 6. API 凭据
# ============================================================
def check_credentials(r, network=False):
    if TS_TOKEN and len(TS_TOKEN) > 20:
        r.ok("凭据", "tushare token", f"已配置 ({TS_TOKEN[:8]}...)")
    else:
        r.fail("凭据", "tushare token", "未配置或过短")

    if PUSHPLUS_TOKEN and len(PUSHPLUS_TOKEN) > 10:
        r.ok("凭据", "pushplus token", f"已配置 ({PUSHPLUS_TOKEN[:8]}...)")
    else:
        r.warn("凭据", "pushplus token", "未配置（推送功能不可用）")

    if not network:
        r.items.append({"group": "凭据", "name": "tushare 连通性",
                        "status": "－", "detail": "未测（加 --network 实测）"})
        return
    # 实测连通性（仅 1 次调用，不写数据）
    try:
        from fetch.base import get_pro
        pro = get_pro()
        df = pro.trade_cal(exchange="SSE", start_date="20260101",
                           end_date="20260110")
        n = 0 if df is None else len(df)
        r.ok("凭据", "tushare 连通性", f"调用成功（返回 {n} 行）")
    except Exception as e:
        r.fail("凭据", "tushare 连通性", f"调用失败: {str(e)[:80]}")


# ============================================================
# 7. 配置完整性
# ============================================================
def check_config_integrity(rep):
    """config.py 结构自检（与回归 R11 **共用同一份判据**）。

    为什么放进 doctor（2026-09-19）:
      config 被误删的症状都是**下游静默异常**（如 db-audit 静默跳过判重、
      每日更新漏表、指数清单缺项），不会当场报错。doctor 是排查问题的
      第一站，在此暴露可省去"跑完整回归（数十秒）才发现"的往返。

    ⚠ 判据来自 tools/config_check.py（单一真源）。若在此重写一份下限表，
      就是 R10 揭示的"两个结构各管一摊"复发形态 —— 新增条目时只改一处。
    """
    try:
        from tools.config_check import check_config as _cc
        res = _cc()
    except Exception as e:
        rep.warn("配置", "完整性", f"检查未执行: {str(e)[:60]}")
        return
    if res.ok:
        rep.ok("配置", "完整性", res.summary)
    else:
        # 失败项逐条列出（可能多条，合并会看不清）
        for f in res.failures:
            rep.fail("配置", "完整性", f.replace("\n", " "))


# ============================================================
# 7b. config 死配置复活检测（G11 · 2026-09-25）
# ============================================================
def check_dead_config_revival(rep):
    """扫描 config.py 中带 [ARCHIVED] 标记的死配置名，全库 grep 确认仍无引用。
    若死配置被重新引用 → FAIL（说明有人未读注释复活了废弃链路，需回读归档依据）。
    与 G1（2026-09-25 三重核实）同一判据；标记清单单一真源在 config.py 注释本身。"""
    import re
    from pathlib import Path
    try:
        cfg = Path(__file__).resolve().parent.parent / "config.py"
        text = cfg.read_text(encoding="utf-8")
        # [ARCHIVED ...] 注释的下一行 = 死配置定义
        dead_names = re.findall(
            r"^# \[ARCHIVED[^\]]*\].*\n^([A-Z_]+)\s*=", text, re.M)
        if not dead_names:
            rep.warn("配置", "死配置复活检测", "config.py 无 [ARCHIVED] 标记（G1 标记丢失？）")
            return
        skip = {"_archive", "_scratch", "_venv", "_python", "__pycache__"}
        root = cfg.parent
        revived = []
        for name in set(dead_names):
            pat = re.compile(rf"\b{name}\b")
            for p in root.rglob("*.py"):
                if any(part in skip for part in p.parts):
                    continue
                if p.name == "config.py":
                    continue  # 定义处自身
                try:
                    for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                        if pat.search(line):
                            revived.append(f"{name} ← {p.name}:{i}")
                            break
                except OSError:
                    continue
                if len([x for x in revived if x.startswith(name)]) > 2:
                    break
        if revived:
            for x in revived[:8]:
                rep.fail("配置", "死配置复活检测", f"被引用: {x}")
        else:
            rep.ok("配置", "死配置复活检测", f"{len(set(dead_names))} 个 [ARCHIVED] 死配置均无新引用")
    except Exception as e:
        rep.warn("配置", "死配置复活检测", f"检查未执行: {str(e)[:60]}")


# ============================================================
# 8. 定时任务
# ============================================================
def check_scheduler(r):
    """列出已登记的定时任务（只读 TeleAgent 的 scheduler.db）"""
    home = os.path.expanduser("~")
    db = os.path.join(home, ".local", "share", "TeleAgent", "scheduler",
                      "scheduler.db")
    if not os.path.exists(db):
        r.items.append({"group": "定时任务", "name": "调度库", "status": "－",
                        "detail": "未找到 scheduler.db（非 TeleAgent 环境属正常）"})
        return
    try:
        import sqlite3
        conn = sqlite3.connect(db)
        cur = conn.cursor()
        cur.execute("SELECT name, enabled FROM jobs")
        rows = cur.fetchall()
        conn.close()
        enabled = [n for n, e in rows if e]
        r.ok("定时任务", "已登记任务",
             f"{len(rows)} 个（启用 {len(enabled)}）: {', '.join(enabled[:8])}")
    except Exception as e:
        r.warn("定时任务", "调度库", f"读取失败: {str(e)[:60]}")


# ============================================================
# 主流程
# ============================================================
def run_doctor(network=False, as_json=False):
    r = Report()
    check_env(r)
    check_paths(r)
    check_disk(r)
    check_checkpoints(r)
    check_data(r)
    check_credentials(r, network=network)
    check_config_integrity(r)
    check_dead_config_revival(r)
    check_scheduler(r)

    if as_json:
        print(json.dumps({"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                          "n_fail": r.n_fail, "n_warn": r.n_warn,
                          "items": r.items}, ensure_ascii=False, indent=1))
        return r

    SEP = "=" * 92
    print(SEP)
    print("  环境自检 (doctor)")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  库根: {MARKET_DATA_DIR}")
    print(SEP)

    groups = []
    for it in r.items:
        if it["group"] not in groups:
            groups.append(it["group"])

    for g in groups:
        print(f"\n  【{g}】")
        for it in r.group(g):
            st = it["status"]
            mark = {OK: "[通过]", WARN: "[提示]", FAIL: "[失败]"}.get(st, f"[{st}]")
            print(f"    {mark} {it['name']:22s} {it['detail']}")

    print()
    print(SEP)
    if r.n_fail:
        print(f"  自检结论: {FAIL} {r.n_fail} 项失败, {r.n_warn} 项提示")
        print("    建议先处理失败项，它们会导致写入/续跑/推送异常。")
    elif r.n_warn:
        print(f"  自检结论: {WARN} 全部通过，但有 {r.n_warn} 项提示（通常不影响使用）")
    else:
        print(f"  自检结论: {OK} 全部通过")
    print(SEP)
    return r


def main(argv=None):
    ap = argparse.ArgumentParser(description="环境自检")
    ap.add_argument("--network", action="store_true",
                    help="追加 tushare 连通性实测（消耗 1 次 API 调用）")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="输出 JSON（供程序消费）")
    a = ap.parse_args(argv)
    r = run_doctor(network=a.network, as_json=a.as_json)
    return 0 if r.n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
