# -*- coding: utf-8 -*-
"""
alt/list_backfill.py — 通用 list 模式引擎（建库 / 轮转更新 / 整表重建）
=======================================================================
（v1.2 外盘接入，2026-10-11；设计 06 §10.9 / ADR-13/14/15；审查放行 §4.3-⑥）
（v1.3.1，2026-10-11：F-15 修复（rebuild 水位/覆盖率按**成功落盘**批次收编）+
  机读覆盖率 `--json` / `--strict-coverage`（O-1）+ 分区快照行数+sha256（§10.6））

职责：
  1. **清单适配**：spec.universe_from（"master:hk_basic"）→ 标的列表
     （主库只读；`strip_hk_suffix` 变换 = 去 ".HK" 后缀）
  2. **轮转调度**：按 spec.rotate_days 摊薄（"最久未拉"优先），
     水位独立持久化 `_meta/rotate_state.json`（与三态断点**分文件分权责**）
  3. **拉取**：socket 超时补丁 + **串行 + AltRateLimiter**（sina 20/分）
  4. **双阈值缓冲** → flush → **先落盘、后记水位**（INV-3 / I7）
  5. **--rebuild**：影子目录 `{table}__new/` → 校验 → **文件级原子替换**
     （旧文件进 `{table}__old/` 保留 1 轮；ADR-15）

⚠ 四条纪律（勿改）：
  ① **禁止无 limiter 直发**——us_backfill 的 4 线程无限流是历史偏差（R-16），
     本引擎不复制；串行 + sina 20/分 是港股的合规前提（06 §10.9）。
  ② **复权展开唯一入口** = `alt.reader.expand_qfq_factor`；本引擎只负责
     "拉取 + 落盘"，**不做任何复权计算**（防"两套实现各管一摊"）。
  ③ **水位只在落盘成功后推进**；flush 失败则该批不记（下次自然重拉）。
  ④ **V8 预热**：`stock_hk_daily` 用 MiniRacer——若未来放宽为多线程，
     必须**先单线程预热**再并发（us 实测：未预热直接 4 线程会 FATAL 崩溃）。
     当前串行执行，天然无此问题。

用法:
  python -m alt.list_backfill --table hk_daily                  # 建库或轮转（按水位）
  python -m alt.list_backfill --table hk_daily --rebuild        # 整表重建（影子目录）
  python -m alt.list_backfill --table hk_daily --symbols 00700,00981   # 指定标的（调试）
  python -m alt.list_backfill --table hk_daily --budget-syms 20        # 限量试跑
  python -m alt.list_backfill --status                          # 轮转进度（零请求）
  python -m alt.list_backfill --table hk_daily --dry-run        # 只打印计划

⚠ `--status --json` 键集（**契约**，R6R-3；勿擅改）：
  每表固定键 = {total, fresh, stale, oldest, newest, rotate_done, checkpoint_done}
  ＋ 异常时附 error。其中：
    · rotate_done      = RotateState（本引擎的轮转水位）条数
    · checkpoint_done  = AltCheckpoint（us_backfill/backfill 的一次性断点）done 条数
  两者**互不可见**，故并列输出（§140 R6-1 修的就是"只看前者 ⇒ 显示 done=0 假象"）。
  新增/删除键属接口变更，须先获批（§142 R6R-2）后再动。
"""
import argparse
import hashlib
import json
import math
import os
import shutil
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from config_alt import (
    ALT_META_DIR,
    FLUSH_BYTES,
    FLUSH_ROWS,
    FLUSH_SYMS,
    HK_BATCH_SIZE,
    ROTATE_STATE_FILE,
    TS_DATA_DIR,
    alt_table_dir,
    assert_writable,
    ensure_alt_dirs,
)
from common.parquet_store import list_parquet_files
from alt import spec as spec_mod
from alt import io as alt_io
from alt.rate import AltRateLimiter
from alt.urls import guess_url
from alt.backfill import AltCheckpoint
from alt.us_backfill import SOCKET_TIMEOUT, _patch_requests_timeout


# ============================================================
# 一、轮转水位（rotate_state.json）
# ============================================================
class RotateState:
    """轮转水位：{table: {symbol: "YYYY-MM-DD"}}（04 §8.6，与三态断点分权责）。

    ⚠ 语义（ADR-14）：记"该标的最近一次**成功落盘**的轮转日期"；
      仅在 flush 成功后推进（INV-3 / I7）。中断不推进 => 下次优先重拉。
    """

    def __init__(self, path=ROTATE_STATE_FILE, dry_run=False):
        self.path = path
        self.dry_run = dry_run
        self.data = {}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception:  # noqa: BLE001
                self.data = {}

    def last_done(self, table, symbol):
        return self.data.get(table, {}).get(str(symbol), "")

    def mark_done(self, table, symbols, day=None):
        """批量推进水位（symbols: list/str）。无变化不重写文件。"""
        if self.dry_run or not symbols:
            return
        if isinstance(symbols, str):
            symbols = [symbols]
        day = day or datetime.now().strftime("%Y-%m-%d")
        rec = self.data.setdefault(table, {})
        changed = False
        for s in symbols:
            if rec.get(str(s)) != day:
                rec[str(s)] = day
                changed = True
        if not changed:
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        assert_writable(self.path)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def progress(self, table, symbols, rotate_days=None):
        """(fresh, stale, oldest, newest)：按 rotate_days 判 stale（零请求）。"""
        rd = rotate_days if rotate_days else 7
        rec = self.data.get(table, {})
        today = datetime.now()
        fresh = stale = 0
        dates = []
        for s in symbols:
            d = rec.get(str(s), "")
            if not d:
                stale += 1
                continue
            dates.append(d)
            try:
                age = (today - datetime.strptime(d, "%Y-%m-%d")).days
            except Exception:  # noqa: BLE001
                age = 10 ** 6
            if age <= rd:
                fresh += 1
            else:
                stale += 1
        return fresh, stale, (min(dates) if dates else ""), (max(dates) if dates else "")


