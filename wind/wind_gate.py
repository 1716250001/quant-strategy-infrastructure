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
import os, sys, json, datetime as _dt, hashlib, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config_wind import (
    assert_writable, WIND_RAW_DIR, WIND_BUDGET_FILE, WIND_META_DIR,
    WIND_CALL_INTERVAL, WIND_QPS_LIMIT, WIND_MAX_RETRY, WIND_RPC_TIMEOUT,
    WIND_DAILY_CALL_SOFT_LIMIT, WIND_DAILY_POINT_SOFT_LIMIT,
    WIND_API_KEY, violation_count, normalize_date_iso8601,
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
    df.to_parquet(snap_path, index=False)
    print(f"[snap] 写入 {topic}/{as_of} ({len(df)} 行)")
    return snap_path


def selfcheck():
    """W-3 自验：缓存命中计数 + 快照覆盖拒绝 + violation_count 一致性"""
    print("=== wind_gate 自验 ===")

    # 测试 1：连续两次同 params → 第二次 cache_hit
    print("\n[1] 缓存命中测试:")
    r1 = call("test", "probe", {"a": 1, "b": 2}, cache_ttl=3600)
    r2 = call("test", "probe", {"a": 1, "b": 2}, cache_ttl=3600)
    print(f"  两次调用均已记账")

    # 测试 2：快照覆盖被拒
    print("\n[2] 快照覆盖拒绝:")
    test_snap_dir = os.path.join(os.path.dirname(WIND_RAW_DIR), "norm", "snap", "_gate_test")
    os.makedirs(test_snap_dir, exist_ok=True)
    test_path = os.path.join(test_snap_dir, "20261022.parquet")
    # 先写一个空的
    import pandas as pd
    pd.DataFrame({"x": [1, 2]}).to_parquet(test_path, index=False)
    try:
        write_snap("_gate_test", "20261022", {"x": [3, 4]}, force=False)
        print("  ❌ 快照覆盖未被拒绝")
    except WindGateError as e:
        print(f"  ✅ 快照覆盖已拒绝")
    # 清理
    os.remove(test_path)
    os.rmdir(test_snap_dir)
    # 再清理空 norm/snap 目录
    for _ in range(2):
        try:
            os.rmdir(os.path.dirname(test_snap_dir))
        except OSError:
            break

    # 测试 3：越界拦截
    print("\n[3] assert_writable 越界:")
    before = violation_count()
    try:
        call("test", "probe_out", {"a": 1})  # 正常路径
    except Exception:
        pass
    after = violation_count()
    print(f"  violation_count 正常流程: {before} → {after} (预期不变)")

    print(f"\n=== wind_gate 自验完成 ===")
    print(f"  cache 键数: {len(_CACHE)}")
    print(f"  violation_count: {violation_count()}")
    print(f"  budget_ledger 行数: ", end="")
    if os.path.exists(WIND_BUDGET_FILE):
        with open(WIND_BUDGET_FILE, "r") as f:
            print(f"{sum(1 for _ in f)}")
    else:
        print("0")


if __name__ == "__main__":
    selfcheck()
