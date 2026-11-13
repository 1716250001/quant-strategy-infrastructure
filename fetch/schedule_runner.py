# -*- coding: utf-8 -*-
"""fetch/schedule_runner.py — 周期调度执行器（CC-FIX-13 / R3'-b，2026-11-10）

按 `config.schedule.SCHEDULE` 的频率筛表 → **逐表复用** `fetch.backfill.run_target`
（取数参数全部来自 `BACKFILL_TARGETS`，本文件不复制任何配置）。

语义（派单 §三／§六）
1. **互斥**：目标表若在当日日更组内 → 跳过并记日志；另用文件锁（`.schedule.lock`）拒绝并发。
2. **断点语义**（CC-FIX-7 同源）：成功 → `last_fetch`；失败 → 只写 `last_attempt`。
3. **到期判定（双条件，CC-FIX-19）**：有 `RELEASE_DAY` 的表按「已过本月发布日 ∧ 上次
   拉取早于该锚点」判；其余表沿用 `last_fetch` 距今 ≥ `FREQ_DAYS[freq]`（无记录＝到期）。
4. **dry-run（默认）零请求**（连 `get_pro()` 都不调）；请求量按断点过滤后的待跑任务计，
   `--refresh` 时按全量任务计；峰值按 `API_RATE_LIMIT` 报出。
5. **护栏**（CC-FIX-16）：裸 `--refresh` 拒绝（全档 84,325 次）；`--budget N`（默认 2,000）
   超限即停并 RC=2、打印跳过清单；估算异常置 `est_error`，不伪装 0／`ok`。
6. **known_gaps**：只读对账；改写须显式走 `tools/db/meta_close.py --apply` 并单独批准。
7. 红线：不改 `subset`、不碰 audit、不写任何取数参数。
用法：python -m fetch.schedule_runner --dry-run｜--freq M --dry-run｜--freq W --apply
"""
import argparse
import json
import os
import sys
from calendar import monthrange
from datetime import date, datetime

from config import MARKET_DATA_DIR
from config.backfill_targets import BACKFILL_TARGETS
from config.daily import DAILY_UPDATE_EXTRA, DAILY_UPDATE_GROUPS, PERIODIC_UPDATE_TARGETS
from config.schedule import FREQ_DAYS, RELEASE_DAY, SCHEDULE
from fetch.backfill import API_RATE_LIMIT, API_RATE_LIMIT_DEFAULT, _compute_pending, run_target
from fetch.backfill_spec import build_tasks, estimate_requests
from fetch.base import Checkpoint, get_pro

LOCK_PATH = os.path.join(MARKET_DATA_DIR, ".schedule.lock")
CKPT_NAME = ".backfill_checkpoint.json"
KG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "tools", "known_gaps.json")
FREQS = ("D", "W", "M", "Q")
DEFAULT_BUDGET = 2000
REFRESH_COST = "D 5,994／M 9,320／Q 35,634／W 33,377（合计 84,325）"


class _InstanceLock:
    """文件锁：O_EXCL 创建；异常退出留残锁，下次启动提示人工确认。"""

    def __init__(self, path=LOCK_PATH):
        self.path = path

    def __enter__(self):
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise SystemExit(f"[schedule] 已有实例在跑（锁文件 {self.path}）；"
                             f"确认无进程后可手动删除该文件再试") from exc
        os.write(fd, f"{os.getpid()} {datetime.now().isoformat(timespec='seconds')}".encode())
        os.close(fd)
        return self

    def __exit__(self, *exc):
        try:
            os.remove(self.path)
        except OSError:
            pass
        return False

def daily_names():
    names = set(DAILY_UPDATE_EXTRA) | set(PERIODIC_UPDATE_TARGETS)
    for group in DAILY_UPDATE_GROUPS.values():
        names |= set(group)
    return names

def _last_fetch(cp, name):
    rec = cp.data.get(name) if isinstance(cp.data, dict) else None
    return rec.get("last_fetch") if isinstance(rec, dict) else None

def _last_dom(year, month):
    """当月最后一天（发布日超出月末时夹紧，如 2 月 30 日 → 28/29）。"""
    return monthrange(year, month)[1]

def is_due(freq, last_fetch, today=None, name=None):
    """到期判定（CC-FIX-19 双条件）。

    无拉取记录＝到期（行为不变）；有 `RELEASE_DAY` 的表按日历锚点判，
    「已过本月发布日 ∧ 上次拉取早于该锚点」才到期；其余表沿用等间隔。
    """
    t = today or date.today()
    if not last_fetch:
        return True
    try:
        lf = datetime.fromisoformat(str(last_fetch)).date()
    except Exception as exc:
        print(f"[fetch.schedule_runner] 时间戳不可解析 {last_fetch!r}"
              f"（按到期处理）: {type(exc).__name__}: {exc}", file=sys.stderr)
        # 时间戳不可解析按到期（宁可多拉一次）
        return True
    if name in RELEASE_DAY:
        day, months = RELEASE_DAY[name]
        if months and t.month not in months:
            return False                      # 非发布月，不到期
        anchor = date(t.year, t.month, min(day, _last_dom(t.year, t.month)))
        return t >= anchor and lf < anchor    # 已过本月发布日且上次早于锚点
    return (t - lf).days >= FREQ_DAYS[freq]

