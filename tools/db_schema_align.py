# -*- coding: utf-8 -*-
"""tools/db_schema_align.py — 跨年度分区 schema 漂移扫描/修复（只读扫描 + 显式修复）
==============================================================================
用途：检出并修复"同一张表的不同年度文件 arrow schema 不一致"。

为什么需要它（2026-10-11 实证，E-01 写盘副作用连带发现）
------------------------------------------------------
`common/parquet_store.merge_append` 走 pandas 往返写盘，会**丢 arrow 类型**：
    · 全空列 `null` ⇄ `double`        （实证 `dividend.stk_bo_rate`）
    · `string` ⇄ `large_string`      （实证 `namechange.end_date`）
后果：同一表不同年份分区的 schema 不一致 ⇒ 跨分区 `pa.concat_tables` 直接抛
`ArrowInvalid`（btf `data/quality.py::_check_cc4_events` 即在此崩；普通跨年读取
也会因 schema 合并而降级/报错）。

根治与善后（两件都要做）
------------------------
1. **根治**：`merge_append` 已改为"以既有文件 arrow schema 为权威"落盘
   （`_existing_schema` + `_write_aligned`）—— 写入不再引入新漂移。
2. **善后**：本工具把**少数派** schema 的分区重写为**多数派** schema
   （按列名 cast；`string`⇄`large_string`、`null`→具体类型均为安全转换）。

判定口径（刻意保守）
--------------------
- 只处理 `by_year` 布局（`{YYYY}.parquet`）；非年度文件不参与。
- **canonical schema ＝ 逐列并集**（不是简单多数派）：同一列在各分区出现过的
  类型按**更宽者**取（`null` < `string` < `large_string` < 数值型）——
  因为 `null`/`string` 往往是"该年整列为空"的**推断产物**，按多数派会把
  有效值列压成 `null` 反而 cast 失败。
- 列集合相同、**仅列序不同** ⇒ 按**多数派列序**统一（schema 元数据级规范化；
  行值/行数零改动，按列名取数的消费侧不受影响）。（R64′ C 段实证：`bc_otcqt`
  三个年度文件列集合完全相同、仅顺序不同。）
- 列集合**真不一致**（某分区缺列/多列）⇒ 该表**不动**并如实报告——补列属结构
  变更，超出"仅类型 cast"授权，须另行裁决（宁可留红也不静默补列）。
- cast 失败的分区**不动**且如实报告（禁静默改数据）。
- 修复是**逐文件原子写**（.tmp → os.replace），任何单文件失败只影响该文件。

⚠ 规模提示（2026-10-11 全库扫描：32 张表、约 1.96 GB 存在漂移，其中
  `moneyflow` 1.1 GB / `income` 28 个变体 / `balancesheet` 34 个变体）——
  这是**历史写入（pandas 往返丢类型）**的存量问题，与本次审查同源发现。
  修复是**写库操作**：按表分批、先冷备，不要一次全库跑。

用法:
  python -m tools.db_schema_align scan                 # 全库扫描（只读）
  python -m tools.db_schema_align scan dividend        # 只扫指定表
  python -m tools.db_schema_align fix dividend namechange   # 修复指定表
  python -m tools.db_schema_align fix --all            # 修复全部有漂移的表
⚠ 修复会写库；先 `scan` 看清范围，必要时先冷备。
"""
import argparse
import os
import sys
from collections import defaultdict

import pyarrow as pa
import pyarrow.parquet as pq

_CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)

from config import MARKET_DATA_DIR      # noqa: E402


def _sig(schema):
    return tuple((n, str(t)) for n, t in zip(schema.names, schema.types))


def year_files(table, root=None):
    """该表的年度分区文件（升序；非 by_year 布局返回 []）。"""
    d = os.path.join(root or MARKET_DATA_DIR, table)
    if not os.path.isdir(d):
        return []
    return sorted(f for f in os.listdir(d)
                  if f.endswith(".parquet") and len(f) == 12 and f[:4].isdigit())


def scan(tables=None, root=None):
    """→ {表名: {schema_sig: [文件, ...]}}（只含漂移表）。"""
    root = root or MARKET_DATA_DIR
    names = tables or sorted(
        e for e in os.listdir(root)
        if os.path.isdir(os.path.join(root, e)) and not e.startswith("."))
    drift = {}
    for t in names:
        fs = year_files(t, root)
        if len(fs) < 2:
            continue
        sigs = defaultdict(list)
        for f in fs:
            try:
                sigs[_sig(pq.read_schema(os.path.join(root, t, f)))].append(f)
            except Exception as e:
                sigs[("__ERR__", str(e)[:60])].append(f)
        if len(sigs) > 1:
            drift[t] = dict(sigs)
    return drift