# ============================================================
# 一·五、进度心跳 + 分段停机（§128 R-E3）
# ------------------------------------------------------------
# 动机：R-2 是 15 小时作业。旧观测手段下进程静默死亡可能数小时无人知
#       （2026-10-24 14:07 实证），分段起停靠人记（当日已失守一次 16:30）。
#   ① 心跳文件 {ALT_META_DIR}/<table>_progress.json —— 外部看门狗只读
#      `updated_at`：超 15 min 未变即告警。**字段名已冻结，不得擅改。**
#   ② `--until HH:MM` —— 到点只停"发起新批次"，在途批次完成并落盘后退出，
#      绝不在请求中途硬中断（会造成该只数据半截）。
# ============================================================

# `--until` 允许跨午夜的滚动窗口（小时）：若 HH:MM 今日已过，
# 且距**明日同一时刻**不超过本值，视为"跨夜停机点"（如 23:00 起跑 --until 06:30）；
# 超过本值则判为"已过期"（明显是记错时刻），立即退出码 2。
UNTIL_MAX_ROLL_HOURS = 12


class ProgressHeartbeat:
    """进度心跳文件（§128 T1-1）—— 原子写，14 个字段名冻结（看门狗按此解析）。"""

    FIELDS = ("table", "pid", "phase", "started_at", "updated_at", "done", "total",
              "pct", "rate_sym_per_s", "eta_min", "last_batch", "batch_ok",
              "batch_fail", "until")

    def __init__(self, table, phase, total, until=None):
        self.path = os.path.join(ALT_META_DIR, f"{table}_progress.json")
        self.table = table
        self.phase = phase
        self.total = max(int(total), 1)
        self.until = until or None
        self.started = datetime.now().astimezone()
        self.t0 = time.time()

    def write(self, done=0, last_batch=0, batch_ok=0, batch_fail=0, extra=None):
        """覆写心跳（tmp + os.replace 原子写，外部永不会读到半截 JSON）。"""
        now = datetime.now().astimezone()
        el = max(time.time() - self.t0, 1e-6)
        rate = (done / el) if done else 0.0
        payload = {
            "table": self.table,
            "pid": os.getpid(),
            "phase": self.phase,
            "started_at": self.started.isoformat(timespec="milliseconds"),
            "updated_at": now.isoformat(timespec="milliseconds"),
            "done": int(done),
            "total": int(self.total),
            "pct": round(done / self.total * 100, 2),
            "rate_sym_per_s": round(rate, 3),
            "eta_min": round((self.total - done) / rate / 60, 1) if rate > 0 else None,
            "last_batch": int(last_batch),
            "batch_ok": int(batch_ok),
            "batch_fail": int(batch_fail),
            "until": self.until,
        }
        if extra:
            payload.update(extra)
        ensure_alt_dirs()
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)
        return payload


def parse_until(spec_str, now=None):
    """解析 `--until HH:MM` → 目标 datetime。

    返回 (target_dt, err)。err 非空 ⇒ 调用方应立即退出码 2。
    · 今日 HH:MM 未到 → 今日
    · 今日已过且距明日同一时刻 ≤ UNTIL_MAX_ROLL_HOURS → **明日**（跨夜停机点）
    · 否则 → err="已过期"
    """
    s = str(spec_str or "").strip()
    if not s:
        return None, ""
    try:
        hh, mm = s.split(":")
        hh, mm = int(hh), int(mm)
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError
    except Exception:  # noqa: BLE001
        return None, f"--until 格式非法: {spec_str!r}（应为 HH:MM）"
    now = now or datetime.now()
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target > now:
        return target, ""
    roll = target + timedelta(days=1)
    if (roll - now).total_seconds() <= UNTIL_MAX_ROLL_HOURS * 3600:
        return roll, ""
    return None, (f"--until {s} 已过期（当前 {now.strftime('%H:%M')}），"
                  f"请检查时刻")


# ============================================================
# 二、清单适配（universe_from）
# ============================================================
def _read_master_table(table):
    """读主库表（**只读**；single 优先，by_year 兜底 concat）。"""
    d = os.path.join(TS_DATA_DIR, table)
    single = os.path.join(d, f"{table}.parquet")
    if os.path.exists(single):
        return pd.read_parquet(single)
    files = list_parquet_files(d, sort=True, with_dir=True)
    if not files:
        raise FileNotFoundError(f"主库无此表: {d}（universe_from 指向的清单表）")
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


def _read_alt_table(name):
    """读 **alt 库内**清单表（§125 RE2-1 新增；只读，single 优先，by_year 兜底 concat）。

    为什么需要：us 语义的清单（`us_universe` / `us_daily_universe`）落在 alt 库，
    原先 `load_universe` 只认 `master:` 前缀 ⇒ `us_daily_raw --dry-run` 直接
    `ValueError` 崩掉（§123.2.1 实测 EXIT=1），全量建库无法启动。
    """
    d = alt_table_dir(name)
    single = os.path.join(d, f"{name}.parquet")
    if os.path.exists(single):
        return pd.read_parquet(single)
    files = list_parquet_files(d, sort=True, with_dir=True)
    if not files:
        raise FileNotFoundError(
            f"alt 库无此表: {d}（universe_from 指向的清单表；"
            f"若为派生清单，请先按派单生成）")
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


