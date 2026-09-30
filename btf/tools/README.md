# -*- coding: utf-8 -*-
"""开发工具目录（工具脚本不进 btf 包：核心包 diff 为零纪律）。

常驻工具（可重复跑；多数已是 `bt` 子命令的**单一真源**，CLI 只转发不复制判定）：
    check.py                  九项架构门禁（`bt check` 委托本脚本）
    deps_budget.py            依赖预算 ≤8（门禁 4/9）
    check_domain_whitelist.py domain 白名单（门禁 6/9）
    check_report_render.py    报告渲染级结构（门禁 7/9）
    check_data_quality.py     数据不变量（门禁 8/9）
    check_io_boundary.py      IO 单一入口（门禁 9/9）
    check_plugin_boundary.py  插件边界基线（**不在九项内**：AA-3 显式刷新留痕）
    check_rules_four_way.py   规则四方机检（**不在九项内**：需赤潮源码树）
    cold_backup.py            跨版本源码冷备（`bt cold-backup` 委托）
    build_golden.py           黄金集构建/对账/重算（`bt dataset` 委托）
    golden_refcalc.py         独立参考计算器（构建与校验共用；纯标准库，不 import btf）
    export_rules_mirror.py    规则镜像导出（四方 ④）
    align_mdcore.py / align_v77.py          口径对齐抽检
    run_qidian_rerun.py / run_v77_robustness.py  批次复跑脚本（有对应测试锚定）

探针（`probes/` 子目录，CLI 审查报告-20260930 P3 归档）：
    一次性性能/等价性探针（probe_layout / probe_sort_equiv / probe_sorted /
    profile_b1 / profile_b1_breakdown）——**不被任何代码 import**、不进门禁，
    留档备查；结论已写进代码注释与 benchmarks/README.md，故不保证随版本可跑。
"""
