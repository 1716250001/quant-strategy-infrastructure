# -*- coding: utf-8 -*-
"""分批补数 driver — 每批 N 任务 + 批后自检，出错即停。

为什么分批 + 每批自检（2026-11-16 P0）：
  一次 `--limit 520` 曾出现「断点 +1,040、数据只落 4 个指数、17.9 万行
  被静默覆盖」，全程零 ERROR。「一批 50 指数」一旦踩中就永久缺 50 个指数，
  而批次数**不影响总耗时**（瓶颈是限频 170 次/分，不是进程启动），
  所以把批次切小、每批后校验，是零成本缩小损失半径。
  根因（缺跨进程锁）已在 fetch/base.py:FileLock 修复，本 driver 是第二道网。

用法
----
    python scripts/run_backfill_batches.py --table index_weight --batch 1300

断点续跑：随时 Ctrl-C；重跑同命令会自动跳过已完成部分（断点 key =
`{指数代码}|{月末日}`）。自检失败时脚本退出码 1，断点已保存。
"""

import argparse
import os
import re
import subprocess
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
LOG_DIR = os.path.join(_ROOT, "_scratch", "backfill-batches")

RE_OK = re.compile(r"成功=(\d+) 空=(\d+) 失败=(\d+)")
RE_PEND = re.compile(r"待跑=\s*(\d+)")
# ⭐ 2026-11-17 修正：backfill 打印的「待跑=」是 --limit 截断后的值
#    （永远等于 batch，如 1300），拿它做 stall 判据会误判原地打转、第 4 批
#    就提前退出，导致补不到完备。真实进度用「跳过数」（limit 之前的值）：
#    真实待跑 = 任务数 - 跳过数。跳过数只在断点真的前进时才增长。
RE_SKIP = re.compile(r"任务数=\s*(\d+)\s+待跑=\s*\d+\s+跳过=\s*(\d+)")


def _run(cmd, log_path):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run(
        cmd, cwd=_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env
    )
    out = (p.stdout or "") + (p.stderr or "")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(out)
    return p.returncode, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", default="index_weight")
    ap.add_argument("--batch", type=int, default=1300, help="每批任务数（1300 = 10 指数）")
    ap.add_argument("--max-batches", type=int, default=60)
    ap.add_argument("--stall-limit", type=int, default=3, help="连续 N 批待跑不降则退出")
    # ⭐ 2026-11-18 P1：补拉场景下额外校验"清单内每个码是否真落盘"。
    #    引擎的「成功=N」不可信（cb_issue 实证：成功=1 但库内 0 行）。
    ap.add_argument(
        "--codes-file", default=None, help="给出则每批后额外跑 verify_landed 校验清单落盘"
    )
    a = ap.parse_args()

    os.makedirs(LOG_DIR, exist_ok=True)
    checker = os.path.join(_ROOT, "scripts", "check_backfill_coverage.py")
    verifier = os.path.join(_ROOT, "scripts", "verify_landed.py")
    prev_pend, stall = None, 0

    for i in range(1, a.max_batches + 1):
        ts = time.strftime("%H:%M:%S")
        print(f"\n===== BATCH {i}  {ts}  (limit={a.batch}) =====", flush=True)

        rc, _ = _run(
            [PY, checker, "--table", a.table, "--snapshot"],
            os.path.join(LOG_DIR, f"snap_{i:02d}.log"),
        )
        if rc != 0:
            print("  [FATAL] 快照失败，停止", flush=True)
            return 2

        cmd = [
            PY,
            os.path.join(_ROOT, "main.py"),
            "backfill",
            "--only",
            a.table,
            "--limit",
            str(a.batch),
        ]
        if a.codes_file:
            # 按清单补拉：不加 --force-listed，让断点生效——已成功的不重跑，
            # 未落盘的（如空日期被拒）下批自然重试。
            cmd += ["--codes-file", a.codes_file]
        rc, out = _run(cmd, os.path.join(LOG_DIR, f"batch_{i:02d}.log"))
        # ⭐ 2026-11-17 收尾 bug：待跑=0 时 backfill 不打「成功=N」进度行（直接
        #    跳过），原逻辑 RE_OK 匹配不到就误报 rc=2。先从跳过数判完成。
        skip_pre = RE_SKIP.findall(out)
        if skip_pre:
            _nt, _sk = (int(x) for x in skip_pre[-1])
            if _nt > 0 and _sk >= _nt:
                print(f"  ✅ 真实待跑=0（跳过={_sk}/{_nt}），补数完成", flush=True)
                return 0
        m = RE_OK.findall(out)
        if not m:
            print(f"  [FATAL] 未捕获到进度统计（rc={rc}），看日志", flush=True)
            return 2
        ok, empty, fail = (int(x) for x in m[-1])
        pend_m = RE_PEND.findall(out)  # limit 截断后的值，仅展示用
        pend_show = int(pend_m[0]) if pend_m else -1
        skip_m = RE_SKIP.findall(out)  # 真实进度：任务数/跳过数
        if skip_m:
            n_tasks, skipped = (int(x) for x in skip_m[-1])
            remain = n_tasks - skipped  # 真实剩余待跑
        else:
            n_tasks, skipped, remain = -1, -1, -1
        expect = ok + empty
        print(
            f"  rc={rc} 成功={ok} 空={empty} 失败={fail} | "
            f"真实剩余={remain}（已跳过={skipped}/{n_tasks}）| "
            f"本批读取={pend_show} expect={expect}",
            flush=True,
        )

        if rc != 0:
            print("  [FATAL] backfill 退出码非 0，停止（断点已保存）", flush=True)
            return 2

        vrc, vout = _run(
            [PY, checker, "--table", a.table, "--verify", "--expect", str(expect)],
            os.path.join(LOG_DIR, f"verify_{i:02d}.log"),
        )
        for line in vout.strip().splitlines():
            if line.startswith("[VERIFY]") or "[FAIL]" in line or "[WARN]" in line:
                print("  " + line, flush=True)
        if vrc != 0:
            print(f"  ⛔ 自检失败，停在 BATCH {i}（断点已保存，可重跑同命令续跑）", flush=True)
            return 1

        # P1（2026-11-18）：清单落盘校验——引擎的「成功=N」不可信
        if a.codes_file:
            lrc, lout = _run(
                [PY, verifier, "--table", a.table, "--codes-file", a.codes_file],
                os.path.join(LOG_DIR, f"landed_{i:02d}.log"),
            )
            for line in lout.strip().splitlines():
                if line.startswith("[落盘校验]") or "未落盘" in line or "✅" in line:
                    print("  " + line, flush=True)
            if lrc != 0:
                print(f"  ⛔ 落盘校验未过，停在 BATCH {i}", flush=True)
                return 1

        if remain == 0:
            print("\n✅ 真实待跑=0，补数完成", flush=True)
            return 0

        # stall 判据用「真实剩余」——空结果也记断点，故 remain 会因配额/失败
        # 停滞；连续 N 批不降才停，交人工排查（避免无限空转烧配额）。
        if prev_pend is not None and remain >= prev_pend:
            stall += 1
            print(
                f"  [WARN] 真实剩余未下降（{prev_pend} -> {remain}），连续 {stall} 次", flush=True
            )
            if stall >= a.stall_limit:
                print("  ⛔ 疑似原地打转（配额耗尽/全空），停止排查", flush=True)
                return 1
        else:
            stall = 0
        prev_pend = remain

    print("  [WARN] 达到 --max-batches 上限，可能未补完", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