def load_universe(sp, verbose=True):
    """清单适配 → 标的列表（保序去重）。

    两种来源（§125 RE2-1）：
      · `universe_from="master:<表名>"` → 读主库 `TS_DATA_DIR/<表名>`（原有路径，行为逐字不变）
      · `universe_from="<表名>"`（无前缀）→ 读 **alt 库** `ALT_DATA_DIR/<表名>`
      · 空 → 报错（该表必须靠 --symbols 显式指定）
    """
    src = sp.universe_from or ""
    if src.startswith("master:"):
        table = src.split(":", 1)[1]
        df = _read_master_table(table)
        label = f"master:{table}"
    elif src:
        table = src
        df = _read_alt_table(table)
        label = f"alt:{table}"
    else:
        raise ValueError(
            f"{sp.name}: universe_from 为空——请用 --symbols 显式指定标的，"
            f"或在 spec 中声明 'master:<表名>' / alt 内清单表名")
    col = sp.universe_col
    if col not in df.columns:
        raise KeyError(f"清单表 {table} 缺少列 '{col}'（实际: {list(df.columns)[:12]}）")
    s = df[col].astype(str)
    if sp.universe_xform == "strip_hk_suffix":
        s = s.str.replace(r"\.HK$", "", regex=True)      # "00700.HK" → "00700"
    elif sp.universe_xform:
        raise ValueError(f"未知 universe_xform: {sp.universe_xform!r}")
    seen, out = set(), []
    for x in s.tolist():
        x = str(x).strip()
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    if verbose:
        print(f"[清单] {sp.name} ← {label}.{col}"
              f"（变换 {sp.universe_xform or '原样'}）→ {len(out):,} 只")
    return out


def select_batch(symbols, sp, state, budget=None, verbose=True):
    """按"最久未拉"选本轮批次：K = ceil(标的数 / rotate_days)（或 --budget-syms 指定）。

    ⚠ K 是**只数**；每只 = 价格 + 因子两条请求 => 请求数 = K × 2
      （默认 7 天 => K≈398 只 => 796 请求/天，06 §10.9 速率核算）。
    """
    if sp.rotate_days <= 0:
        todo = [s for s in symbols if not state.last_done(sp.name, s)]
        if verbose:
            print(f"[批次] rotate_days=0（不轮转）→ 未拉过 {len(todo):,} 只")
        return todo
    k = budget or math.ceil(len(symbols) / sp.rotate_days)
    k = min(k, len(symbols))
    ordered = sorted(symbols, key=lambda s: (state.last_done(sp.name, s) or ""))
    todo = ordered[:k]
    if verbose and todo:
        oldest = state.last_done(sp.name, todo[0]) or "（从未拉）"
        print(f"[批次] K={k} 只（=ceil({len(symbols):,}/{sp.rotate_days})"
              f"{'；--budget-syms 覆盖' if budget else ''}）"
              f" => 请求 ≈ {k * 2} 条（价格+因子两表合计）｜队首水位={oldest}")
    return todo


# ============================================================
# 二·五、调度层保护（OS-R6 / R6-2，2026-10-26）
# ------------------------------------------------------------
# 背景（§140.0 对账）：`AltCheckpoint`（`us_backfill`/`backfill` 写）与
# `RotateState`（本引擎写）是**两套互不可见的记录**。`us_daily_qfq` 盘上
# 3,040 万行、ckpt done=18,052，但 RotateState **无此表** ⇒ `select_batch`
# 把它当成"从未拉过"，一次误启动就会把全量重拉一遍（≈15 h 白烧）。
# ============================================================
def _table_disk_rows(name):
    """盘上该表的 (分区数, 数据行数)——按 parquet **footer 元数据**统计。

    为什么读 footer 而不是 `pd.read_parquet`：`us_daily_raw` 3,058 万行 / 55 分区，
    只读一列也要数秒且吃内存；footer 的 num_rows 是 O(分区数) 的元数据读。
    footer 读不出来时按"该分区有数据"保守计 1 行（宁可拦住，不可放行重拉）。
    """
    d = alt_table_dir(name)
    if not os.path.isdir(d):
        return 0, 0
    files = sorted(f for f in os.listdir(d) if f.endswith(".parquet"))
    if not files:
        return 0, 0
    import pyarrow.parquet as pq
    n_rows = 0
    for f in files:
        try:
            n_rows += int(pq.ParquetFile(os.path.join(d, f)).metadata.num_rows)
        except Exception:  # noqa: BLE001
            n_rows += 1
    return len(files), n_rows


def _guard_schedule_state(sp, state, rebuild, dry_run, verbose=True):
    """R6-2 调度层保护：RotateState 无记录 + 盘上已有数据 ⇒ **发请求前**拒绝。

    判定顺序（缺一不可）：
      ① `--dry-run` 直接放行（零请求，且 §140 R6-4 要求 dry-run 输出不变）；
      ② `--rebuild` 放行（显式全量重建语义，唯一越过口；打印说明）；
      ③ RotateState 有该表记录 ⇒ 放行（正常路径，行为与改动前逐字一致）；
      ④ RotateState 无记录且盘上有 parquet、行数 > 0 ⇒ 退出码 **4**。

    ⚠ 为什么不做"自动补水位"：补水位等于**替操作者下结论**"盘上数据就是全量"，
      而该结论恰恰是本次要防的误判（盘上可能只是历史残留的一部分）；
      错误地补水位会让后续轮转**跳过真正缺的标的**。故只拦截 + 给出两条出路。
    """
    if dry_run:
        return
    if rebuild:
        if verbose:
            print("[R6-2] --rebuild：显式全量重建，越过调度层保护")
        return
    if state.data.get(sp.name):
        return
    n_files, n_rows = _table_disk_rows(sp.name)
    if n_files == 0 or n_rows <= 0:
        return
    print("=" * 84)
    print(f"[R6-2] 拒绝执行：{sp.name} 的 RotateState 无记录，"
          f"但盘上已有 {n_files} 个分区 / {n_rows:,} 行数据")
    print("  两套记录互不可见（AltCheckpoint vs RotateState）——若继续，"
          "select_batch 会把全部标的当成「从未拉过」重新拉一遍。")
    print("  处置（二选一）：")
    print("    ① 确认确需全量重拉 ⇒ 追加 --rebuild（显式越过本保护）")
    print("    ② 只想看进度 ⇒ python -m alt.list_backfill --table "
          f"{sp.name} --status")
    print("=" * 84)
    raise SystemExit(4)


