# -*- coding: utf-8 -*-
"""IO 单一入口检查（**EX-4 完全体**；19 号 §9.2 / §47.4 余项 ② / §49）。

纪律（`data` 域的唯一 IO 出口）：
    1. `btf/data/parquet_reader.py` —— 磁盘读取的**唯一实现**（布局解析 /
       pyarrow.parquet 调用）；
    2. `btf/data/core.py` —— 全系统**唯一**允许 import `parquet_reader` 的模块
       （其余模块经 `YearTableStore` 的同名方法取数）；
    3. 因此 `import pyarrow.parquet` 在 `btf/` 内**只允许**出现在上述两个文件。

为什么需要本工具（与 import-linter 新 9 的关系）：
    import-linter 只能拦"模块间 import"，拦不住**第三方包**的直接使用
    （`import pyarrow.parquet as pq` 是外部导入，不属任何内部契约）。
    本工具是该空白的补丁：AST 扫描 `btf/**/*.py`，逐条判定。

变异性自检（铁律新 15「防线必须可被证伪」）：`--self-test` 用**合成源码**
跑同一套判定，断言"注入违例必被检出、合规样本必通过"——防止规则写反/失效。

用法：python tools/check_io_boundary.py [--self-test]
      （= `bt check` 第 9/9 项，带 --self-test 编排；单跑仅用于定位）
退出码：0=通过 / 1=违例（或自检失败）
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 允许触磁盘的**唯一**两个文件（相对仓库根）
IO_OWNERS = {"btf/data/parquet_reader.py", "btf/data/core.py"}

#: `import pyarrow.parquet as pq` 之后禁止出现的属性（其余文件命中即违例）
_PQ_ATTRS = frozenset({"read_table", "read_schema", "ParquetFile", "write_table",
                       "write_to_dataset"})

#: 禁止的 import 形态（例外见 IO_OWNERS）
_FORBIDDEN_IMPORTS = ("pyarrow.parquet", "btf.data.parquet_reader")


def _rel(path: Path) -> str:
    """相对仓库根的 posix 路径（绝对/相对入参皆可；self-test 用相对路径）。"""
    s = str(path).replace("\\", "/")
    root = str(ROOT).replace("\\", "/")
    if s.startswith(root + "/"):
        s = s[len(root) + 1:]
    return s.lstrip("./")


def scan(path: Path, src: str) -> list[str]:
    """扫描单份源码 → 违例清单（相对路径 + 说明）。

    判定用 **AST**（不是行正则）：注释与 docstring 里提到 `pq.read_table`
    **不算**违例（本项目大量留痕注释会提及它）——只有真实 import / 调用节点
    才判红。这是本工具的第一版教训：行正则把三处**说明性注释**误报为违例。
    """
    rel = _rel(path)
    if rel in IO_OWNERS:
        return []
    out: list[str] = []
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name in _FORBIDDEN_IMPORTS:
                    out.append(f"{rel}:{node.lineno}: import {a.name}"
                               f"（IO 只允许 {sorted(IO_OWNERS)}）")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod in _FORBIDDEN_IMPORTS:
                out.append(f"{rel}:{node.lineno}: from {mod} import …"
                           f"（IO 只允许 {sorted(IO_OWNERS)}）")
        elif (isinstance(node, ast.Attribute)
              and isinstance(node.value, ast.Name)
              and node.value.id == "pq"
              and node.attr in _PQ_ATTRS):
            out.append(f"{rel}:{node.lineno}: 直接调用 pyarrow.parquet 接口："
                       f"pq.{node.attr}")
    return out


def check() -> list[str]:
    problems: list[str] = []
    for py in sorted((ROOT / "btf").rglob("*.py")):
        problems.extend(scan(py, py.read_text(encoding="utf-8")))
    return problems


def _self_test() -> int:
    """变异性自检：合成源码上判定必须**双向**成立（能红、能绿）。"""
    bad_cases = [
        ("btf/data/state.py", "import pyarrow.parquet as pq\n"),
        ("btf/data/feed.py", "from btf.data.parquet_reader import read_full\n"),
        ("btf/engine/loop.py", "import pyarrow.parquet as pq\npq.read_table(p)\n"),
    ]
    good_cases = [
        ("btf/data/parquet_reader.py", "import pyarrow.parquet as pq\n"),
        ("btf/data/core.py", "from btf.data import parquet_reader as pr\n"),
        ("btf/data/state.py", "from btf.data.core import YearTableStore\n"),
        ("btf/runtime/_runtime.py", "from btf.data.core import YearTableStore\n"),
    ]
    for rel, src in bad_cases:
        found = scan(Path(rel), src)
        if not found:
            print(f"[self-test:FAIL] 违例未被检出：{rel} :: {src.strip()[:40]}")
            return 1
    for rel, src in good_cases:
        found = scan(Path(rel), src)
        if found:
            print(f"[self-test:FAIL] 合规样本被误报：{rel} :: {found}")
            return 1
    print(f"[self-test:ok] 注入违例 {len(bad_cases)} 例全部检出；"
          f"合规样本 {len(good_cases)} 例全部通过")
    return 0


def main(argv: list[str]) -> int:
    if "--self-test" in argv:
        return _self_test()
    problems = check()
    if problems:
        print("[fail] IO 单一入口违例（EX-4；19 号 §9.2）：")
        for p in problems:
            print(f"  - {p}")
        return 1
    n = len(list((ROOT / "btf").rglob("*.py")))
    print(f"[ok] IO 单一入口检查通过（{n} 个文件扫描；"
          f"仅 {', '.join(sorted(IO_OWNERS))} 可触磁盘）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
