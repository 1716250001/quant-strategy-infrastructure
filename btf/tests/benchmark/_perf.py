# -*- coding: utf-8 -*-
"""L5 性能门槛的**归一化墙钟**（**MM-8**，19 号 §140）——探针与判定助手。

**要解决的问题**（§139 F-2 实测）：B2／B3／B4 的预算是对**绝对墙钟**的门槛，
而本机吞吐随负载漂移：同一代码、同一会话，仅因起始时刻不同即跨越门槛
（B4 实测 18.2／21.7／22.6／24.5s ⇒ 外推 1516／1808／1881／2042s，预算
1800s ↔ 临界 21.6s）。⇒ 门槛「绿了不代表没问题，红了也不代表有回归」。

**做法**（派单原则：不放宽任何 budget、不删任何断言；改的是"用什么量去比"）：
    normalized = raw ÷ machine_factor()
    machine_factor = clamp(max(cpu/cpu_ref, io/io_ref), [1.0, 3.0])
即：把本机当前状态折算成"相当于参考机的多少倍慢"，再与**原 budget** 比。
只许放大（`>=1.0`）：快机器不得因此放宽门槛（防"快机放宽"）。

**环境不达标 vs 代码回归**（必须可分；**M-1（19 号 §141 D）顺序订正**）：
    先判达标：`normalized <= budget` ⇒ **通过**（不论 k 多大——clamp 只到
        `FACTOR_MAX`，normalized 是**欠校正**的，欠校正只会让"通过"更难，
        不会制造假通过）；
    仅当 `normalized > budget` **且** `k > FACTOR_MAX` ⇒ `[ENV]` 失败
        （**不许 skip**）——此时确实无法区分"机器太慢"还是"代码退化"；
    `normalized > budget` 且 `k <= FACTOR_MAX` ⇒ `[PERF]` 失败。

参考常数（`CPU_REF`／`IO_REF`）为**空载 n=3 取中位**；**禁止在负载下取样**
（否则系数恒≈1，机制自废）。换机须重取并留痕。
"""
from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from btf.config.paths import MARKET_DATA_DIR

#: 参考机：本机（书房电脑，12 逻辑核），2026-10-24，空载（实测均值 ~20% 负载），
#: n=3 取中位。**换机须重取并留痕**（禁止在负载下取样，否则系数恒≈1、机制自废）。
#: CPU 探针（`cpu_probe`）：0.2070 / 0.2079 / 0.2063 ⇒ 中位 **0.2070s**
CPU_REF = 0.2070
#: IO 探针（`io_probe`，预热后取两次最小）：0.0550 / 0.0533 / 0.0537 ⇒ 中位 **0.0537s**
IO_REF = 0.0537

#: 系数上限（只许放大；到顶仍超 ⇒ `[ENV]`：本机不具判定条件）
FACTOR_MAX = 3.0

#: CPU 探针固定运算量（**写死，不按 CPU 自适应**——§140.2 第 5 条）
_CPU_RING = 2_000_000
_CPU_FLOAT = 200_000

#: IO 探针固定文件集与固定 2 列（§140.1）
_IO_FILES = (("daily", "2016.parquet", ("ts_code", "close")),
             ("adj_factor", "2016.parquet", ("ts_code", "adj_factor")),
             ("index_daily", "2016.parquet", ("ts_code", "close")))


def cpu_probe() -> float:
    """固定运算量的 CPU 探针（整数环 + 浮点累加），返回秒。"""
    t0 = time.perf_counter()
    x = 12345
    acc = 0
    for _ in range(_CPU_RING):
        x = (x * 1103515245 + 12345) & 0x7FFFFFFF
        acc += x
    total = 0.0
    for i in range(_CPU_FLOAT):
        total += 1.0 / (i + 1)
    if acc == 0 or total == 0.0:      # 防"死代码消除"把探针优化成空跑
        raise RuntimeError("CPU 探针退化（结果未被使用？）")
    return time.perf_counter() - t0


def io_probe() -> float | None:
    """固定文件的固定 2 列读取探针；主库不可用 ⇒ `None`（系数只用 CPU 项）。

    I/O 首读含页缓存冷启动（实测首样本 0.66s vs 热 0.055s）⇒ 先**预热一遍**
    （不计时），再计时两次取**最小**：探针要测"本机当前吞吐"，不是缓存状态。
    """
    import pyarrow.parquet as pq

    root = Path(MARKET_DATA_DIR)
    paths = [(root / name / fname, cols) for name, fname, cols in _IO_FILES]
    if not all(path.is_file() for path, _ in paths):
        return None
    for path, cols in paths:                      # 预热（不计入）
        pq.read_table(path, columns=list(cols))
    samples: list[float] = []
    for _ in range(2):
        rows = 0
        t0 = time.perf_counter()
        for path, cols in paths:
            rows += pq.read_table(path, columns=list(cols)).num_rows
        samples.append(time.perf_counter() - t0)
        if rows == 0:
            raise RuntimeError("IO 探针退化（读到 0 行？）")
    return min(samples)


