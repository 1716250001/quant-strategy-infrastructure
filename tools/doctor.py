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
  7b. 死配置复活  [ARCHIVED] 标记项是否被重新引用
  8. 漏网接口    monitor=True 的漏网接口是否**真的会被每天调用**（空转检测）+ 新鲜度。
                ⚠ 只查 monitor=True 的：monitor=False 的（如 hk_daily/us_daily，
                本地无表、走 akshare 替代源且新鲜度由替代源侧自带检查）本就不在
                Tushare 日更体系内，跳过而非漏检 —— 详见 config.LEAKY_IDLE_EXEMPT。
  9. 定时任务   列出已登记的定时任务（若可访问）
 10. 事件面表   周期更新通道（config.PERIODIC_UPDATE_TARGETS：dividend /
                namechange）的**陈旧监控** —— 堵住"by_code 事件面表既不在日更组、
                也不在豁免清单 ⇒ 无归宿无告警"的缺口（2026-10-11 三轮审查 E-01；
                实证 namechange 曾停更 4 周无人发现）。只读、零请求。

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
    PUSHPLUS_TOKEN, LEAKY_IDLE_EXEMPT,
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
# 9. 漏网接口：监控空转检测 + 新鲜度（2026-10-01 新增）
# ============================================================
def check_leaky_permissions(rep):
    """LEAKY_PERMISSIONS 自检 —— 回答"漏网失效能不能被自动发现"。

    背景（fund_adj 实证，2026-10-01）：
      LEAKY_PERMISSIONS 的 monitor=True 是**被动告警**——只在接口被调用失败时提示
      （fetch/base.py）、以及日更结束时汇总**本次涉及的接口**（fetch/daily_update.py）。
      它从来不是主动心跳。⇒ **不进 DAILY_UPDATE_GROUPS 的漏网接口，没人每天调它，
      失效就永远发现不了**——登记 monitor=True 给的是安全感，不是覆盖。
      fund_adj 正是因此空转（库停 20260918，无人察觉）。

    两个判据:
      ① 空转：接口不在任何日更组 ⇒ 每日零调用 ⇒ 告警形同虚设
      ② 变旧：有本地表但最新日期落后 daily 基准 > 3 个自然日
    """
    try:
        from config import LEAKY_PERMISSIONS, DAILY_UPDATE_GROUPS
        from common import reader
    except Exception as e:
        rep.warn("漏网接口", "检查未执行", f"{str(e)[:60]}")
        return

    daily_groups = set()
    for members in DAILY_UPDATE_GROUPS.values():
        daily_groups |= set(members)

    base = None
    try:
        reader.clear_cache("daily")
        base = reader.latest_date("daily")
    except Exception:
        pass

    def _days(a, b):
        """YYYYMMDD 日期差（自然日）；解析失败返回 None"""
        try:
            return (datetime.strptime(b, "%Y%m%d") - datetime.strptime(a, "%Y%m%d")).days
        except Exception:
            return None

    idle, stale, no_table, undated, healthy, exempted = [], [], [], [], [], []
    for api, info in LEAKY_PERMISSIONS.items():
        if not info.get("monitor"):
            continue
        # 按设计就不该每日调用的（接口语义不支持 / 全量重拉代价不可接受）
        # ⇒ 归为"豁免"而非"空转"，避免与真疏漏混在一起刷屏
        if api in LEAKY_IDLE_EXEMPT:
            exempted.append(api)
            continue
        # ⚠ 区分"目录不存在"与"有表但读不出日期列"——后者仍须参与空转判定
        #   （实证 share_float 有目录，只是无 trade_date 列，不能当"无表"放过去）
        has_dir = os.path.isdir(os.path.join(MARKET_DATA_DIR, api))
        if not has_dir:
            no_table.append(api)          # 本地无表（如 hk_daily/us_daily 走 akshare）
            # ⚠ 不进 idle 判定：它们本就不走 Tushare 日更那条路（替代源自建），
            #    "不在日更组"不构成风险，报出来只是噪音。真风险在"应该日更却没日更"。
            continue
        latest = None
        try:
            reader.clear_cache(api)
            latest = reader.latest_date(api)
        except Exception:
            latest = None
        gap = _days(latest, base) if (base and latest) else None
        if latest is None:
            undated.append(api)
        elif gap is not None and gap > 3:
            stale.append(f"{api}={latest}(落后基准{base} {gap}天)")
        if api not in daily_groups:
            idle.append(api)
        elif gap is None or gap <= 3:
            healthy.append(api)

    if idle:
        rep.warn("漏网接口", "监控空转",
                 f"⛔ {len(idle)} 个漏网接口**不在任何日更组** ⇒ 每日零调用 ⇒ "
                 f"失效无法自动发现: {', '.join(idle)}"
                 f"（登记 monitor=True 只在被调用失败时才告警）")
    else:
        rep.ok("漏网接口", "监控空转",
               f"{len(healthy)} 个受监控漏网接口均纳入日更（失效可被日发现）")
    if exempted:
        rep.items.append({
            "group": "漏网接口", "name": "设计豁免（非真空转）", "status": "－",
            "detail": f"{len(exempted)} 个按设计不日更（接口语义/代价所限，"
                      f"原因见 config.LEAKY_IDLE_EXEMPT）: {', '.join(exempted)}"})
    if stale:
        rep.warn("漏网接口", "新鲜度", f"落后: {', '.join(stale)}")
    elif base:
        rep.ok("漏网接口", "新鲜度", f"在库漏网表均不落后于基准 {base}")
    if no_table:
        rep.items.append({
            "group": "漏网接口", "name": "本地无表", "status": "－",
            "detail": f"{len(no_table)} 个走替代源/未建库: {', '.join(no_table)}"})
    if undated:
        rep.items.append({
            "group": "漏网接口", "name": "无日期列", "status": "－",
            "detail": f"{len(undated)} 个有表但无日期列，无法判新鲜度: {', '.join(undated)}"})


