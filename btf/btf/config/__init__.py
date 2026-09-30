# -*- coding: utf-8 -*-
"""C 配置层：分层配置合并 + JSON Schema 校验。

契约（ADR-4 + 06 §11.5）：
    四层合并（后覆盖先）：内置默认 → 环境变量 BTF_* → 用户 YAML → CLI 参数
    Schema 校验（jsonschema）：校验后冻结为只读 Mapping
    敏感项脱敏（secret 字段 → manifest 落盘 ***）
    路径配置支持 ${BTF_DATA_DIR} 环境变量插值——禁止硬编码盘符
    热加载边界：仅 report/viz 可热加载；引擎语义配置运行期不可变
    rules 段：规则参数字面量（未声明 override:true）→ Schema 拒绝（H2）

paths 子模块（0.4 任务）：全部路径经环境变量派生的单一真源。
"""