def canonical_schema(schemas):
    """逐列并集 canonical schema → (schema, 说明)；不可并 → (None, 原因)。

    列型优先级（宽者胜）：数值型 > large_string > string > null。
    理由：`null`/`string` 常是"该年整列为空"的推断产物，按多数派会把有效值列
    压成 `null` → cast 反而失败。

    列序（R64′ C 段实证 bc_otcqt）：**列集合相同、仅顺序不同** ⇒ 按多数派列序
    统一（schema 元数据级规范化，行值/行数零改动，消费侧按列名取数不受影响）；
    **列集合不同**（缺列/多列）⇒ 拒修（补列属结构变更，超出"仅类型 cast"授权，
    须另行裁决——宁可留红也不静默补列）。
    """
    from collections import Counter

    orders = Counter(tuple(s.names) for s in schemas)
    canon_names = list(orders.most_common(1)[0][0])
    sets = {frozenset(o) for o in orders}
    if len(sets) > 1:
        miss = set.union(*sets) - set.intersection(*sets)
        return None, (f"列集合不一致（结构差异，非类型差异；差异列 {sorted(miss)[:6]}"
                      f"）——补列超出本工具授权，须另行裁决")
    # 按列名聚合类型（列序可能不同 ⇒ 不能按下标对齐）
    types_by_col: dict[str, list] = {}
    for s in schemas:
        for name, t in zip(s.names, s.types):
            types_by_col.setdefault(name, [])
            if not any(t.equals(x) for x in types_by_col[name]):
                types_by_col[name].append(t)
    fields = []
    conflict = []
    for name in canon_names:
        cand = types_by_col[name]
        if not cand or all(pa.types.is_null(t) for t in cand):
            fields.append(pa.field(name, pa.null()))     # 全分区皆空列
            continue
        cand = [t for t in cand if not pa.types.is_null(t)]
        if len(cand) == 1:
            fields.append(pa.field(name, cand[0]))       # 唯一实型（含 double 等）
            continue
        names_s = {str(t) for t in cand}
        if names_s <= {"string", "large_string"}:
            fields.append(pa.field(name, pa.large_string()))
            continue
        if all(pa.types.is_integer(t) or pa.types.is_floating(t) for t in cand):
            fields.append(pa.field(name, pa.float64()))
            continue
        conflict.append(f"{name}:{sorted(names_s)}")
    if conflict:
        return None, f"列类型冲突且无安全并集: {conflict[:4]}"
    why = "逐列并集"
    if len(orders) > 1:
        why += "＋列序统一（列集合相同，按多数派列序）"
    return pa.schema(fields), why


def fix(table, root=None):
    """把各分区统一到 canonical schema（逐列并集）。返回 (已修数, 失败, 说明)。"""
    root = root or MARKET_DATA_DIR
    d = os.path.join(root, table)
    fs = year_files(table, root)
    schemas, sigs = [], defaultdict(list)
    for f in fs:
        s = pq.read_schema(os.path.join(d, f))
        schemas.append(s)
        sigs[_sig(s)].append(f)
    if len(sigs) <= 1:
        return 0, [], "无漂移，跳过"
    canon, why = canonical_schema(schemas)
    if canon is None:
        return 0, [], f"未修（{why}）"
    canon_sig = _sig(canon)
    fixed, failed = [], []
    for sig, files in sigs.items():
        if sig == canon_sig:
            continue
        for f in files:
            p = os.path.join(d, f)
            try:
                df = pq.read_table(p).to_pandas()
                cols = list(canon.names)
                t = pa.Table.from_pandas(df[cols], preserve_index=False)
                t = t.cast(canon)
                tmp = p + ".tmp"
                pq.write_table(t, tmp)
                os.replace(tmp, p)
                fixed.append(f)
            except Exception as e:
                failed.append(f"{f}: {str(e)[:70]}")
    note = (f"canonical={why}；变体 {len(sigs)} 种；已修 {len(fixed)} 个分区 "
            f"{fixed[:6]}{'…' if len(fixed) > 6 else ''}")
    return len(fixed), failed, note


def main(argv=None):
    try:                       # GBK 控制台下 ⚠ 等符号会让 print 抛错（同 doctor 教训）
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="跨年度分区 schema 漂移扫描/修复")
    p.add_argument("action", choices=["scan", "fix"])
    p.add_argument("tables", nargs="*", help="表名（缺省＝全库扫描）")
    p.add_argument("--all", action="store_true", help="fix：修复全部有漂移的表")
    a = p.parse_args(argv)

    if a.action == "scan":
        d = scan(a.tables or None)
        if not d:
            print("无跨分区 schema 漂移")
            return 0
        print(f"发现 {len(d)} 张表存在跨分区 schema 漂移:")
        for t, sigs in d.items():
            print(f"\n  [{t}]")
            for sig, files in sigs.items():
                print(f"    {len(files)} 文件 {files[0]}…{files[-1]}")
                if sig and sig[0] == "__ERR__":
                    print(f"      读取失败: {sig[1]}")
                else:
                    diff = [f"{n}:{t_}" for n, t_ in sig]
                    print(f"      {diff}")
        return 1

    targets = a.tables or (sorted(scan()) if a.all else [])
    if not targets:
        print("未指定表名（或用 --all）；无动作")
        return 2
    n_fail = 0
    fixed_summary = []                     # (表, 修的分区数)——结构性变更记库版本用
    for t in targets:
        n, failed, note = fix(t)
        print(f"[{t}] {note}")
        if n:
            fixed_summary.append(f"{t}({n}分区)")
        if failed:
            n_fail += len(failed)
            print(f"    ⚠ 失败 {len(failed)}: {failed}")
    # ── 库版本（08 §13.5："--rebuild 类整表重建视同结构性变更"；schema 对齐同理。
    #    EE-D11-1 动作 A 布线 + R64′ C 段回执建议：结构性变更应留版本痕）──
    if fixed_summary and not n_fail:
        try:
            from tools.db_version import record as _dbv
            _dbv([s.split("(")[0] for s in fixed_summary],
                 note=f"跨分区 schema 对齐 {', '.join(fixed_summary)}",
                 structural=True)
        except Exception as e:
            print(f"    [WARN] db_version 记录失败（不影响主流程）: {str(e)[:80]}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