# ============================================================
# 10. 事件面表（周期更新通道）陈旧监控 — 2026-10-11 新增（E-01）
# ============================================================
def check_periodic_tables(rep):
    """`config.PERIODIC_UPDATE_TARGETS` 的新鲜度监控 —— 回答"停更会不会被发现"。

    背景（2026-10-11 三轮架构审查 E-01，中级别）：
      `dividend`（公司行动装载源）与 `namechange`（ST 判定源）是 btf 引擎股票侧
      的数据源，但 mode=by_code ⇒ 既不在日更组、也不在豁免清单（豁免判据只扫
      by_date / freq=day）⇒ **无归宿、无陈旧监控**。实证：namechange 库内最新
      公告日停在 20260912（滞后 4 周），期间新分红/送转/ST 事件**静默缺失**，
      且表内「无行」与「真无事件」不可区分。

    判据（两条）：
      ① 陈旧：库内最新 `recency_col`（公告日类）落后今天 > `stale_after_days`
         ⇒ **提示**（不失败：停更窗口内的回测结论须降级，但环境本身没坏）
      ② 到期未跑：`fetch.periodic_update.is_due` 判为到期（间隔已过 / 数据陈旧）
         ⇒ 提示"可运行 python -m fetch.periodic_update 补"

    ⚠ 只读（读 parquet 列 + 状态文件），零 API 请求、零写入。
    """
    try:
        from config import PERIODIC_UPDATE_TARGETS
    except Exception as e:
        rep.warn("事件面表", "检查未执行", f"{str(e)[:60]}")
        return
    try:
        from fetch.periodic_update import data_recency, is_due, load_state, _days, _today
    except Exception as e:
        rep.warn("事件面表", "检查未执行", f"{str(e)[:60]}")
        return

    state = load_state()
    stale, due, fresh = [], [], []
    for name, spec in PERIODIC_UPDATE_TARGETS.items():
        recency = data_recency(name, spec["recency_col"])
        age = _days(recency, _today()) if recency else None
        limit = int(spec.get("stale_after_days", 45))
        is_due_now, why = is_due(name, spec, state)
        label = f"{name}={recency or '无数据'}"
        if recency is None:
            due.append(f"{name}（库内无数据）")
        elif age is not None and age > limit:
            stale.append(f"{label}(落后{age}天>{limit})")
        elif is_due_now:
            due.append(f"{name}（{why}）")
        else:
            fresh.append(f"{label}(落后{age}天)")

    if stale:
        rep.warn("事件面表", "陈旧", "⚠ " + ", ".join(stale)
                 + " —— 近端回测的公司行动/ST 判定可能静默缺失，结论须降级；"
                   "运行 `python -m fetch.periodic_update` 补齐")
    if due:
        rep.items.append({
            "group": "事件面表", "name": "到期未跑", "status": "－",
            "detail": f"{len(due)} 张表可按周期补: {', '.join(due)}"
                      f"（python -m fetch.periodic_update）"})
    if not stale and not due:
        rep.ok("事件面表", "周期通道",
               f"{len(fresh)} 张表均新鲜: {', '.join(fresh)}")
    elif not stale:
        rep.ok("事件面表", "陈旧", "无落后超阈值的表")


