# -*- coding: utf-8 -*-
"""main.py — CLI 入口薄壳（W7b 起命令层移至 cli/）。

兼容保留（勿删）：
  - build_parser  ← tools/regress_pipeline.py:149 `from main import build_parser`
  - cmd_gap_update / cmd_leverage / cmd_etf_flow
                  ← tools/regress_pipeline.py:346 R5 断言 `hasattr(m, f)`
"""
# 兼容面 re-export（R5 断言/回归导入依赖）：以下 4 名非未用导入 —— 禁删（§339 §3 第 3 类）
from cli import main
from cli.fetch_cmds import cmd_gap_update  # noqa: F401
from cli.parser import build_parser  # noqa: F401
from cli.research_cmds import cmd_etf_flow, cmd_leverage  # noqa: F401

if __name__ == "__main__":
    main()
