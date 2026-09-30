# -*- coding: utf-8 -*-
"""路径单一真源（阶段 0 任务 0.4；架构 ADR-4：禁止硬编码盘符）。

纪律（吸取现有 config.py:101 MARKET_DATA_DIR 硬编码教训——评审 E 系列与 ADR-4）：
    1. 全部路径由「包根锚点 + 环境变量覆盖」派生，本模块零硬编码盘符；
    2. 环境变量优先级：BTF_* 显式设置 > 包根同级约定 > 默认值；
    3. 中文路径全链路 UTF-8（调用方 open() 必须 encoding="utf-8"）；
    4. 主库目录只读（写入=启动失败级违例，运行时守卫在 data 层 Feed 内实现）。

环境变量清单：
    BTF_DATA_DIR     主库根（默认 D:/全量数据/market_data —— 经 default_from_env 之外
                     的「探测式默认」取得，见 _default_market_data；可指向任意挂载点）
    BTF_ALT_DATA_DIR akshare 备库根（v0.5 接入用）
    BTF_OUTPUT_DIR   回测产物根（默认 包根/../回测产物）
    BTF_DATASETS_DIR 黄金/示例数据集根（默认 包根/btf_datasets）
"""
from __future__ import annotations

import os
from pathlib import Path

__all__ = [
    "ALT_DATA_DIR",
    "DATASETS_DIR",
    "MARKET_DATA_DIR",
    "OUTPUT_DIR",
    "PACKAGE_ROOT",
    "PROJECT_ROOT",
    "QIDIAN_REF",
    "RUNS_DIR",
]

# 包根（btf/ 上层 = 代码/btf/）；PROJECT_ROOT = 代码/（与现有工程同级约定）
PACKAGE_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = PACKAGE_ROOT.parent


def _from_env(name: str, default: Path) -> Path:
    """环境变量优先（ADR-4：支持覆盖与移植）；返回绝对化路径。"""
    val = os.environ.get(name)
    return Path(val).resolve() if val else default


def _default_market_data() -> Path:
    """主库默认位置：PROJECT_ROOT 同级盘符约定（D:/全量数据/market_data）。

    这里不硬编码 "D:"——从 PROJECT_ROOT 所在盘符派生（数据盘随项目盘走），
    BTF_DATA_DIR 环境变量可完全覆盖（重装/迁移场景）。
    """
    drive_root = Path(PROJECT_ROOT.anchor)  # anchor 是 str（如 "D:\\"），须包成 Path
    return drive_root / "全量数据" / "market_data"


MARKET_DATA_DIR = _from_env("BTF_DATA_DIR", _default_market_data())
ALT_DATA_DIR = _from_env(
    "BTF_ALT_DATA_DIR", Path(PROJECT_ROOT.anchor) / "全量数据" / "alt_data"
)

# 产物目录：默认 PROJECT_ROOT 同级「回测产物/」（06 §11.3 部署拓扑；绝不写主库）
OUTPUT_DIR = _from_env("BTF_OUTPUT_DIR", PROJECT_ROOT.parent / "回测产物")
RUNS_DIR = OUTPUT_DIR / "runs"

# 黄金/示例数据集（05 §20.1 三层数据集体系的落盘位）
DATASETS_DIR = _from_env("BTF_DATASETS_DIR", PACKAGE_ROOT / "btf_datasets")

# 奇点战法信号层真源（M3 6.3；赤潮侧 `代码/strategies/qidian.py`）——标的池与
# 阈值常量**运行时读取**，不在 btf 复制清单。BTF_QIDIAN_REF 可覆盖。
QIDIAN_REF = _from_env(
    "BTF_QIDIAN_REF", PROJECT_ROOT.parent / "strategies" / "qidian.py"
)