def _est_requests(name, conf, cp, refresh=False):
    """本次请求量＝待跑任务数（refresh 时按全量任务计；只读）。

    ⚠ 位置参顺序须与 `_compute_pending(name, conf, tasks, kind, cp)` 一致；
    异常**不得**在此吞掉（CC-FIX-16 §0.1），由 `select` 置 `est_error`。
    """
    tasks, kind = build_tasks(name, conf, dry_run=True)
    if not tasks:
        return 0
    if refresh:
        return int(estimate_requests(tasks, conf))
    pending, _ = _compute_pending(name, conf, tasks, kind, cp, dry_run=True)
    return int(estimate_requests(pending, conf)) if pending else 0

def select(freqs=None, only=None, cp=None, refresh=False, exclude=None):
    """[(table, freq, due, est, status)]；status ∈ ok/skip_daily/skip_fresh/
    skip_excluded/est_error。"""
    freqs = set(freqs or FREQS)
    only = set(only) if only else None
    exclude = set(exclude or ())
    dn = daily_names()
    out = []
    for name, freq in SCHEDULE.items():
        if freq not in freqs or (only and name not in only):
            continue
        if name in dn:                 # 互斥优先于配置存在性
            out.append((name, freq, False, 0, "skip_daily"))
            continue
        conf = BACKFILL_TARGETS.get(name)
        if conf is None:
            continue
        if name in exclude:            # R10 备份窗口互斥（§三.5，预留）
            out.append((name, freq, False, 0, "skip_excluded"))
            continue
        due = is_due(freq, _last_fetch(cp, name) if cp else None, name=name)
        if cp is None:
            out.append((name, freq, due, 0, "ok" if due else "skip_fresh"))
            continue
        try:
            est = _est_requests(name, conf, cp, refresh)
        except Exception as exc:
            # 不得伪装成 0/ok（§0.1）
            print(f"⚠ 估算失败: {name}: {type(exc).__name__}: {exc}")
            out.append((name, freq, due, 0, "est_error"))
            continue
        out.append((name, freq, due, est, "ok" if due else "skip_fresh"))
    return out

def _rate(name):
    return API_RATE_LIMIT.get((BACKFILL_TARGETS.get(name) or {}).get("api"),
                              API_RATE_LIMIT_DEFAULT)

def _budget_split(rows, budget):
    """按 est 累加切分本轮：超预算的进 over（**不静默截断**，CC-FIX-16 §一.2）。"""
    todo, acc, over = [], 0, []
    for r in rows:
        if r[4] != "ok":
            continue
        if acc + r[3] > budget:
            over.append(r)
            continue
        acc += r[3]
        todo.append(r)
    return todo, over

def _mark_attempt(cp, name):
    rec = cp.data.setdefault(name, {"completed": []})
    rec.setdefault("completed", [])
    rec["last_attempt"] = datetime.now().isoformat(timespec="seconds")
    cp.save()

def _kg_report(tables):
    """known_gaps **只读**对账；改写须显式走 `tools/db/meta_close.py --apply`
    并单独批准（CC-FIX-16 §二），本函数不得有任何写路径。"""
    if not os.path.isfile(KG_PATH):
        return
    kg = json.load(open(KG_PATH, encoding="utf-8"))
    for t in [t for t in tables if t in kg]:
        print(f"  [known_gaps] {t}: A={kg[t].get('A')} B={kg[t].get('B')}"
              f"（只读；改写走 tools/db/meta_close.py --apply）")