# ============================================================
# 三、拉取（串行 + limiter）
# ============================================================
def fetch_one(sp, symbol, limiter, url):
    """拉单只（经 limiter；limiter 内已含 3 次重试与风控静默）。
    返回 (symbol, DataFrame|None, err)；成功时**注入标的列**（接口不返回）。
    """
    import akshare as ak
    fn = getattr(ak, sp.func, None)
    if fn is None:
        return symbol, None, f"akshare 无此函数: {sp.func}"
    kw = dict(sp.params)
    kw[sp.symbol_param or "symbol"] = symbol          # 接口参数名（akshare 惯例 symbol）
    df, ok, err = limiter.call(lambda: fn(**kw), url)
    if not ok:
        return symbol, None, err
    if df is None or len(df) == 0:
        return symbol, None, "empty"
    d = df.copy()
    d[sp.symbol_col or "symbol"] = symbol             # 注入**标的列**（与 subset 一致）
    return symbol, d, None


# ============================================================
# 四、缓冲与落盘（先落盘、后记水位）
# ============================================================
def _should_flush(buf, batch_syms, batch_size):
    """flush 判据：**三阈值取先到**（F-10 修复，2026-10-11；ADR-15 / 14 §22.7）。

    ① 只数：本批已处理只数 ≥ `batch_size`（默认 `HK_BATCH_SIZE` = `FLUSH_SYMS` = 300）
    ② 行数：缓冲行数 ≥ `FLUSH_ROWS`（1,000,000）
    ③ 字节：缓冲内存 ≈ ≥ `FLUSH_BYTES`（256 MB）——防"小行数表下只数阈值
       一次性读入过多行"的内存尖峰（F-10 指出的潜在危害路径）。

    ⚠ 为什么必须接线（而非"配置在册代码不读"）：本库高频缺陷族 #9
      "服务已实现 ≠ 已接线"——配置声明了三阈值、实现只用单阈值即**文档≠实现**。
    """
    if batch_syms and len(batch_syms) >= batch_size:
        return True
    if not buf:
        return False
    if len(buf) >= batch_size:
        return True
    buf_rows = sum(len(d) for _, d in buf)
    if buf_rows >= FLUSH_ROWS:
        return True
    buf_bytes = sum(int(d.memory_usage(deep=True).sum()) for _, d in buf)
    return buf_bytes >= FLUSH_BYTES


def flush_buffer(sp, buf, dest_dir=None):
    """把缓冲区归一化并落盘。**只落盘、不记水位**——由调用方在成功后再记，
    以保证 INV-3：先落盘、后记水位（失败则什么都不记，下次自然重拉）。

    返回 dict(ok, rows, added, warnings, errors)
    """
    if not buf:
        return {"ok": True, "rows": 0, "added": 0, "warnings": [], "errors": []}
    raw = pd.concat([d for _, d in buf], ignore_index=True)
    norm, warns = alt_io.normalize(raw, sp)
    res = alt_io.save(norm, sp, dest_dir=dest_dir)
    return {"ok": bool(res.get("ok")), "rows": res.get("rows", 0),
            "added": res.get("added", 0),
            "warnings": list(warns) + list(res.get("warnings", [])),
            "errors": list(res.get("errors", []))}


# ============================================================
# 五、影子目录（--rebuild）
# ============================================================
def _verify_shadow(sp, new_dir, verbose=True):
    """影子目录校验：**逐分区非空**（F-9 修复，2026-10-11）。

    原先只判"总行数 > 0"——残缺的影子目录（缺年份分区）照样通过（F-9 实测）。
    现逐文件校验：
      ① 目录内至少 1 个 parquet；
      ② **每个分区文件至少 1 行**（by_year 表 = 每年至少 1 行，空分区不得进正式目录）。
    返回 {分区文件名: 行数}（供切换守卫与最终汇总）。
    """
    files = sorted(f for f in os.listdir(new_dir) if f.endswith(".parquet"))
    if not files:
        raise RuntimeError(f"影子目录无 parquet 文件: {new_dir}")
    counts = {}
    for f in files:
        n = len(pd.read_parquet(os.path.join(new_dir, f), columns=[sp.date_col]))
        if n <= 0:
            raise RuntimeError(
                f"[F-9 守卫] 影子目录分区为空: {f}（0 行）——"
                f"空分区不得进正式目录（正式目录零改动）")
        counts[f] = n
    if verbose:
        print(f"[校验] 影子目录 {len(files)} 分区 / {sum(counts.values()):,} 行"
              f"（逐分区非空 OK，按 {sp.date_col} 计）")
    return counts


def _sha256_file(path, chunk_size=1 << 20):
    """文件 SHA256（分块读；29 分区 ≈130 MB 总开销约 1~2 s，B2/§10.6 评估可接受）。"""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def _official_partition_state(sp):
    """正式目录分区状态：{文件名: {"rows": n, "sha256": hex}}（F-9/F-11 + B2/§10.6）。

    - rows：数据行数——**空分区**（0 行）允许被 new 弃置（F-11）；读取失败记 -1，
      按"非空"**保守处理**（宁可拦死不可丢数据）；
    - sha256：文件内容哈希（§10.6 升级：切换前快照可事后与 `__old` 逐位对账）。
    """
    d = alt_table_dir(sp.name)
    if not os.path.isdir(d):
        return {}
    state = {}
    for f in sorted(os.listdir(d)):
        if not f.endswith(".parquet"):
            continue
        p = os.path.join(d, f)
        try:
            rows = len(pd.read_parquet(p, columns=[sp.date_col]))
            state[f] = {"rows": rows, "sha256": _sha256_file(p)}
        except Exception:
            state[f] = {"rows": -1, "sha256": ""}   # 读失败 ⇒ 保守按"非空"处理
    return state


