# -*- coding: utf-8 -*-
"""
wind_gate.py — Wind 唯一调用入口（WIND-LIB W-3）
===================================================
职责：
  1. 所有 Wind 调用**必须**过闸门，无旁路
  2. 先存后解析（红线 4）：响应先落 raw/{yyyymmdd}/{qid}__{seq}.json 再解析
  3. 记账：每次调用写 budget_ledger.jsonl
  4. 缓存命中：params_hash 命中 + 未过期 → 直接返回缓存，不发请求
  5. qid 归一化：topic + 槽位模板化（禁原始字符串哈希）
  6. 快照表不可覆盖写（红线 3）

注：本 W-3 为轻量骨架，完整调用链需接 Wind API 密钥（已在 config_wind）。
"""
import datetime as _dt
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.parquet_store import _atomic_write_parquet
from wind.config import (
    FORBIDDEN_WRITE_ROOTS,
    WIND_BUDGET_FILE,
    WIND_CALL_INTERVAL,
    WIND_META_DIR,
    WIND_RAW_DIR,
    WIND_TMP_DIR,
    WindPathViolationError,
    assert_writable,
    violation_count,
)


class WindGateError(RuntimeError):
    """闸门级错误"""


# ── 内部状态 ───────────────────────────────────────────────────
_LAST_CALL_TS = 0
_CACHE = {}   # params_hash → {"data": ..., "expire_ts": ...}


def _qid(topic, slot, **kwargs):
    """qid 归一化：topic + slot + kwargs 模板化。

    派单 §70.7 禁原始字符串哈希（换措辞即重问，30% 空返回）。
    固定模板：topic[:20]_slot[:20]_paramkeys_hash8
    """
    pk = sorted(kwargs.keys())
    h = hashlib.sha256(("|".join(f"{k}={kwargs[k]}" for k in pk)).encode()).hexdigest()[:8]
    q = f"{topic[:20]}_{slot[:20]}_{h}"
    return q.replace(" ", "_").replace("/", "_")


def _params_hash(params):
    """稳定 params hash（缓存键）"""
    s = json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha256(s.encode()).hexdigest()[:24]


def _budget_ledger_entry(qid, endpoint, tool, params_hash, rows, valid,
                         cost_point, raw_path, elapsed, status):
    """记账行"""
    return {
        "ts": _dt.datetime.now().isoformat(timespec="seconds"),
        "qid": qid,
        "endpoint": endpoint,
        "tool": tool,
        "params_hash": params_hash,
        "rows": rows,
        "valid": valid,
        "cost_point": cost_point,
        "raw_path": raw_path,
        "elapsed_ms": int(elapsed * 1000),
        "status": status,
    }


