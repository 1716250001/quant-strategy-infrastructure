# -*- coding: utf-8 -*-
"""tools/db/freshness.py — 表级数据新鲜度报告（F4/R10，2026-11-08）

回答一个问题：**"数据到哪天" vs "源端到底有没有更新的"**。

双条件（§345 教训：内容日期 ≠ 新鲜度）
--------------------------------------
    ds = 今天 − 内容最新日期（读 parquet 日期列）
    dw = 今天 − 文件最新 mtime（读目录里最新 parquet 的写入时间）
**dw ≤ 7 直接绿**：刚写过、源端就这些（财报/宏观发布滞后属常态）。

分档阈值
--------
    日更表（DAILY_UPDATE_GROUPS ∪ DAILY_UPDATE_EXTRA）
        黄：ds > 7  且 dw > 7      红：ds > 30 且 dw > 30
    仅回补表
        黄：ds > 60 且 dw > 30     红：ds > 180 且 dw > 30
    alt 表：尊重 spec.max_age_days（金=正常，黄/红按 1x/3x 阈值）
    CC-FIX-14（§380）：「黄」且命中 `config.tables.PENDING_REFRESH` → 「待补拉」
        （只改写黄，不计入 `--strict` 告警；未命中的黄照常显示黄）

硬约束（CC-FIX-2R Step4）
-------------------------
1. **只读**：不写数据文件、不写断点、不建目录。
2. **恒 exit 0**；仅 `--strict` 时发现红/黄才非零。
3. 断点缺时间戳（F3 之前的旧文件）→ 显示"未知"，不算错、不改配置"修绿"。

用法
----
    python main.py db freshness                 # 全部表
    python main.py db freshness --only daily    # 只看几张（逗号分隔）
    python main.py db freshness --json          # 机器可读
    python main.py db freshness --strict        # 有红/黄时 exit 1
"""
import calendar
import glob
import json
import os
import time
from datetime import date, datetime

import pandas as pd
import pyarrow.parquet as pq

from config import MARKET_DATA_DIR
from config.backfill_targets import BACKFILL_TARGETS
from config.daily import DAILY_UPDATE_EXTRA, DAILY_UPDATE_GROUPS
from config.tables import PENDING_REFRESH, STALE_BY_DESIGN, STATIC_TABLES, TABLE_DATE_COL

# alt 断点文件（只读，不导入 alt 包以免触发写入守卫初始化）
ALT_CKPT = os.path.join(os.path.dirname(MARKET_DATA_DIR), "alt_data", ".alt_checkpoint.json")
MKT_CKPT = os.path.join(MARKET_DATA_DIR, ".backfill_checkpoint.json")

_PARTITION_SAMPLE = 3
# CC-FIX-12（2026-11-10，§378）：补月频/季频/周频列名——`cn_ppi`/`cn_m`/`sf_month` 用
# 小写 month、`cn_pmi` 用大写 MONTH、`cn_gdp` 用 quarter、`fut_weekly_detail` 用 week_date。
_DATE_COL_CANDIDATES = ("trade_date", "ann_date", "end_date", "date", "month",
                        "cal_date", "MONTH", "quarter", "week_date")

# 日更表名单（两组并集）
_DAILY_TABLES = set(DAILY_UPDATE_GROUPS) | set(DAILY_UPDATE_EXTRA)


def _pick_date_col(table_dir, preferred):
    """优先用权威定义；未登记则读 parquet schema 探一个日期列。"""
    if preferred:
        return preferred
    files = sorted(glob.glob(os.path.join(table_dir, "*.parquet")))
    if not files:
        return None
    try:
        names = set(pq.read_schema(files[-1]).names)
    except Exception:
        return None
    for c in _DATE_COL_CANDIDATES:
        if c in names:
            return c
    return None


def _to_date(v):
    """把 20261108 / '20261108' / 202608(月) / '2026Q3'(季) / Timestamp 统一成 date。

    CC-FIX-12（2026-11-10，§378）：月频/季频表此前一律解析失败 → ds=None → 判「未知」
    （监控盲区）。口径：**取期末日**（202608→2026-08-31、2026Q3→2026-09-30），
    既不把「月」误当「日」，也不因月初口径把月度释放时滞多算 30 天。
    """
    if v is None:
        return None
    s = str(v).strip()
    if len(s) >= 10 and s[4] in "-/":
        s = s[:10].replace("-", "").replace("/", "")
    if len(s) == 6 and s.isdigit():
        y, m = int(s[:4]), int(s[4:])
        if 1 <= m <= 12:
            return date(y, m, calendar.monthrange(y, m)[1])
        return None
    if len(s) == 6 and s[:4].isdigit() and s[4] in "Qq" and s[5].isdigit():
        q = int(s[5])
        if 1 <= q <= 4:
            return date(int(s[:4]), q * 3, calendar.monthrange(int(s[:4]), q * 3)[1])
        return None
    if not (s.isdigit() and len(s) == 8):
        return None
    try:
        return datetime.strptime(s, "%Y%m%d").date()
    except ValueError:
        return None