def _switch_shadow(sp, new_dir, official_state=None, verbose=True):
    """影子目录 → 正式目录：**文件级原子替换**（ADR-15）+ **分区守卫**（F-9/F-11）。

    ⚠ F-9 守卫（P1 修复）：原实现切完后 official 分区集合**完全等于 new**——
      new 缺失的年份（某年拉取全空 / rebuild 中断 / 误用 --symbols 部分标的）
      会被**静默删除**（沙盒复现：3 分区 → 1 分区，属目录级数据丢失）。
    ⚠ F-11 精确化（P3 修正）：守卫**只针对"非空分区"**——official 的**空分区**
      （0 行，历史遗留 / 上游撤回）允许被 new 弃置（打印说明，无数据损失）；
      否则一旦 official 误入空分区，rebuild 将被**永久拦死**（分区棘轮，无逃逸口）。
      确需丢弃 official **非空**分区的异常场景（源端撤回），报错给出人工清理指引。

    ① 正式目录现有文件 → `{table}__old/`（清旧 __old，保留 1 轮）
    ② 影子目录文件 → 正式目录（os.replace，同盘原子）
    ③ 清理影子目录
    失败处置：打印从 `__old` 回退的命令。
    """
    official = alt_table_dir(sp.name)
    old = official + "__old"
    new_files = sorted(f for f in os.listdir(new_dir) if f.endswith(".parquet"))
    if not new_files:
        raise RuntimeError(f"影子目录为空: {new_dir}（正式目录未动）")

    # ── F-9/F-11 守卫：new 不得缺 official **非空**分区（空分区可弃置）──
    if official_state is None:
        official_state = _official_partition_state(sp)
    if verbose and official_state:
        n_rows = sum(st["rows"] for st in official_state.values() if st["rows"] > 0)
        f0, st0 = next(iter(official_state.items()))
        print(f"[快照] 切换前正式目录 {len(official_state)} 分区 / {n_rows:,} 行"
              f"｜sha256（示例 {f0}）={st0['sha256'][:12]}…"
              f"（全量已记，供 `__old` 逐位对账；B2/§10.6）")
    new_set = set(new_files)
    missing = sorted(f for f, st in official_state.items()
                     if st["rows"] != 0 and f not in new_set)
    if missing:
        raise RuntimeError(
            f"[F-9 守卫] 影子目录缺少正式目录已有分区 {missing}——"
            f"直接切换会静默删除这些分区，已中止（正式目录零改动）。"
            f"可能原因：rebuild 中断 / 某年拉取全空 / 误用 --symbols 部分标的。"
            f"若确认某分区确应废弃（如源端撤回数据），"
            f"请手动清理 official 对应 .parquet 后重跑")
    dropped_empty = sorted(f for f, st in official_state.items()
                           if st["rows"] == 0 and f not in new_set)
    if dropped_empty and verbose:
        print(f"[F-11] 弃置 official 空分区 {dropped_empty}（0 行，无数据损失）")

    os.makedirs(official, exist_ok=True)
    if os.path.isdir(old):
        shutil.rmtree(old)                    # 只保留 1 轮
    os.makedirs(old, exist_ok=True)
    moved_old = 0
    for f in sorted(os.listdir(official)):
        if f.endswith(".parquet"):
            os.replace(os.path.join(official, f), os.path.join(old, f))
            moved_old += 1
    moved_new = 0
    for f in new_files:
        os.replace(os.path.join(new_dir, f), os.path.join(official, f))
        moved_new += 1
    shutil.rmtree(new_dir, ignore_errors=True)
    if verbose:
        print(f"[切换] 旧文件 {moved_old} → {old}（保留 1 轮）；"
              f"新文件 {moved_new} → {official}")
        print(f"  回退（如需）: 将 {old} 下的 .parquet 覆盖回 {official} 后重跑本命令")
    return moved_new


def _check_rebuild_coverage(sp, universe, done_syms, verbose=True):
    """F-14：rebuild 的 **universe 级覆盖率核对**（P2 修复，2026-10-11）。

    背景：F-9/F-11 守卫只保证"**分区级**"完整（年份不丢）；"**标的级**"缺口
    （网络超时 → 整只缺失，轮次八实测 00798/01581）不被拦截 ⇒ 静默降级
    （因子缺失 ⇒ `close_qfq` 退化为 `close`）——**静默**才是真问题。

    设计（"告警 + 自愈"，而非拦截）：
      · 拦截会让 2/2,785 只超时废掉整轮 ≈4.6 h 重建，代价不成比例；
      · **水位只标 `done_syms`**（失败标的不标）⇒ 下轮轮转（≤`rotate_days` 天）
        优先重拉（04 §8.6 状态机"失败保持原水位 ⇒ 下轮优先"）；
      · 补拉路径已打通：切换后 `--symbols` 增量 upsert 进正式目录（轮次八闭环）。

    返回覆盖率 dict（随 `run()` 返回，供自动化判据解析）。
    """
    done_set = set(done_syms)
    missing = [s for s in universe if s not in done_set]
    cov = {"universe": len(universe), "done": len(done_set), "missing": missing}
    if verbose:
        if missing:
            head = ", ".join(missing[:10])
            more = "…" if len(missing) > 10 else ""
            print(f"[F-14] 覆盖率 {cov['done']:,}/{cov['universe']:,} WARN"
                  f"——缺失 {len(missing)} 只（{head}{more}）")
            print(f"  ① 已跳过其水位 ⇒ 下轮轮转（≤{sp.rotate_days} 天）优先自愈；"
                  f"② 立即补拉: python -m alt.list_backfill --table {sp.name} "
                  f"--symbols {','.join(missing[:20])}")
        else:
            print(f"[F-14] 覆盖率 {cov['done']:,}/{cov['universe']:,} OK（无缺失）")
    return cov