# ============================================================
# 10b. 库版本展示（EE-D11-1 动作 A 消费侧；2026-10-12 新增）
# ============================================================
def check_db_version(rep):
    """读取并展示库版本 `metadata/db_version.json`（08 §13.5；**展示级，不判失败**）。

    向后兼容：文件缺失 ⇒ 提示「未生成」（接线前的库没有该文件，属正常态；
    接线后由日更/回补/结构性变更自动写入）。只读、零请求、零写入。
    """
    try:
        from tools.db_version import show, version_path
    except Exception as e:
        rep.warn("库版本", "检查未执行", f"{str(e)[:60]}")
        return
    rec = show()
    if not rec:
        rep.items.append({
            "group": "库版本", "name": "db_version", "status": "－",
            "detail": "未生成（08 §13.5；接线后由日更/回补/结构性变更自动写入）"
                      f" — {version_path()}"})
        return
    detail = (f"{rec.get('version')} @ {rec.get('generated_at')}"
              f" | 变更表 {len(rec.get('tables_changed') or [])} 个"
              f" | checkpoint_rev={rec.get('checkpoint_rev')}")
    rep.ok("库版本", str(rec.get("version")), detail)
    hist = rec.get("history") or []
    if hist:
        rep.items.append({
            "group": "库版本", "name": "历史", "status": "－",
            "detail": " → ".join(h.get("version", "?") for h in hist[-6:])})


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
def _harden_stdout():
    """让 stdout 对不可编码字符降级而非抛错。

    为什么必须做（2026-10-11 实测）:
      本模块用 ⚠/✗ 符号渲染"提示/失败"。在 GBK 控制台下走管道
      （`python main.py doctor | Out-File x.txt` / CI 捕获）时，stdout 的
      encoding 是 GBK —— ⚠(U+26A0) 不在 GBK 中 ⇒ 汇总行
      `print(f"... {WARN} ...")` 直接抛 UnicodeEncodeError，
      **整个 doctor 以异常退出**（有提示项时 100% 触发，与检查内容无关）。
      errors="replace" 把不可编码字符降级为 `?`，命令行/机器人仍可读到全部
      检查结果（保真度损失仅限符号本身）。
    """
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass


def run_doctor(network=False, as_json=False):
    _harden_stdout()
    r = Report()
    check_env(r)
    check_paths(r)
    check_disk(r)
    check_checkpoints(r)
    check_data(r)
    check_credentials(r, network=network)
    check_config_integrity(r)
    check_dead_config_revival(r)
    check_leaky_permissions(r)
    check_periodic_tables(r)
    check_db_version(r)
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