def _append_ledger(entry):
    """追加到 budget_ledger.jsonl"""
    os.makedirs(WIND_META_DIR, exist_ok=True)
    with open(WIND_BUDGET_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _cooldown():
    """限流：距上次调用 ≥ WIND_CALL_INTERVAL 秒"""
    global _LAST_CALL_TS
    now = time.time()
    elapsed = now - _LAST_CALL_TS
    if elapsed < WIND_CALL_INTERVAL:
        time.sleep(WIND_CALL_INTERVAL - elapsed)
    _LAST_CALL_TS = time.time()


def call(topic, slot, params=None, cache_ttl=3600):
    """唯一入口：所有 Wind 调用必须过这里。

    参数：
        topic: str    — 业务主题（如 "wind_index_daily"）
        slot:  str    — 槽位/操作（如 "get_index_kline"）
        params: dict  — 调用参数（决定缓存键）
        cache_ttl: int — 缓存过期秒数，0=不缓存

    返回：dict   解析后的数据（或缓存命中的数据）
    """
    global _LAST_CALL_TS

    params = params or {}
    qid = _qid(topic, slot, **params)
    phash = _params_hash(params)
    today = _dt.datetime.now().strftime("%Y%m%d")
    raw_subdir = os.path.join(WIND_RAW_DIR, today)
    os.makedirs(raw_subdir, exist_ok=True)

    # 缓存命中
    if cache_ttl > 0 and phash in _CACHE:
        cached = _CACHE[phash]
        if cached["expire_ts"] > time.time():
            entry = _budget_ledger_entry(
                qid=qid, endpoint="", tool="cache_hit", params_hash=phash,
                rows=cached.get("rows", 0), valid=True, cost_point=0,
                raw_path="", elapsed=0, status="cache_hit"
            )
            _append_ledger(entry)
            print(f"[gate] cache_hit qid={qid}")
            return cached["data"]
        else:
            del _CACHE[phash]

    # 先存后解析（红线 4）
    raw_path = os.path.join(raw_subdir, f"{qid}__{phash[:8]}.json")
    assert_writable(raw_path)   # 硬保护

    start = time.time()
    _cooldown()

    # ── 实际调用（占位：接入 Wind API 时填充）─────────────
    # 这里仅存骨架，等真实 Wind 客户端接入后替换
    response_data = {
        "mock": True,
        "topic": topic,
        "slot": slot,
        "qid": qid,
        "params": params,
        "fetched_at": _dt.datetime.now().isoformat(timespec="seconds"),
    }
    status = "ok"
    valid = True
    rows = 0
    cost_point = 0

    # 先落盘
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(response_data, f, ensure_ascii=False, indent=2)

    # 解析（解析失败不影响原始留存）
    elapsed = time.time() - start
    data = response_data   # 简化：直接返回原始
    if cache_ttl > 0:
        _CACHE[phash] = {"data": data, "rows": rows, "expire_ts": time.time() + cache_ttl}

    entry = _budget_ledger_entry(
        qid=qid, endpoint="", tool=slot, params_hash=phash,
        rows=rows, valid=valid, cost_point=cost_point,
        raw_path=raw_path, elapsed=elapsed, status=status
    )
    _append_ledger(entry)
    print(f"[gate] {status} qid={qid} rows={rows} cost={cost_point} raw={raw_path}")

    return data


def write_snap(topic, as_of, data, force=False):
    """写快照（norm/snap/{topic}/{as_of}.parquet）—— 不可覆盖。"""
    import pandas as pd
    snap_root = os.path.join(
        os.path.dirname(WIND_RAW_DIR), "norm", "snap", topic
    )
    os.makedirs(snap_root, exist_ok=True)
    snap_path = os.path.join(snap_root, f"{as_of}.parquet")

    if os.path.exists(snap_path) and not force:
        raise WindGateError(
            f"快照已存在，禁止覆盖（红线 3）\n"
            f"  目标: {snap_path}\n"
            f"  如需覆盖请显式 force=True 并留意留痕"
        )
    assert_writable(snap_path)
    df = pd.DataFrame(data)
    # CC-WIND-1（2026-11-03）：改走原子写（tmp → fsync → os.replace → 目录 fsync）。
    # 原来直写最终路径，写一半崩溃＝快照损坏且旧副本已丢；红线 3 的「不可覆盖」
    # 让重跑也救不回来（目标文件已存在 ⇒ 直接拒绝）。
    _atomic_write_parquet(df, snap_path)
    print(f"[snap] 写入 {topic}/{as_of} ({len(df)} 行)")
    return snap_path


def _selfcheck_probe_path():
    """自检用的越界探针路径（仅在自检内引用，生产逻辑不调用）。

    S2 负控通过替换本函数返回「合法路径」来验证断言真的会失败。
    """
    return os.path.join(
        FORBIDDEN_WRITE_ROOTS[0], "selfcheck_never_written.parquet"
    )


def _sc_file_snapshot(root):
    """目录快照（path → mtime_ns），用于断言「未被改动」。"""
    snap = {}
    if not os.path.isdir(root):
        return snap
    for cur, _dirs, files in os.walk(root):
        for fn in files:
            p = os.path.join(cur, fn)
            snap[p] = os.stat(p).st_mtime_ns
    return snap


def _selfcheck_cache(failures):
    """[1] 二次同参调用 → 命中缓存：r1==r2，且 raw 目录零新增/零改写。"""
    print("\n[1] 缓存命中测试:")
    os.makedirs(WIND_RAW_DIR, exist_ok=True)
    # 带 nonce：保证本次首调必为冷路径（不受跨次运行残留缓存影响）
    params = {"a": 1, "b": 2, "run": _dt.datetime.now().isoformat(timespec="microseconds")}
    snap0 = _sc_file_snapshot(WIND_RAW_DIR)
    r1 = call("test", "probe", params, cache_ttl=3600)
    snap1 = _sc_file_snapshot(WIND_RAW_DIR)
    r2 = call("test", "probe", dict(params), cache_ttl=3600)
    snap2 = _sc_file_snapshot(WIND_RAW_DIR)

    if r1 != r2:
        failures.append("[1] 两次同参调用返回值不一致（缓存未命中或返回被改写）")
    if len(snap1) - len(snap0) != 1:
        failures.append(f"[1] 首次调用应新增 1 个 raw 文件，实际新增 {len(snap1) - len(snap0)} 个")
    if snap1 != snap2:
        failures.append(
            f"[1] 第二次调用改动了 raw 目录：{len(snap1)} → {len(snap2)} 个文件（或 mtime 变化）"
        )
    print(f"  r1==r2: {r1 == r2}；raw 新增 {len(snap1) - len(snap0)} 个；"
          f"二次调用零改动: {snap1 == snap2}")


def _selfcheck_snap(failures):
    """[2] 快照覆盖必须抛 WindGateError（未抛即记 failure）。"""
    print("\n[2] 快照覆盖拒绝:")
    as_of = "20261022"
    snap_dir = os.path.join(os.path.dirname(WIND_RAW_DIR), "norm", "snap", "_gate_test")
    os.makedirs(snap_dir, exist_ok=True)
    try:
        write_snap("_gate_test", as_of, {"x": [1, 2]})
    except Exception as e:
        failures.append(f"[2] 首写快照意外失败：{type(e).__name__}: {e}")
        return
    try:
        write_snap("_gate_test", as_of, {"x": [3, 4]}, force=False)
    except WindGateError:
        print("  ✅ 覆盖被 WindGateError 拒绝")
    else:
        failures.append("[2] 快照覆盖未被拒绝（应抛 WindGateError）")
    finally:
        p = os.path.join(snap_dir, f"{as_of}.parquet")
        if os.path.exists(p):
            os.remove(p)
        for d in (snap_dir, os.path.dirname(snap_dir), os.path.dirname(os.path.dirname(snap_dir))):
            try:
                os.rmdir(d)
            except OSError:
                pass


def _selfcheck_violation(failures):
    """[3] 真正走禁区路径：必须抛 WindPathViolationError 且 violation_count 恰好 +1。"""
    print("\n[3] assert_writable 越界:")
    path = _selfcheck_probe_path()
    before = violation_count()
    try:
        assert_writable(path)
    except WindPathViolationError:
        print(f"  ✅ 越界被 WindPathViolationError 拒绝：{path}")
    except Exception as e:
        failures.append(f"[3] 越界抛错类型不符：{type(e).__name__}: {e}")
    else:
        failures.append(f"[3] 越界路径未被拦截（仍返回成功）：{path}")
    after = violation_count()
    if after != before + 1:
        failures.append(f"[3] violation_count 应 {before} → {before + 1}，实际 {after}")
    if os.path.exists(path):
        failures.append(f"[3] 越界探针路径竟存在文件：{path}")
    print(f"  violation_count: {before} → {after}（预期 +1）")


def selfcheck():
    """W-3 自验：三项硬断言 → 返回 failures（空列表=全过）。

    失败转退出码 1 的责任在 `__main__`，本函数始终返回 `list[str]`
    （便于 regress 等调用方复用，而不必捕获 SystemExit）。

    约束（CC-SELFCHECK-1 / §277）：
      * 全程零写入生产库：call/write_snap 的目录在 WIND_TMP_DIR 沙箱内临时重定向；
      * 不改变 call / write_snap / assert_writable 的签名与行为；
      * 0 次 Wind API 调用（call 仍为 mock 骨架，无网络）。
    """
    global WIND_RAW_DIR, WIND_META_DIR, WIND_BUDGET_FILE
    print("=== wind_gate 自验 ===")
    failures = []

    prod_raw = WIND_RAW_DIR
    prod_before = _sc_file_snapshot(prod_raw)

    saved = (WIND_RAW_DIR, WIND_META_DIR, WIND_BUDGET_FILE)
    sandbox = os.path.join(WIND_TMP_DIR, "selfcheck")
    WIND_RAW_DIR = os.path.join(sandbox, "raw")
    WIND_META_DIR = os.path.join(sandbox, "_meta")
    WIND_BUDGET_FILE = os.path.join(WIND_META_DIR, "budget_ledger.jsonl")
    try:
        _selfcheck_cache(failures)
        _selfcheck_snap(failures)
        _selfcheck_violation(failures)
    finally:
        WIND_RAW_DIR, WIND_META_DIR, WIND_BUDGET_FILE = saved

    prod_after = _sc_file_snapshot(prod_raw)
    if prod_before != prod_after:
        failures.append(f"[S3] 自检期间生产 raw 目录发生变化：{prod_raw}")

    print(f"\n=== wind_gate 自验完成：{len(failures)} 项失败 ===")
    for f in failures:
        print(f"  ❌ {f}")
    if not failures:
        print("  ✅ 三项断言全过")
    return failures


if __name__ == "__main__":
    if selfcheck():
        raise SystemExit(1)