# ============================================================
# 六、主流程
# ============================================================
def run(table, *, symbols=None, budget=None, rebuild=False, dry_run=False,
        batch=None, yes=False, verbose=True, until=None):
    sp = spec_mod.get(table)
    if sp.mode != "list":
        raise SystemExit(f"{table}: mode={sp.mode!r} 不是 'list'（本引擎仅处理 list 表）")
    if rebuild and symbols:
        raise SystemExit(
            "--rebuild 与 --symbols 互斥（F-9 守卫）：rebuild 必须全量 universe——"
            "部分标的的影子目录必然缺分区，切换即静默丢数据")
    # §128 T2：分段停机点先解析（格式错/已过期一律立即退出码 2，不落任何东西）
    until_dt, until_err = parse_until(until)
    if until_err:
        print(f"[until] {until_err}")
        raise SystemExit(2)
    ensure_alt_dirs()
    url = guess_url(sp.func)
    if verbose:
        print(f"[配置] {sp.name} | func={sp.func} params={sp.params} | 档位={url}")

    universe = ([s.strip() for s in symbols.split(",")] if symbols
                else load_universe(sp, verbose=verbose))
    universe = [s for s in universe if s]
    if not universe:
        raise SystemExit("清单为空，无可拉标的")

    state = RotateState(dry_run=dry_run)
    # OS-R6 R6-2：调度层保护（必须在**任何请求之前**判定；dry-run 直接放行）
    _guard_schedule_state(sp, state, rebuild, dry_run, verbose=verbose)
    if rebuild:
        todo = universe
        mode_label = "整表重建（--rebuild，影子目录）"
    elif symbols:
        todo = universe
        mode_label = "指定标的（--symbols，跳过轮转选批）"
    else:
        todo = select_batch(universe, sp, state, budget=budget, verbose=verbose)
        mode_label = "轮转更新（最旧优先）"

    # 轮转进度摘要（零请求）
    fresh, stale, oldest, newest = state.progress(sp.name, universe, sp.rotate_days)
    print(f"[轮转] 进度：新鲜 {fresh:,} / 陈旧或未拉 {stale:,}（共 {len(universe):,}）"
          f"｜最旧水位 {oldest or '—'}｜最新 {newest or '—'}")
    print(f"[计划] {mode_label}：本轮 {len(todo):,} 只 => 本表请求 ≈ {len(todo):,} 条"
          f"（单表口径；sina 20/分 => 约 {len(todo) / 20:.0f} 分钟）"
          f"｜价格+因子两表合计 {len(todo) * 2:,} 条")

    if dry_run:
        print("\n[DRY-RUN] 未发请求、未写库。样例队首:", todo[:5])
        return {"table": sp.name, "planned": len(todo), "dry_run": True}

    # §130 RE3-5（阻断项修复）：交互确认不得挂死无人值守作业
    #   原实现只有 `except EOFError` 兜底 ⇒ 当 stdin 是**管道/后台**（未关闭、非 TTY）时
    #   `input()` 永久阻塞，进程卡在提示符上（R-2 是 15h 无人值守作业，会整段空转）。
    #   现在：**非 TTY 一律自动确认**（先判 isatty），TTY 下才真的问人。
    if not yes and len(todo) > 50:
        stdin_tty = bool(sys.stdin is not None and getattr(sys.stdin, "isatty", lambda: False)())
        if not stdin_tty:
            print(f"  （非 TTY，自动确认继续：{len(todo):,} 只）")
        else:
            try:
                input(f"  开始？回车继续（{len(todo):,} 只，Ctrl+C 中断，水位保护） > ")
            except EOFError:
                print("  （非交互环境，继续）")

    limiter = AltRateLimiter(dry_run=False, verbose=verbose)
    ckpt = AltCheckpoint()
    _patch_requests_timeout(SOCKET_TIMEOUT)   # akshare 的 requests 无超时会长挂起（us 实测）

    dest_dir = None
    if rebuild:
        dest_dir = alt_table_dir(sp.name) + "__new"
        if os.path.isdir(dest_dir):
            shutil.rmtree(dest_dir)
        os.makedirs(dest_dir, exist_ok=True)

    stat = {"ok": 0, "empty": 0, "fail": 0, "rows": 0, "added": 0}
    errs = []
    done_syms = []              # F-15：**落盘成功批次**收编（成功+返空；rebuild 水位/覆盖率只认这些）
    buf = []                    # [(symbol, df), ...] 待落盘
    batch_syms = []             # 本批已处理（ok + empty），flush 成功后统一记水位
    t0 = time.time()
    batch_size = batch or HK_BATCH_SIZE

    # §128 T1-1：进度心跳（dry-run 已在上面 return，故 dry-run 绝不写 meta —— 门槛 f）
    phase = "build" if (rebuild or (budget is not None and budget >= len(universe))) else "rotate"
    hb = ProgressHeartbeat(sp.name, phase, len(universe), until=until)
    hb.write(done=0)
    persisted = 0                 # 本次运行**落盘成功**的只数（心跳 done 的来源）
    last_batch_n = 0
    stopped_by_until = False

    def _flush():
        nonlocal buf, batch_syms, done_syms, persisted, last_batch_n
        if not buf and not batch_syms:
            return
        n_batch = len(batch_syms)
        r = flush_buffer(sp, buf, dest_dir=dest_dir)
        if r["ok"]:
            stat["added"] += r["added"]
            if buf:
                print(f"    [落盘] {len(buf)} 只 → 新增 {r['added']:,} 行"
                      f"（累计 {stat['added']:,}）")
            if not rebuild:
                # 先落盘、后记（INV-3）：水位与一次性断点在此统一推进（含 empty）
                state.mark_done(sp.name, batch_syms)
                ckpt.mark_done(sp.name, batch_syms)
            else:
                # F-15 修复（修法 1 · 按批入列）：rebuild 的水位/覆盖率同样只在
                # **落盘成功后**收编该批——flush 失败批不进入 done_syms
                # （此前在取数成功处即时入列 ⇒ 落盘失败的批也被记"完成"）。
                done_syms.extend(batch_syms)
            # 心跳**只在 flush 成功后**更新（失败保持旧 updated_at，看门狗才识别得出卡死）
            persisted += n_batch
            last_batch_n = n_batch
            hb.write(done=persisted, last_batch=n_batch,
                     batch_ok=stat["ok"], batch_fail=stat["fail"])
        else:
            errs.append(f"flush: {r['errors'][:1]}")
            print(f"    [落盘失败] 不记水位（下次重拉）: {str(r['errors'][:1])[:120]}")
        buf = []
        batch_syms = []

    for i, sym in enumerate(todo, 1):
        # §128 T2：到点**不再发起新批次**（在标的边界停，绝不截断在途请求）
        if until_dt is not None and datetime.now() >= until_dt:
            stopped_by_until = True
            print(f"\n[until] 到达 {until}，停止发起新批次"
                  f"（已处理 {len(batch_syms)} 只待落盘）")
            break
        s, d, err = fetch_one(sp, sym, limiter, url)
        batch_syms.append(s)
        if err == "empty":
            stat["empty"] += 1            # F-15：随批入列（_flush ok 后统一收编）
        elif err:
            stat["fail"] += 1
            errs.append(f"{s}: {err}")
            batch_syms.remove(s)          # 失败不记水位
        else:
            stat["ok"] += 1
            stat["rows"] += len(d)
            buf.append((s, d))            # F-15：随批入列（非即时 append）

        if _should_flush(buf, batch_syms, batch_size):
            _flush()
        if i % 200 == 0 or i == len(todo):
            el = time.time() - t0
            rate = i / max(el, 1)
            print(f"    {i:,}/{len(todo):,} | 成功 {stat['ok']:,} 空 {stat['empty']} "
                  f"失败 {stat['fail']} | {rate:.2f} 只/s | "
                  f"剩余≈{(len(todo) - i) / max(rate, 0.01) / 60:.0f} 分钟")
    _flush()

    el = time.time() - t0
    # §128 T2 4.2：退出前必须写最后一次心跳（updated_at 即退出时刻）
    _fin = datetime.now().astimezone().isoformat(timespec="milliseconds")
    if stopped_by_until:
        hb.write(done=persisted, last_batch=last_batch_n,
                 batch_ok=stat["ok"], batch_fail=stat["fail"],
                 extra={"until_stop": True, "finished_at": _fin})
        print(f"[until] 到达 {until}，已 flush {persisted} 只后退出")
    else:
        hb.write(done=persisted, last_batch=last_batch_n,
                 batch_ok=stat["ok"], batch_fail=stat["fail"],
                 extra={"finished_at": _fin})
    print("\n" + "=" * 84)
    print(f"[完成] {sp.name} | {mode_label}")
    print(f"  成功 {stat['ok']:,} | 返空 {stat['empty']} | 失败 {stat['fail']} | "
          f"拉取 {stat['rows']:,} 行 | 落盘新增 {stat['added']:,} 行 | 耗时 {el / 60:.1f} 分钟")
    print(f"  {limiter.status_str()}")
    if errs:
        print(f"  失败样例（前 5）: {errs[:5]}")

    cov = None
    if rebuild:
        official_state = _official_partition_state(sp)  # F-9/F-11：切换前快照（含行数）
        cov = _check_rebuild_coverage(sp, universe, done_syms, verbose=verbose)  # F-14
        counts = _verify_shadow(sp, dest_dir, verbose=verbose)
        total = sum(counts.values())
        moved = _switch_shadow(sp, dest_dir, official_state=official_state,
                               verbose=verbose)
        # 切换成功后刷新水位与断点：**只标 done_syms**（F-14：失败标的不标 ⇒
        # 下轮轮转优先重拉自愈；此前标全量 universe 会把失败也记成"已完成"）
        state.mark_done(sp.name, done_syms)
        ckpt.mark_done(sp.name, done_syms)
        print(f"[rebuild] done: {moved} 分区 / {total:,} 行 → 正式目录；"
              f"水位已刷新（{len(set(done_syms)):,}/{len(universe):,} 只）")
    else:
        fresh, stale, oldest, newest = state.progress(sp.name, universe, sp.rotate_days)
        print(f"  [轮转] 进度更新：新鲜 {fresh:,} / 陈旧或未拉 {stale:,}"
              f"｜最旧 {oldest or '—'}｜最新 {newest or '—'}")
    return {"table": sp.name, "stat": stat, "errors": errs,
            "coverage": cov, "dry_run": False}


