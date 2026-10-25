# -*- coding: utf-8 -*-
"""examples 包标记（MM-5，19 号 §135.2——N15 闭环·方案 a）。

使 `examples.rotation` 等示例策略可随 btf 安装被 `module:Class` 点路径装载
（`bt run --config examples/config_rotation.yaml`），**不依赖 CWD 也不做任何
sys.path 注入**（禁止方案 b 的导入期路径注入形态）。
"""
