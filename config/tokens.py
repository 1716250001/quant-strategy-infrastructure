# -*- coding: utf-8 -*-
"""config.tokens — API 凭据 + PushPlus 推送配置（W3b 起独立成文件）。

凭据读取优先级：环境变量 > 代码目录 .env > 空串；明文不入库。
"""
import os

from config.paths import CODE_DIR


# ============================================================
# 1. API Token — 全局唯一（G2 · 2026-09-25 环境变量化）
#    读取优先级：环境变量 > 代码目录 .env 文件 > 空串。
#    明文已从代码移除；本地值存放于 D:\量化策略\代码\.env（勿外发/勿同步云端）。
#    重装恢复法：.env 随 D 盘走，或按 .env.example 手动重设三枚系统环境变量。
# ============================================================
def _secret(name: str) -> str:
    """凭据读取：优先环境变量，其次代码目录 .env（KEY=VALUE 每行一条，# 注释）。"""
    val = os.environ.get(name, "").strip()
    if val:
        return val
    try:
        env_path = os.path.join(CODE_DIR, ".env")
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == name:
                    return v.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


TS_TOKEN = _secret("TS_TOKEN")          # Tushare（2000 积分档；2026-09-24 老大提供重填，09-25 G2 移入 .env）
PUSHPLUS_TOKEN = _secret("PUSHPLUS_TOKEN")  # PushPlus 微信推送（同上）
WIND_API_KEY = _secret("WIND_API_KEY")  # Wind AIFin（ak_ 前缀；同上）。⚠ 2026-09-25 Wind 研究暂停、代码剥离至 _scratch\wind-研究暂存\——本 key 仅保留供暂存脚本环境变量续用（暂存脚本均直读环境变量，不 import 本常量）

# ============================================================
# 1b. PushPlus 微信推送配置
# ============================================================
PUSHPLUS_CHANNEL = "wechat"       # 默认渠道: wechat(微信公众号)
PUSHPLUS_TEMPLATE = "markdown"    # 默认模板: markdown