def _latest_in_files(files, dc):
    best = None
    for f in files:
        try:
            s = pd.read_parquet(f, columns=[dc])[dc].dropna()
        except Exception:
            continue
        if not len(s):
            continue
        v = str(s.max())
        best = v if best is None else max(best, v)
    return best


def _as_of(table_dir, dc):
    """返回 (最新数据日, 取样说明)。布局无关，只读少量分区。"""
    if not os.path.isdir(table_dir):
        return None, "目录不存在"
    files = sorted(glob.glob(os.path.join(table_dir, "*.parquet")))
    if not files:
        return None, "无 parquet"
    names = [os.path.basename(f) for f in files]
    year_like = [n for n in names if len(n) == 12 and n[:4].isdigit()]
    if len(year_like) == len(files):
        picked = files[-_PARTITION_SAMPLE:]
        return _latest_in_files(picked, dc), f"by_year 末 {len(picked)} 分区"
    if any("." in n[:-8] for n in names):
        picked = files[:5]
        return _latest_in_files(picked, dc), "by_code 抽样 5 码"
    return _latest_in_files(files[:2], dc), "single"


def _newest_mtime(table_dir):
    """目录内最新 parquet 的 mtime（秒）；无文件返回 None。"""
    files = glob.glob(os.path.join(table_dir, "*.parquet"))
    if not files:
        return None
    return max(os.path.getmtime(f) for f in files)