def run(freqs=None, only=None, dry_run=True, workers=1, limit=None, refresh=False,
        exclude=None, budget=DEFAULT_BUDGET):
    if refresh and not freqs and not only:
        print(f"⛔ 裸 --refresh 已拒绝：全档成本 {REFRESH_COST} 次请求")
        print("   必须与 --freq <单档> 或 --only <表清单> 同用（--budget 默认 2,000）")
        return 1
    cp = Checkpoint(MARKET_DATA_DIR, CKPT_NAME)      # 只读：仅 select 估待跑用
    rows = select(freqs, only, cp, refresh, exclude)
    todo, over = _budget_split(rows, budget)
    est, peak = sum(r[3] for r in todo), max([_rate(r[0]) for r in todo], default=0)

    print(f"{'表':22s} {'档':3s} {'到期':4s} {'请求≈':>5s} {'限频/m':>7s} 状态")
    print("-" * 64)
    for name, freq, due, e, status in rows:
        print(f"{name:22s} {freq:3s} {'是' if due else '-':4s} {e:5d} {_rate(name):7.2f} {status}")
    print("-" * 64)
    print(f"在册 {len(rows)} 张｜本次待跑 {len(todo)} 张｜预计请求≈{est}｜峰值 {peak}/min"
          f"｜预算 {budget}")
    if peak > 190:
        print(f"⚠ 峰值 {peak}/min 超过判据 2 上限 190/m")
    errs = [r[0] for r in rows if r[4] == "est_error"]
    if errs:
        print(f"⚠ 估算失败 {len(errs)} 张（未纳入本轮）：{', '.join(errs)}")
    if over:
        print(f"⚠ 超预算：本轮跳过 {len(over)} 张 → {', '.join(r[0] for r in over)}（RC=2）")

    if dry_run:
        print("[dry-run] 未发请求、未读写任何数据（仅解析代码清单）")
        return 2 if over else 0
    if not todo:
        print("[schedule] 无到期表，退出")
        return 2 if over else 0

    pro, failed = get_pro(), []
    for name, _freq, _due, _est, _st in todo:
        try:
            res = run_target(pro, name, BACKFILL_TARGETS[name], cp, workers=workers,
                             dry_run=False, verbose=False, limit=limit,
                             ignore_checkpoint=refresh)
            if res is None:
                raise RuntimeError("run_target 返回 None（前置/配置不可用）")
            cp.mark_fetched(name)          # 成功：补 last_fetch（run_target 内多已写）
            cp.save()
            print(f"[ok] {name} 完成={res.get('done')}")
        except Exception as exc:
            # 单表失败不得拖垮整轮
            _mark_attempt(cp, name)
            failed.append(name)
            print(f"[fail] {name}: {type(exc).__name__}: {exc}")
    _kg_report([r[0] for r in todo])
    # R10-2（§446）：日备随调度自动跑。仅「真跑」且无失败时才备——
    # 干跑不备（无用）、有失败不备（备的是半截库）。mirror() 自带
    # .backup.lock（O_CREAT|O_EXCL）与「今日快照已存在不覆盖」双保护，
    # 重复调用安全，故此处不需要额外幂等判断。
    if not dry_run and not failed:
        try:
            from tools.data_backup import mirror
            _rc = mirror()
            print(f"[schedule] 日备 rc={_rc}")
        except Exception as exc:
            # 备份失败不改变取数退出码（否则计划任务 LastTaskResult 会一直非 0）
            print(f"[schedule] 日备异常：{type(exc).__name__}: {exc}")
    print(f"[schedule] 完成 {len(todo) - len(failed)}/{len(todo)}，失败 {len(failed)}")
    if over:
        return 2
    return 1 if failed else 0

def main(argv=None):
    p = argparse.ArgumentParser(prog="schedule_runner",
                                description="周期调度执行器（默认 dry-run）")
    p.add_argument("--freq", default=None, help="频率档，逗号分隔：D,W,M,Q（默认全档）")
    p.add_argument("--only", default=None, help="只跑指定表，逗号分隔")
    p.add_argument("--workers", type=int, default=1, help="每接口并发（默认 1）")
    p.add_argument("--limit", type=int, default=None, help="每表最多跑 N 个任务")
    p.add_argument("--budget", type=int, default=DEFAULT_BUDGET,
                   help=f"单轮请求预算（默认 {DEFAULT_BUDGET}；超限 RC=2 并列跳过清单）")
    p.add_argument("--apply", action="store_true", help="实际执行（不加则等同 --dry-run）")
    p.add_argument("--dry-run", action="store_true", help="只输出计划，不发请求（默认行为）")
    p.add_argument("--refresh", action="store_true",
                   help="忽略断点重拉（by_code 表增量只在 refresh 下才拉得到；"
                        "等价单表 --force，灰度前须赤潮明示；裸跑直接拒绝）")
    p.add_argument("--exclude", default=None, help="R10 备份窗口互斥：跳过这些表（逗号分隔，预留）")
    a = p.parse_args(argv)
    freqs = [x.strip().upper() for x in a.freq.split(",")] if a.freq else None
    only = [x.strip() for x in a.only.split(",")] if a.only else None
    excl = [x.strip() for x in a.exclude.split(",")] if a.exclude else None
    with _InstanceLock():
        return run(freqs, only, dry_run=not a.apply, workers=a.workers,
                   limit=a.limit, refresh=a.refresh, exclude=excl, budget=a.budget)

if __name__ == "__main__":
    sys.exit(main())