# ============================================================
# 七、状态（零请求）
# ============================================================
def _ckpt_done_counts():
    """R6-1：读**一次性断点** `AltCheckpoint` 的 done 条数（仅显示层，不参与调度）。

    为什么读它：`us_daily_qfq` 盘上 3,040 万行、ckpt done=18,052，但 RotateState
    无此表 ⇒ 只看 RotateState 的 `--status` 会显示"新鲜 0 / 陈旧 18,052"，是假象。
    """
    try:
        ck = AltCheckpoint()
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for t, rec in (ck.data or {}).items():
        if not isinstance(rec, dict):
            continue
        done = rec.get("done") or []
        if isinstance(done, (list, tuple, set)):
            out[t] = len(done)
    return out


def show_status(table=None, verbose=True, json_out=False):
    """轮转进度总览（零请求）。

    §128 T1-3：`--status --json` 必须真的输出**机读 JSON**（此前 `--json` 在
    `--status` 分支之前就 return 了，参数被静默忽略）。
    OS-R6 R6-1：合并显示 `AltCheckpoint`（一次性断点）的 done 条数——
      RotateState 有记录的表**输出逐字不变**（R6-4），仅对"RotateState 无记录
      但一次性断点有 done"的表追加一行说明，消除"done=0"假象。
    """
    tables = ([table] if table else
              [s.name for s in spec_mod.ALL_SPECS if s.mode == "list"])
    state = RotateState()
    ck_done = _ckpt_done_counts()

    if json_out:
        out = {}
        for t in tables:
            sp = spec_mod.get(t)
            item = {"total": None, "fresh": None, "stale": None,
                    "oldest": None, "newest": None}
            if not (sp.universe_from or ""):
                item["error"] = "no universe_from"
            else:
                try:
                    uni = load_universe(sp, verbose=False)
                except Exception as e:  # noqa: BLE001
                    item["error"] = str(e)[:120]
                else:
                    fresh, stale, oldest, newest = state.progress(
                        t, uni, sp.rotate_days)
                    item.update({"total": len(uni), "fresh": fresh,
                                 "stale": stale,
                                 "oldest": oldest or None,
                                 "newest": newest or None})
            item["rotate_done"] = len(state.data.get(t, {}) or {})
            item["checkpoint_done"] = ck_done.get(t, 0)
            out[t] = item
        print(json.dumps(out, ensure_ascii=False))
        return 0

    print(f"{'表名':<18}{'标的':>8}{'新鲜':>8}{'陈旧/未拉':>10}"
          f"{'最旧水位':>14}{'最新水位':>14}")
    print("-" * 80)
    for t in tables:
        sp = spec_mod.get(t)
        if not (sp.universe_from or ""):
            print(f"{t:<18}  （无 universe_from，须 --symbols 显式指定）")
        else:
            try:
                uni = load_universe(sp, verbose=False)
            except Exception as e:  # noqa: BLE001
                print(f"{t:<18}  清单读取失败: {str(e)[:50]}")
            else:
                fresh, stale, oldest, newest = state.progress(
                    t, uni, sp.rotate_days)
                print(f"{t:<18}{len(uni):>8,}{fresh:>8,}{stale:>10,}"
                      f"{oldest or '—':>14}{newest or '—':>14}")
        # R6-1：RotateState 无记录但一次性断点有 done ⇒ 追加真实进度（不覆盖原行）
        if not state.data.get(t) and ck_done.get(t, 0) > 0:
            print(f"{'':<18}  ↳ AltCheckpoint done={ck_done[t]:,}"
                  f"（RotateState 无记录；两套记录互不可见，§140 R6-1）")
    return 0


