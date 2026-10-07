# -*- coding: utf-8 -*-
"""一次性 / 外部回测脚本的**产物契约封装器**（老大 2026-10-01 令 · 决策项 3）。

## 治什么病

2026-09-30 的外部方法论复现（`_archive/.../round2/`，19 个脚本）是典型的
**现场脚本**：产物 CSV **就地散落**在脚本同级目录，没有 manifest、没有数据锚、
没有配置哈希、没有代码版本、没有重算锚。证伪馆的 F-48/F-49/D-06 引用了它们的
结论，但结论无法指向一段可复算的执行。

本工具给**任何**非 btf 引擎的回测脚本套上同一套契约，而**不要求改造脚本本身**：
一张 `manifest.json`（复用 `btf.experiment.manifest.RunManifest`，与真入口 run
**同结构**），内含：

    script_sha256   脚本源码指纹（改一个字符即可发现结论换了代码）
    params_hash     --params 的规范哈希
    config_hash     上述二者的复合哈希 → 决定 run_id（同输入必同 id）
    data_version    数据锚点（声明的 tables + as_of + 主库根）
    code_version    包版本 + git commit + dirty
    env_version     numpy/pandas/pyarrow 实采版本
    outputs         本次运行**实际新增/改动**的文件清单 + sha256
    command         重算锚：一条可复制粘贴的完整命令

## 为什么用「目录快照差分」而不是「要求脚本写指定目录」

改造 19 个存量脚本风险高、收益低。差分方案：跑前记录目标树哈希一遍，跑后
再记一遍，差出来的就是本次产物——**不改变脚本任何行为**，却拿到了产物指纹。
新增代码只要 `--outdir` 配合即可未来接入净重定向。

## 用法

    # ① 执行并登记（推荐：新脚本）
    python tools/adhoc_run.py --name rrg-backtest \\
        --script ../path/rrg_backtest.py --params params.json \\
        --tables daily,index_daily --as-of 20260923 -- --k 10 --top 5

    # ② 仅登记已有产物（存量脚本重跑成本高/数据不全时用）
    python tools/adhoc_run.py --name rrg-backtest --register-only \\
        --script ../path/rrg_backtest.py --watch-dir ../path/round2

    # ③ 查询登记台
    python tools/adhoc_run.py --list
退出码：0=成功 / 1=失败（脚本非零退出或登记失败）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from btf.config.paths import MARKET_DATA_DIR, OUTPUT_DIR
from btf.experiment.manifest import RunManifest, canonical_json, sha256_hex

#: 登记台根（与 决策项1/2 的产物同根，便于统一索引）
OUT_DEFAULT = OUTPUT_DIR / "adhoc"
REGISTRY = "registry.jsonl"

#: 差分时忽略的噪声文件
_IGNORE = {".git", "__pycache__", ".pytest_cache", ".ipynb_checkpoints"}
_IGNORE_SUFFIX = {".pyc", ".pyo", ".log", ".tmp", ".sock"}


def _file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def snapshot_tree(root: Path) -> dict[str, tuple[int, int]]:
    """目标树快照 → {相对路径: (size, mtime_ns)}。"""
    out: dict[str, tuple[int, int]] = {}
    if not root.is_dir():
        return out
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if any(part in _IGNORE for part in rel.parts):
            continue
        if p.suffix in _IGNORE_SUFFIX or p.name == REGISTRY:
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        out[str(rel).replace("\\", "/")] = (st.st_size, st.st_mtime_ns)
    return out


def diff_snapshot(before: dict[str, tuple[int, int]],
                  after: dict[str, tuple[int, int]]) -> list[str]:
    """新增或修改的文件（删除不登记——产物关心的是留下了什么）。"""
    changed = []
    for rel, sig in after.items():
        if rel not in before or before[rel] != sig:
            changed.append(rel)
    return sorted(changed)


def _load_params(params_path: str | None) -> dict:
    if not params_path:
        return {}
    p = Path(params_path)
    if not p.is_file():
        raise SystemExit(f"[error] params 不存在：{p}")
    return json.loads(p.read_text(encoding="utf-8"))


def build_config(name: str, script: Path, params: dict, args: list[str],
                 tables: list[str], as_of: str, watch_dir: Path) -> dict:
    """冻结配置 → 决定 config_hash / run_id（同输入必同 id）。"""
    return {
        "schema_version": "adhoc.v1",
        "name": name,
        "script": {"path": str(script), "sha256": _file_sha(script)},
        "params": params,
        "argv": args,
        "watch_dir": str(watch_dir),
        "data": {"tables": sorted(tables), "as_of": as_of,
                 "market_data_dir": str(MARKET_DATA_DIR)},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="一次性回测脚本产物契约封装器")
    ap.add_argument("--name", required=False, help="实验名（登记 key）")
    ap.add_argument("--script", help="待执行/待登记的脚本路径")
    ap.add_argument("--params", help="参数 JSON 路径")
    ap.add_argument("--tables", default="", help="声明用到的主库表，逗号分隔")
    ap.add_argument("--as-of", default="", help="数据基准日 YYYYMMDD")
    ap.add_argument("--watch-dir", default="",
                    help="差分监视目录（默认脚本所在目录）")
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    ap.add_argument("--register-only", action="store_true",
                    help="不执行脚本，仅登记现有产物（存量脚本用）")
    ap.add_argument("--list", action="store_true", help="列出登记台")
    ap.add_argument("script_args", nargs=argparse.REMAINDER,
                    help="传给被封装脚本的参数（用 -- 分隔）")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    root = Path(args.out)

    if args.list:
        reg = root / REGISTRY
        if not reg.is_file():
            print(f"[list] 登记台为空（{reg} 不存在）")
            return 0
        for line in reg.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            print(f"  {row['run_id']}  {row['status']:9s} "
                  f"{row['name']:24s} 产物 {row.get('n_outputs', 0):3d}  "
                  f"{row.get('finished_at', '')}")
        return 0

    if not args.name or not args.script:
        ap.error("除 --list 外，--name 与 --script 必填")

    script = Path(args.script).resolve()
    if not script.is_file():
        print(f"[error] 脚本不存在：{script}", file=sys.stderr)
        return 1
    watch = Path(args.watch_dir).resolve() if args.watch_dir else script.parent
    if not watch.is_dir():
        print(f"[error] 监视目录不存在：{watch}", file=sys.stderr)
        return 1

    tables = [t.strip() for t in args.tables.split(",") if t.strip()]
    params = _load_params(args.params)
    rest = [a for a in args.script_args if a != "--"]
    cfg = build_config(args.name, script, params, rest, tables, args.as_of,
                       watch)

    before = snapshot_tree(watch)
    run_dir = root / args.name
    run_dir.mkdir(parents=True, exist_ok=True)

    manifest = RunManifest.skeleton(
        cfg, data_fingerprint=None,
        assembly_notes=("adhoc：一次性/外部脚本，经 tools/adhoc_run.py 封装",))
    status, reason, logs = "COMPLETED", "", {}

    if args.register_only:
        # 存量脚本：不执行，直接把 watch 树里**已存在**的产物全登记
        after = snapshot_tree(watch)
        changed = sorted(after)
        notes = (*manifest.assembly_notes,
                 "register-only：未执行，登记存量产物")
        manifest = RunManifest.skeleton(
            cfg, data_fingerprint=None,
            assembly_notes=notes, run_id=manifest.run_id)
    else:
        t0 = time.perf_counter()
        proc = subprocess.run(
            [sys.executable, str(script), *rest],
            cwd=str(script.parent), capture_output=True, text=True,
            encoding="utf-8", errors="replace")
        wall = time.perf_counter() - t0
        logs = {"stdout": proc.stdout, "stderr": proc.stderr,
                "returncode": proc.returncode, "wall_s": round(wall, 1)}
        after = snapshot_tree(watch)
        changed = diff_snapshot(before, after)
        if proc.returncode != 0:
            status = "FAILED"
            reason = f"脚本退出码 {proc.returncode}"

    # 产物指纹
    outputs = []
    for rel in changed:
        f = watch / rel
        if not f.is_file():
            continue
        try:
            outputs.append({"path": rel, "size": f.stat().st_size,
                            "sha256": _file_sha(f)})
        except OSError:
            continue

    final = (manifest.completed({"n_outputs": float(len(outputs))})
             if status == "COMPLETED" else manifest.failed(reason))
    payload = final.to_dict()
    payload.update({
        "name": args.name,
        "script_sha256": cfg["script"]["sha256"],
        "params_hash": sha256_hex(canonical_json(params)),
        "outputs": outputs,
        "command": " ".join([sys.executable, str(script), *rest]),
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    })
    payload["logs"] = {k: v for k, v in logs.items() if k != "stdout"}
    payload["stdout"] = logs.get("stdout", "")[-8000:]

    out_dir = run_dir / manifest.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    row = {"run_id": manifest.run_id, "name": args.name, "status": status,
           "finished_at": payload["finished_at"],
           "script": str(script), "script_sha256": cfg["script"]["sha256"],
           "params_hash": payload["params_hash"],
           "config_hash": manifest.config_hash, "tables": sorted(tables),
           "as_of": args.as_of, "n_outputs": len(outputs),
           "dir": str(out_dir)}
    with (root / REGISTRY).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"[adhoc] {status} | run_id={manifest.run_id}")
    print(f"   脚本指纹 {cfg['script']['sha256'][:22]}…")
    print(f"   配置哈希 {manifest.config_hash[:22]}…")
    print(f"   产物 {len(outputs)} 个（首 {min(5, len(outputs))}）")
    for o in outputs[:5]:
        print(f"     · {o['path']}  {o['size']}B  {o['sha256'][:22]}…")
    print(f"   manifest → {out_dir / 'manifest.json'}")
    print(f"   登记台   → {root / REGISTRY}")
    return 0 if status == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
