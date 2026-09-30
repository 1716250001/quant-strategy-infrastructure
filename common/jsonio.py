# -*- coding: utf-8 -*-
"""
common/jsonio.py — JSON 读写统一封装
=====================================
替换此前散落在 16 处的 open()+json.load/dump() 手写实现。

设计：
  - 读失败不抛异常，返回 default 并打印告警（调用方可判空）
  - 写自动创建父目录；default=str 兼容 numpy 标量等非原生类型
  - 显式提供 raises=True 时按严格模式抛异常
"""
import json
import os


def load_json(path, default=None, raises=False):
    """读取 JSON 文件。

    参数:
        path: 文件路径
        default: 文件不存在或解析失败时的返回值
        raises: True 时解析失败抛出异常（严格模式）
    返回:
        解析后的对象，或 default
    """
    if not path or not os.path.exists(path):
        if raises:
            raise FileNotFoundError(f"JSON 文件不存在: {path}")
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        if raises:
            raise
        print(f"  [WARN] JSON读取失败 {path}: {e}")
        return default


def save_json(path, obj, indent=2, ensure_ascii=False, default=str, raises=False):
    """写入 JSON 文件（自动创建父目录）。

    参数:
        default: 非原生类型的兜底转换器；默认 str（兼容 numpy 标量）
        raises: True 时写入失败抛出异常
    返回:
        True / False（是否写入成功）
    """
    try:
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=ensure_ascii, indent=indent, default=default)
        return True
    except Exception as e:
        if raises:
            raise
        print(f"  [WARN] JSON写入失败 {path}: {e}")
        return False