# ============================================================
# 八、CLI
# ============================================================
def main(argv=None):
    ap = argparse.ArgumentParser(
        description="alt 库 list 模式引擎（建库 / 轮转更新 / 整表重建）")
    ap.add_argument("--table", help="表名（spec 中 mode='list' 的表）")
    ap.add_argument("--symbols", nargs="+",
                    help='指定标的（调试用，跳过轮转选批）。'
                         '⚠ PowerShell 下必须加引号保全前导零：--symbols "00700,00981"')
    ap.add_argument("--budget-syms", type=int, default=None, dest="budget",
                    help="本轮最多拉 N 只（覆盖 ceil(n/rotate_days)）")
    ap.add_argument("--batch", type=int, default=None,
                    help=f"flush 批大小（只），默认 {HK_BATCH_SIZE}")
    ap.add_argument("--rebuild", action="store_true",
                    help="整表重建（影子目录 __new → 校验 → 文件级原子替换）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不发请求")
    ap.add_argument("--status", action="store_true", help="轮转进度总览（零请求）")
    ap.add_argument("--json", action="store_true", dest="json_out",
                    help="rebuild 完成时在 stdout 末尾输出版本化覆盖率 JSON（B1/O-1）")
    ap.add_argument("--strict-coverage", action="store_true",
                    help="rebuild 覆盖率缺失（missing>0）时退出码 3；默认关（告警不阻断）")
    ap.add_argument("--yes", action="store_true", help="跳过确认")
    ap.add_argument("--until", default=None,
                    help="分段停机点 HH:MM（到点不再发起新批次，在途批次完成并落盘后退出 0）")
    args = ap.parse_args(argv)

    if args.status:
        if args.json_out:
            # §128 T1-3：机读 JSON（**不**打表头，保证 stdout 可被 json.loads 解析）
            return show_status(args.table, json_out=True)
        print("=" * 84)
        print("alt list 表轮转进度")
        print("=" * 84)
        return show_status(args.table)
    if not args.table:
        print("需 --table（或不带参数用 --status 看总览）")
        return 2
    sym_arg = ",".join(args.symbols) if args.symbols else None
    res = run(args.table, symbols=sym_arg, budget=args.budget,
              rebuild=args.rebuild, dry_run=args.dry_run, batch=args.batch,
              yes=args.yes, until=args.until)
    # ── B1（O-1）：机读覆盖率（版本化 JSON，仅 rebuild 且有覆盖数据时输出）──
    cov = (res or {}).get("coverage")
    if args.json_out and cov is not None:
        payload = {"schema": "alt-list-rebuild-coverage/1",
                   "table": res.get("table"),
                   "universe": cov["universe"],
                   "done": cov["done"],
                   "missing": cov["missing"]}
        print(json.dumps(payload, ensure_ascii=False))
    # 退出码策略：默认"告警不阻断"（0）；--strict-coverage 时 missing>0 ⇒ 3
    if args.strict_coverage and cov and cov["missing"]:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