def _days_since(ts_epoch):
    if ts_epoch is None:
        return None
    return max(0, int((time.time() - ts_epoch) // 86400))


def _days_since_iso(s):
    """ISO 时间戳 → 距今天数；缺失/不可解析返回 None（CC-FIX-7 Step3）。"""
    if not s:
        return None
    try:
        return max(0, (datetime.now() - datetime.fromisoformat(str(s))).days)
    except Exception:
        return None


def _diag(r):
    """诊断列：分离「每天都拉、源端无新数据」与「没人拉」。

    CC-FIX-7（2026-11-09，§362）：**只做诊断，不参与 `_verdict()`**。
    CC-FIX-14（2026-11-10，§380）：market 侧补第 4 态「目录缺失」；alt 侧补齐同列
    （此前恒空，§十.1）。两库口径一致：dw≤7 不诊断；alt 的 last_pull 取
    last_attempt，同为「最近一次尝试拉取」时刻。
    """
    if r.get("source") == "目录不存在":
        return "目录缺失"
    dw = r.get("dw")
    if dw is None or dw <= 7:
        return ""
    _lf_days = _days_since_iso(r.get("last_pull"))
    if _lf_days is None:
        return "无拉取记录"
    return "已拉取·源端无更新" if _lf_days <= 7 else "拉取记录陈旧"


def _load_ckpt(path):
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _verdict(ds, dw, tier, stale_days=None):
    """绿=正常 / 黄=关注 / 红=滞后 / 未知。dw≤7 一律绿。"""
    if ds is None and dw is None:
        return "未知"
    if dw is not None and dw <= 7:
        return "正常"
    if tier == "alt" and stale_days:
        if ds is None:
            return "未知"
        if ds > stale_days * 3:
            return "红"
        return "黄" if ds > stale_days else "正常"
    if tier == "daily":
        y_ds, y_dw, r_ds, r_dw = 7, 7, 30, 30
    else:
        y_ds, y_dw, r_ds, r_dw = 60, 30, 180, 30
    if ds is None or dw is None:
        return "未知"
    if ds > r_ds and dw > r_dw:
        return "红"
    if ds > y_ds and dw > y_dw:
        return "黄"
    return "正常"


def _market_rows(only=None):
    cp = _load_ckpt(MKT_CKPT)
    rows = []
    for name in BACKFILL_TARGETS:
        if only and name not in only:
            continue
        d = os.path.join(MARKET_DATA_DIR, name)
        dc = _pick_date_col(d, TABLE_DATE_COL.get(name))
        as_of, how = _as_of(d, dc) if dc else (None, "未登记日期列")
        rec = cp.get(name, {}) if isinstance(cp.get(name), dict) else {}
        rows.append({
            "lib": "market", "table": name, "as_of": as_of,
            "last_pull": rec.get("last_fetch"),
            "last_pull_rows": rec.get("last_fetch_rows"),
            "tier": "daily" if name in _DAILY_TABLES else "backfill",
            "newest_mtime": _newest_mtime(d), "source": how, "stale_days": None,
        })
    return rows


def _alt_rows(only=None):
    from alt.spec import BY_NAME
    cp = _load_ckpt(ALT_CKPT)
    root = os.path.dirname(ALT_CKPT)
    rows = []
    for name, sp in BY_NAME.items():
        if only and name not in only:
            continue
        d = os.path.join(root, name)
        as_of, how = _as_of(d, sp.date_col)
        rec = cp.get(name, {}) if isinstance(cp.get(name), dict) else {}
        rows.append({
            "lib": "alt", "table": name, "as_of": as_of, "last_pull": rec.get("last_attempt"),
            "last_pull_rows": None,
            "tier": "alt", "newest_mtime": _newest_mtime(d), "source": how,
            "stale_days": int(getattr(sp, "max_age_days", 120) or 120),
        })
    return rows


def build_rows(only=None):
    rows = _market_rows(only) + _alt_rows(only)
    today = date.today()
    _alt_specs = {}
    if any(r["lib"] == "alt" for r in rows):
        from alt.spec import BY_NAME as _ALT_BY_NAME  # 惰性导入（同 _alt_rows）
        _alt_specs = _ALT_BY_NAME
    for r in rows:
        d = _to_date(r["as_of"])
        r["as_of_date"] = d.isoformat() if d else None
        r["ds"] = (today - d).days if d else None
        r["dw"] = _days_since(r["newest_mtime"])
        r.pop("newest_mtime", None)
        r["verdict"] = _verdict(r["ds"], r["dw"], r["tier"], r["stale_days"])
        # CC-FIX-6（2026-11-09，§362）：两档特判 —— 名单之外的表判定逐字不变。
        # ⚠ 只对 market 库生效：alt 库存在同名表 `hibor`（实测 2026-11-09 正常），
        #   按表名全局匹配会把 alt.hibor 误标「已停更」（首跑实证）。
        r["note"] = ""
        name = r["table"]
        if r["lib"] == "market" and name in STALE_BY_DESIGN:
            r["verdict"] = "已停更"
            r["note"] = f"自 {STALE_BY_DESIGN[name]['since']} 停更"
        elif r["lib"] == "market" and name in STATIC_TABLES:
            # 静态名录表：无「内容日期」语义 ⇒ 只判「目录存在且有 parquet」。
            # 取舍：config 里的 min_rows 下限**本版不校验** —— 逐表全量读行数
            #       成本太高（_as_of 只抽样 2~3 个分区），留作后续判据再启用。
            _root = (MARKET_DATA_DIR if r["lib"] == "market"
                     else os.path.dirname(ALT_CKPT))
            _has = bool(glob.glob(os.path.join(_root, name, "*.parquet")))
            r["verdict"] = "正常" if _has else "红"
            r["note"] = "静态名录"
        elif r["lib"] == "market" and (BACKFILL_TARGETS.get(name) or {}).get("enabled") is False:
            # CC-FIX-12（2026-11-10，§378）：配置侧显式关闭（tier X，建库价值有限，
            #   bc_bestotcqt/stk_mins/report_rc）⇒ 永不进更新通道，按「已停更」呈现。
            #   复用既有判定词、以既有配置为证，**不新增档位**。
            r["verdict"] = "已停更"
            r["note"] = "未启用（enabled=False）"
        elif r["lib"] == "alt":
            # CC-FIX-12：alt 侧复用 spec 既有语义（alt/update.py 同源），不新增档位：
            #   has_date=False → 静态快照（"无日期维度"，判有数据即正常）
            #   spec.note 标「已废弃」且未建 → 已废弃（us_daily_qfq，§159）
            #   known_stale 且未建 → 源端停更（macro_bank_brazil/india，Wind 线承接）
            _sp = _alt_specs.get(name)
            _root = os.path.dirname(ALT_CKPT)
            _has = bool(glob.glob(os.path.join(_root, name, "*.parquet")))
            if _sp is not None and not getattr(_sp, "has_date", True):
                r["verdict"] = "正常" if _has else "红"
                r["note"] = "静态快照（无日期维度）"
            elif not _has and getattr(_sp, "known_stale", ""):
                r["verdict"] = "已停更"
                r["note"] = "源端停更·未建表"
            elif not _has and "已废弃" in (getattr(_sp, "note", "") or ""):
                r["verdict"] = "已停更"
                r["note"] = "已废弃（spec.note 标注）"
        # CC-FIX-14（2026-11-10，§380）：**仅**「黄 ∧ 命中补拉登记」→「待补拉」。
        #   其余判定逐字不变（未命中的黄仍显示黄，防吞警）；strict 的 n_alert
        #   只数红/黄，故「待补拉」不计警——strict 逻辑本身不改。
        if r["verdict"] == "黄" and name in PENDING_REFRESH:
            _pr = PENDING_REFRESH[name]
            r["verdict"] = "待补拉"
            r["note"] = f"补拉计划: {_pr['plan']}（{_pr['decided']} 决定）"
        r["diag"] = _diag(r)          # CC-FIX-7：诊断列（不参与 verdict）
    return rows


def render(rows):
    head = (f"{'库':6s} {'表':24s} {'最新数据日':10s} {'ds':>4s} {'dw':>4s} "
            f"{'最近拉取':20s} {'诊断':18s} {'档':9s} 判定")
    lines = [head, "-" * 110]
    for r in rows:
        _note = f"  ({r['note']})" if r.get("note") else ""
        lines.append(
            f"{r['lib']:6s} {r['table']:24s} {r['as_of_date'] or '-'!s:10s} "
            f"{('-' if r['ds'] is None else r['ds']):>4} {('-' if r['dw'] is None else r['dw']):>4} "
            f"{r['last_pull'] or '未知'!s:20s} {r.get('diag') or '':18s} "
            f"{r['tier']:9s} {r['verdict']}{_note}")
    n_red = sum(1 for r in rows if r["verdict"] == "红")
    n_yel = sum(1 for r in rows if r["verdict"] == "黄")
    n_unk = sum(1 for r in rows if r["verdict"] == "未知")
    n_stale = sum(1 for r in rows if r["verdict"] == "已停更")
    n_pend = sum(1 for r in rows if r["verdict"] == "待补拉")
    lines.append("-" * 110)
    lines.append(f"合计 {len(rows)} 张表：正常 {len(rows) - n_red - n_yel - n_unk - n_stale - n_pend} | "
                 f"待补拉 {n_pend} | 黄 {n_yel} | 红 {n_red} | 已停更 {n_stale} | 未知 {n_unk}")
    _diag_ct = {"已拉取·源端无更新": 0, "拉取记录陈旧": 0, "无拉取记录": 0, "目录缺失": 0}
    for r in rows:
        if r.get("diag") in _diag_ct:
            _diag_ct[r["diag"]] += 1
    lines.append(f"诊断: 已拉取·源端无更新 {_diag_ct['已拉取·源端无更新']} | "
                 f"拉取记录陈旧 {_diag_ct['拉取记录陈旧']} | "
                 f"无拉取记录 {_diag_ct['无拉取记录']} | "
                 f"目录缺失 {_diag_ct['目录缺失']}")
    if n_red or n_yel:
        lines.append("红表: " + ", ".join(r["table"] for r in rows if r["verdict"] == "红"))
        lines.append("黄表: " + ", ".join(r["table"] for r in rows if r["verdict"] == "黄"))
    if n_pend:
        lines.append("待补拉: " + ", ".join(
            r["table"] for r in rows if r["verdict"] == "待补拉"))
    if n_stale:
        lines.append("已停更: " + ", ".join(
            f"{r['table']}({STALE_BY_DESIGN.get(r['table'], {}).get('since', '源端停更')})"
            for r in rows if r["verdict"] == "已停更"))
    lines.append("说明: ds=内容最新日期距今天数, dw=文件最新 mtime 距今天数; dw≤7 视为刚写过→绿")
    lines.append("      默认恒 exit 0；--strict 时存在红/黄才非零")
    return "\n".join(lines)


def run(only=None, as_json=False, strict=False):
    try:
        rows = build_rows(only)
    except Exception as exc:
        # 诊断工具不得因单表异常拖垮整份报告
        if as_json:
            print(json.dumps({"ok": False, "error": repr(exc)}, ensure_ascii=False))
        else:
            print(f"[freshness] 生成失败（默认仍 exit 0）: {exc!r}")
        return 0
    n_alert = sum(1 for r in rows if r["verdict"] in ("红", "黄"))
    if as_json:
        print(json.dumps({"ok": True, "rows": rows,
                          "n_alert": n_alert}, ensure_ascii=False, indent=1))
    else:
        print(render(rows))
    if strict and n_alert:
        return 1
    return 0