def raw_machine_factor() -> float:
    """未截断的系数（`[ENV]` 判据用；> `FACTOR_MAX` 即本机不具判定条件）。"""
    cpu_ratio = cpu_probe() / CPU_REF
    io_seconds = io_probe()
    io_ratio = (io_seconds / IO_REF) if io_seconds is not None else 0.0
    return max(cpu_ratio, io_ratio)


def machine_factor() -> float:
    """标定系数：`max(cpu/cpu_ref, io/io_ref)`，**只许放大**（clamp 下界 1.0）。

    `> FACTOR_MAX` ⇒ 抛 `EnvNotQualified`（消息格式固定，防"空转护栏"）。
    """
    raw = raw_machine_factor()
    if raw > FACTOR_MAX:
        raise EnvNotQualifiedError(
            f"[ENV] 标定系数 k={raw:.2f}>3.0，本机不具备判定条件——请空载重跑；"
            f"不得据此判代码回归、不得改 budget")
    return max(1.0, raw)


class EnvNotQualifiedError(AssertionError):
    """本机状态不满足判定条件（**失败**，不是 skip——§140.1）。"""


def _normalized(raw: float, factor_raw: float) -> tuple[float, float]:
    """→ (用于判定的系数, normalized)：系数只许放大且截到 `FACTOR_MAX`。"""
    factor = min(FACTOR_MAX, max(1.0, factor_raw))
    return factor, raw / factor


def run_budget(
    name: str,
    measure: Callable[[], Any],
    budget: float,
    *,
    hint: str,
    seconds: Callable[[Any], float] | None = None,
) -> Any:
    """min-of-2 归一化门槛判定；通过则返回**第一次达标**那次的 measure 结果。

    纪律（§140.1）：
        - 每个基准最多跑 **2 次**；**任一次** `normalized <= budget` 即通过；
        - 第二次**仅在第一次未达标时**触发（不无条件双跑）；
        - 每次都打印 `raw / factor / normalized` 三行；
        - **先判达标**：`normalized <= budget` ⇒ 通过（不论 k 多大）；
        - 仅当 `normalized > budget` 且 `k > 3.0` ⇒ `[ENV]`（abort，不重试）；
        - `normalized > budget` 且 `k <= 3.0` ⇒ 继续第二次；两次均不达标 ⇒
          `[PERF]`，消息同时给出两次 raw 与 normalized。
    """
    pick = seconds or (lambda value: float(value))
    attempts: list[tuple[float, float, float]] = []
    for attempt in (1, 2):
        # **双侧采样**（实测教训）：只在负载跑完后采样会漏掉"工作期间才有负载"
        # 的情形（实测：B2 在 12 进程满载下 raw 14.3s→33.9s，但事后采样 k=1.00）。
        # 故取"工作前 / 工作后"两次采样的**较大者**——两端口径一致，单侧失真即失效。
        factor_before = raw_machine_factor()
        result = measure()
        factor_after = raw_machine_factor()
        raw = pick(result)
        factor_raw = max(factor_before, factor_after)
        factor, normalized = _normalized(raw, factor_raw)
        attempts.append((raw, factor, normalized))
        print(f"\n{name} attempt-{attempt}: raw {raw:.2f}s | "
              f"factor {factor:.2f}（k_before={factor_before:.2f} / "
              f"k_after={factor_after:.2f}） | "
              f"normalized {normalized:.2f}s（budget {budget:.0f}s）")
        # **M-1（19 号 §141 D）——先判达标，再判环境**：clamp 只截到
        # `FACTOR_MAX` ⇒ normalized 是**欠校正**的（欠校正只会让"通过"更难，
        # 不会制造假通过），故只要 normalized ≤ budget 即通过，与 k 多大无关。
        # 只有"未达标且 k>上限"才无法区分"机器太慢"与"代码退化" ⇒ `[ENV]`。
        if normalized <= budget:
            return result
        if factor_raw > FACTOR_MAX:
            raise EnvNotQualifiedError(
                f"[ENV] 标定系数 k={factor_raw:.2f}>3.0，本机不具备判定条件——"
                f"请空载重跑；不得据此判代码回归、不得改 budget")
    raw, factor, normalized = attempts[-1]
    detail = "；".join(
        f"#{i} raw {r:.2f}s / normalized {n:.2f}s"
        for i, (r, _f, n) in enumerate(attempts, 1))
    raise AssertionError(
        f"[PERF] {name} normalized {normalized:.2f}s > {budget:.0f}s"
        f"（raw {raw:.2f}s / k {factor:.2f}）——{hint}；两次实测：{detail}")


__all__ = ["CPU_REF", "FACTOR_MAX", "IO_REF", "EnvNotQualifiedError", "cpu_probe",
           "io_probe", "machine_factor", "raw_machine_factor", "run_budget"]
