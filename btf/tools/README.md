# -*- coding: utf-8 -*-
"""开发工具目录（工具脚本不进 btf 包：核心包 diff 为零纪律）。

常驻工具（可重复跑；多数已是 `bt` 子命令的**单一真源**，CLI 只转发不复制判定）：
    check.py                  十项架构门禁（`bt check` 委托本脚本；**逐项日志落盘** + 第 3 项冷启动复跑标注，N4）
    deps_budget.py            依赖预算 ≤8（门禁 4/9）
    check_domain_whitelist.py domain 白名单（门禁 6/9）
    check_report_render.py    报告渲染级结构（门禁 7/9）
    check_data_quality.py     数据不变量（门禁 8/9）
    check_io_boundary.py      IO 单一入口（门禁 9/10）
    check_plugin_boundary.py  插件边界基线（**自 GG-6② 起为 `bt check` 第 10 项**；AA-3 显式刷新留痕）
    check_rules_four_way.py   规则四方机检（**不在十项内**：需赤潮源码树）
    cold_backup.py            跨版本源码冷备（`bt cold-backup` 委托）
    build_golden.py           黄金集构建/对账/重算（`bt dataset` 委托）
    golden_refcalc.py         独立参考计算器（构建与校验共用；纯标准库，不 import btf）
    export_rules_mirror.py    规则镜像导出（四方 ④）
    align_mdcore.py / align_v77.py          口径对齐抽检
    run_qidian_rerun.py / run_v77_robustness.py  批次复跑脚本（有对应测试锚定）
    run_l2_ablation.py        L2 选股消融矩阵（7 臂 × 区间）；**HH-2 起 A5/A6 负控制
                              固化为例行对照入口**（`--a56`；每次运行输出对照块 vs
                              2026-10-01 冻结基线）；成本口径**显式 preRC**（GG-1 纪律）
    adhoc_run.py              一次性/外部回测脚本的**产物契约封装器**（HH-3 起收编为
                              `bt adhoc` 子命令；执行差分登记 / register-only / 登记台）
    bare_env_check.py         **独立环境裸验证（C-01，2026-10-11 三轮架构审查）**：
                              临时独立 venv 按两份 requirements 重建 → 真入口
                              `--version` → pytest 子集；把"依赖预算 ≤8"从文档层
                              约束升级为**环境层实测**（双工程共享 `代码/_venv` ⇒
                              隐性依赖在本机测不出）。`--plan` 零副作用预览；
                              **节奏**＝每季度或改动 requirements 后**必跑**
                              （不自动进 `bt check`——建 venv＋联网，非秒级门禁）。
                              实测（2026-10-11）：独立 venv → 底座 28 包 ＋ btf 9 包
                              → `btf 1.3.1`（真入口）→ `tests/unit` 全绿。

探针（`probes/` 子目录，CLI 审查报告-20260930 P3 归档）：
    一次性性能/等价性探针（probe_layout / probe_sort_equiv / probe_sorted /
    profile_b1 / profile_b1_breakdown）——**不被任何代码 import**、不进门禁，
    留档备查；结论已写进代码注释与 benchmarks/README.md，故不保证随版本可跑。
"""
