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
    BTF_OUTPUT_DIR   回测产物根（默认 工作区根/回测产物 = 量化策略/回测产物）
    BTF_DATASETS_DIR 黄金/示例数据集根（默认 包根/btf_datasets）
"""
from __future__ import annotations

import os
from pathlib import Path

__all__ = [
    "ALT_DATA_DIR",
    "CORP_ACTION_DIR",
    "DATASETS_DIR",
    "FREEZE_DIR",
    "MARKET_DATA_DIR",
    "OUTPUT_DIR",
    "PACKAGE_ROOT",
    "PROJECT_ROOT",
    "QIDIAN_REF",
    "RUNS_DIR",
    "WORKSPACE_ROOT",
]

# 包根（btf/ 上层 = 代码/btf/）；PROJECT_ROOT = 代码/（与现有工程同级约定）
PACKAGE_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = PACKAGE_ROOT.parent
# 工作区根（PROJECT_ROOT=代码/btf 的上两层 = 量化策略/）：产物与验收文档的
# 统一落盘锚点（引擎 runs 与专项验证产物同根，避免「回测产物」两处分叉）
WORKSPACE_ROOT = PROJECT_ROOT.parent.parent


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

# 产物目录：默认 工作区根「回测产物/」=（D:/量化策略/回测产物，06 §11.3 部署拓扑；
# 绝不写主库）。单一落盘点：引擎 runs/ 与 tools/run_v77_robustness.py 的
# v77-robustness/ 同处一个根，避免仓库内外两套「回测产物」分叉。
OUTPUT_DIR = _from_env("BTF_OUTPUT_DIR", WORKSPACE_ROOT / "回测产物")
RUNS_DIR = OUTPUT_DIR / "runs"

# 黄金/示例数据集（05 §20.1 三层数据集体系的落盘位）
DATASETS_DIR = _from_env("BTF_DATASETS_DIR", PACKAGE_ROOT / "btf_datasets")

# 公司行动离线清单根（**CL-2 第二段 R1′/R2′**；19 号 §79.4）：配股/缩股事件
# 清单（JSON，含来源哈希与校验统计）——由 `python -m btf.data.corp_action_probe`
# 生成，feed 侧 adapter 读取（**不写主库** `market_data`；含 IRREVERSIBLE 无关）。
#: ⚠ 变量名**必须** `BTFTOOL_` 前缀（**N7**，19 号 §69.4）：`BTF_` 前缀会被
#: 配置层当覆盖键注入（`BTF_CORP_ACTION_DIR` ⇒ 顶层 `corp_action_dir` 非法键，
#: 一设即**全平台配置校验崩**——本批实测踩中一次，见 18 号 §九·X）。
CORP_ACTION_DIR = _from_env("BTFTOOL_CORP_ACTION_DIR",
                            PACKAGE_ROOT / "btf_datasets" / "corp_actions")

# 数据冻结清单根（**CL-4**，19 号 §72.5 S-2）：**项目内只读目录**——每回测落一份
# 「表清单 ＋ content_hash ＋ anchor_date」不可变清单（`bt dataset freeze`）。
# ⚠ S-2 固有风险：**依赖原始库不被改写**——有效边界与原始库变更留痕要求见
# 08 号（数据）与 20 号（使用纪律）。`BTFTOOL_FREEZE_DIR` 可覆盖（**N7 前缀纪律**，
# 19 号 §69.4：`BTF_` 前缀会被配置层当覆盖键注入 ⇒ 校验崩）。
FREEZE_DIR = _from_env("BTFTOOL_FREEZE_DIR", PROJECT_ROOT / "_freeze")

# 奇点战法信号层真源（M3 6.3；赤潮侧 `代码/strategies/qidian.py`）——标的池与
# 阈值常量**运行时读取**，不在 btf 复制清单。BTF_QIDIAN_REF 可覆盖。
QIDIAN_REF = _from_env(
    "BTF_QIDIAN_REF", PROJECT_ROOT.parent / "strategies" / "qidian.py"
)
