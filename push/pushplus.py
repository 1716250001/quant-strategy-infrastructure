# -*- coding: utf-8 -*-
"""
push/pushplus.py — PushPlus 微信推送模块
=========================================
基于 PushPlus 官方 API (V1.16) 实现
从原 pushplus_pusher.py 迁移，调整为从 config.py 统一读取 token。

核心能力:
  - 微信公众号推送 (channel=wechat, 默认)
  - 微信ClawBot推送 (channel=clawbot)
  - markdown/txt/html/json 模板
  - 频率控制 (200条/天, 5条/分钟, 相同内容3条/小时)
  - 信号聚合 (多条信号合并为一条推送, 省额度)
  - 冷却去重 (同一信号N分钟内不重复推送)
  - 当日额度用完自动停止 (返回码900)
"""

import hashlib
import json
import logging
import os
import time
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import requests

from common.jsonio import load_json, save_json
from config import PUSHPLUS_TOKEN, PUSHPLUS_CHANNEL, PUSHPLUS_TEMPLATE, DATA_DIR

logger = logging.getLogger(__name__)

# ============================================================
# 常量
# ============================================================

PUSHPLUS_URL = "http://www.pushplus.plus/send"
PUSHPLUS_BATCH_URL = "http://www.pushplus.plus/batchSend"

# 免费版限制
DAILY_LIMIT = 200          # 每日请求上限
MINUTE_LIMIT = 5           # 每分钟请求上限
SAME_CONTENT_HOURLY = 3    # 相同内容每小时上限
CONTENT_MAX_LEN = 20000    # 内容最大长度
TITLE_MAX_LEN = 100         # 标题最大长度

# 返回码
CODE_SUCCESS = 200
CODE_RATE_LIMITED = 900     # 当日额度用完或被封，必须停止
CODE_NOT_VERIFIED = 905     # 未实名认证
CODE_INVALID_TOKEN = 903    # token无效


class PushPlusError(Exception):
    """PushPlus 推送异常"""
    pass


class DailyLimitExceeded(PushPlusError):
    """当日额度用完"""
    pass


class PushPlus:
    """
    PushPlus 推送客户端

    参数:
        token: PushPlus 用户token或消息token
        channel: 默认发送渠道 (wechat/clawbot/extension/app/mail/cp)
        template: 默认消息模板 (txt/markdown/html/json)
        cooldown_minutes: 同一信号冷却时间(分钟)，默认15分钟
        state_file: 状态文件路径，用于持久化计数器
    """

    def __init__(
        self,
        token: str = None,
        channel: str = None,
        template: str = None,
        cooldown_minutes: int = 15,
        state_file: Optional[str] = None,
    ):
        self.token = token or PUSHPLUS_TOKEN
        self.channel = channel or PUSHPLUS_CHANNEL
        self.template = template or PUSHPLUS_TEMPLATE
        self.cooldown_seconds = cooldown_minutes * 60

        # 状态文件（持久化计数器，跨进程共享）
        self.state_file = state_file or os.path.join(DATA_DIR, ".pushplus_state.json")

        # 运行时状态
        self._state = self._load_state()

        # 请求历史（用于分钟级频率控制）
        self._recent_sends: List[float] = []  # 最近发送的时间戳列表
        self._content_hashes: Dict[str, List[float]] = {}  # content_hash -> [timestamps]

        # 当日是否已被限制
        self._blocked_today = self._state.get("blocked_date") == date.today().isoformat()

        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    # ============================================================
    # 状态持久化
    # ============================================================

    def _load_state(self) -> Dict:
        """加载持久化状态（委托 common.jsonio，失败时返回空 dict）"""
        data = load_json(self.state_file, default=None)
        return data if isinstance(data, dict) else {}

    def _save_state(self):
        """保存持久化状态（委托 common.jsonio，自动建父目录）"""
        if not save_json(self.state_file, self._state):
            logger.warning(f"保存PushPlus状态失败: {self.state_file}")

    def _get_daily_count(self) -> int:
        """获取今日已发送次数"""
        today = date.today().isoformat()
        if self._state.get("count_date") != today:
            self._state["count_date"] = today
            self._state["daily_count"] = 0
            self._state["blocked_date"] = None
            self._blocked_today = False
        return self._state.get("daily_count", 0)

    def _increment_daily_count(self):
        """增加今日计数"""
        self._state["daily_count"] = self._get_daily_count() + 1
        self._save_state()

    # ============================================================
    # 频率控制
    # ============================================================

    def _wait_for_minute_limit(self):
        """确保不超过每分钟5次"""
        now = time.time()
        self._recent_sends = [t for t in self._recent_sends if now - t < 60]
        if len(self._recent_sends) >= MINUTE_LIMIT:
            wait = 60 - (now - self._recent_sends[0]) + 1
            logger.info(f"触发每分钟限制，等待{wait:.0f}秒...")
            time.sleep(wait)
            now = time.time()
            self._recent_sends = [t for t in self._recent_sends if now - t < 60]

    def _check_same_content(self, content: str) -> bool:
        """
        检查相同内容是否超过每小时3次
        返回True表示可以发送，False表示被跳过
        """
        now = time.time()
        content_hash = hashlib.md5(content.encode("utf-8")).hexdigest()

        if content_hash not in self._content_hashes:
            self._content_hashes[content_hash] = []

        self._content_hashes[content_hash] = [
            t for t in self._content_hashes[content_hash] if now - t < 3600
        ]

        if len(self._content_hashes[content_hash]) >= SAME_CONTENT_HOURLY:
            logger.warning(f"相同内容每小时上限({SAME_CONTENT_HOURLY}条)，跳过")
            return False

        self._content_hashes[content_hash].append(now)
        return True

    def _check_limits(self) -> bool:
        """检查是否可以发送（额度+频率）"""
        if self._blocked_today:
            logger.warning("今日PushPlus额度已用完或被封，停止发送")
            return False

        if self._get_daily_count() >= DAILY_LIMIT:
            logger.warning(f"今日已发送{self._get_daily_count()}条，达到上限{DAILY_LIMIT}")
            return False

        return True

    # ============================================================
    # 发送核心
    # ============================================================

    def _send_raw(
        self,
        title: str,
        content: str,
        template: Optional[str] = None,
        channel: Optional[str] = None,
        topic: Optional[str] = None,
        **kwargs
    ) -> Dict:
        """
        原始发送方法

        返回:
            dict: {success: bool, code: int, msg: str, data: str}
        """
        template = template or self.template
        channel = channel or self.channel

        # 截断
        if len(title) > TITLE_MAX_LEN:
            title = title[:TITLE_MAX_LEN]
        if len(content) > CONTENT_MAX_LEN:
            content = content[:CONTENT_MAX_LEN]

        # 前置检查
        if not self._check_limits():
            return {"success": False, "code": -1, "msg": "额度或频率限制", "data": None}

        # 相同内容去重
        if not self._check_same_content(content):
            return {"success": False, "code": -1, "msg": "相同内容每小时上限", "data": None}

        # 分钟级频率控制
        self._wait_for_minute_limit()

        # 构造请求
        payload = {
            "token": self.token,
            "title": title,
            "content": content,
            "template": template,
            "channel": channel,
        }
        if topic:
            payload["topic"] = topic
        payload.update(kwargs)

        try:
            resp = self.session.post(PUSHPLUS_URL, json=payload, timeout=15)
            result = resp.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"PushPlus请求失败: {e}")
            return {"success": False, "code": -1, "msg": str(e), "data": None}
        except json.JSONDecodeError as e:
            logger.error(f"PushPlus响应解析失败: {e}")
            return {"success": False, "code": -1, "msg": "响应解析失败", "data": None}

        code = result.get("code", -1)
        msg = result.get("msg", "")
        data = result.get("data")

        # 处理返回码
        if code == CODE_SUCCESS:
            self._recent_sends.append(time.time())
            self._increment_daily_count()
            logger.info(f"PushPlus发送成功: {msg}, 流水号={data}")
            return {"success": True, "code": code, "msg": msg, "data": data}

        elif code == CODE_RATE_LIMITED:
            self._blocked_today = True
            self._state["blocked_date"] = date.today().isoformat()
            self._save_state()
            logger.error(f"PushPlus当日额度用完: {msg}")
            return {"success": False, "code": code, "msg": msg, "data": data}

        elif code == CODE_NOT_VERIFIED:
            logger.error(f"PushPlus未实名认证: {msg}")
            return {"success": False, "code": code, "msg": msg, "data": data}

        elif code == CODE_INVALID_TOKEN:
            logger.error(f"PushPlus token无效: {msg}")
            return {"success": False, "code": code, "msg": msg, "data": data}

        else:
            logger.error(f"PushPlus发送失败 code={code}: {msg}")
            return {"success": False, "code": code, "msg": msg, "data": data}

    # ============================================================
    # 便捷方法
    # ============================================================

    def send(
        self,
        title: str,
        content: str,
        template: Optional[str] = None,
        channel: Optional[str] = None,
    ) -> Dict:
        """
        发送消息（使用默认渠道和模板）

        参数:
            title: 消息标题（最多100字）
            content: 消息内容（最多2万字）
            template: 消息模板 (txt/markdown/html/json)，默认使用初始化时的模板
            channel: 发送渠道 (wechat/clawbot/extension/app/mail/cp)，默认使用初始化时的渠道

        返回:
            dict: {success, code, msg, data}
        """
        return self._send_raw(title, content, template, channel)

    def send_text(self, content: str, title: str = "通知") -> Dict:
        """发送纯文本消息"""
        return self._send_raw(title, content, template="txt")

    def send_markdown(self, content: str, title: str = "通知") -> Dict:
        """发送markdown消息"""
        return self._send_raw(title, content, template="markdown")

    def send_html(self, content: str, title: str = "通知") -> Dict:
        """发送html消息"""
        return self._send_raw(title, content, template="html")

    # ============================================================
    # 信号聚合推送
    # ============================================================

    def send_signals(
        self,
        signals: List[Dict[str, Any]],
        title_prefix: str = "交易信号",
    ) -> Dict:
        """
        聚合多条信号为一条推送（省额度）

        参数:
            signals: 信号列表，每个信号是一个dict，格式:
                {
                    "symbol": "上证指数",
                    "type": "顶背离",        # 信号类型
                    "period": "30分钟",       # 周期
                    "detail": "DIF从24.7降至7.8...",  # 详细描述
                    "level": "强",           # 信号强度
                }
            title_prefix: 标题前缀

        返回:
            dict: {success, code, msg, data}
        """
        if not signals:
            return {"success": False, "code": -1, "msg": "无信号", "data": None}

        # 冷却去重
        signal_key = self._make_signal_key(signals)
        if not self._check_cooldown(signal_key):
            return {"success": False, "code": -1, "msg": "冷却中，跳过", "data": None}

        # 格式化为markdown
        content = self._format_signals_markdown(signals)

        # 标题
        n = len(signals)
        types = set(s.get("type", "") for s in signals)
        title = f"[{title_prefix}] {n}条信号: {','.join(sorted(types))}"

        result = self._send_raw(title, content, template="markdown")

        if result.get("success"):
            self._update_cooldown(signal_key)

        return result

    def _make_signal_key(self, signals: List[Dict]) -> str:
        """生成信号唯一标识（用于冷却去重）"""
        parts = []
        for s in signals:
            parts.append(f'{s.get("symbol","")}:{s.get("period","")}:{s.get("type","")}')
        return "|".join(sorted(parts))

    def _check_cooldown(self, key: str) -> bool:
        """检查冷却状态"""
        cooldowns = self._state.setdefault("cooldowns", {})
        now = time.time()
        if key in cooldowns:
            elapsed = now - cooldowns[key]
            if elapsed < self.cooldown_seconds:
                remaining = (self.cooldown_seconds - elapsed) / 60
                logger.info(f"信号冷却中，剩余{remaining:.1f}分钟: {key}")
                return False
        return True

    def _update_cooldown(self, key: str):
        """更新冷却时间"""
        self._state.setdefault("cooldowns", {})[key] = time.time()
        now = time.time()
        expired = [k for k, v in self._state["cooldowns"].items()
                   if now - v > self.cooldown_seconds * 2]
        for k in expired:
            del self._state["cooldowns"][k]
        self._save_state()

    @staticmethod
    def _format_signals_markdown(signals: List[Dict]) -> str:
        """将信号列表格式化为markdown内容"""
        lines = []
        lines.append(f"**共{len(signals)}条信号**\n")

        by_type: Dict[str, List[Dict]] = {}
        for s in signals:
            t = s.get("type", "其他")
            by_type.setdefault(t, []).append(s)

        for sig_type, sigs in by_type.items():
            level = sigs[0].get("level", "")
            level_emoji = {"强": "!!", "中": "!", "弱": ""}.get(level, "")
            lines.append(f"### {sig_type} {level_emoji}\n")

            for s in sigs:
                lines.append(f"- **{s.get('symbol', '')}** ({s.get('period', '')})")
                detail = s.get("detail", "")
                if detail:
                    lines.append(f"  {detail}")
                lines.append("")

        lines.append(f"\n---\n推送时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        return "\n".join(lines)

    # ============================================================
    # 工具方法
    # ============================================================

    def get_status(self) -> Dict:
        """获取当前状态"""
        return {
            "daily_count": self._get_daily_count(),
            "daily_limit": DAILY_LIMIT,
            "daily_remaining": DAILY_LIMIT - self._get_daily_count(),
            "blocked_today": self._blocked_today,
            "cooldowns": len(self._state.get("cooldowns", {})),
        }

    def test(self) -> Dict:
        """发送测试消息"""
        content = f"PushPlus测试消息\n\n时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n渠道: {self.channel}\n模板: {self.template}"
        return self._send_raw("PushPlus测试", content, template="markdown")


# ============================================================
# CLI入口
# ============================================================

def run_push(token=None, channel=None, template=None, title="通知",
             content="", test=False):
    """发送推送并打印配额状态（供本文件 CLI 与统一入口 main.py 的 push-test 子命令共用）。

    参数:
        token/channel/template: None 时取 config 中的默认值
        content: 消息内容；test=True 时可不填
        test: 发送测试消息
    返回:
        PushPlus 接口返回的 dict
    """
    token = token or PUSHPLUS_TOKEN
    if not token:
        raise ValueError("未提供token且config.py中无PUSHPLUS_TOKEN配置")
    channel = channel or PUSHPLUS_CHANNEL
    template = template or PUSHPLUS_TEMPLATE

    pp = PushPlus(token=token, channel=channel, template=template)

    if test:
        result = pp.test()
    elif content:
        result = pp.send(title=title, content=content)
    else:
        raise ValueError("请提供 content 或 test=True")

    print(json.dumps(result, ensure_ascii=False, indent=2))

    status = pp.get_status()
    print(f"\n今日已发送: {status['daily_count']}/{status['daily_limit']}")
    return result


def main():
    import argparse

    parser = argparse.ArgumentParser(description="PushPlus微信推送")
    parser.add_argument("--token", default=PUSHPLUS_TOKEN,
                        help="PushPlus token (默认从config.py读取)")
    parser.add_argument("--channel", default=PUSHPLUS_CHANNEL, help="发送渠道 (wechat/clawbot)")
    parser.add_argument("--template", default=PUSHPLUS_TEMPLATE, help="消息模板 (txt/markdown/html)")
    parser.add_argument("--title", default="通知", help="消息标题")
    parser.add_argument("--content", default="", help="消息内容 (--test时可不填)")
    parser.add_argument("--test", action="store_true", help="发送测试消息")
    args = parser.parse_args()

    if not args.token:
        parser.error("未提供token且config.py中无PUSHPLUS_TOKEN配置")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    run_push(token=args.token, channel=args.channel, template=args.template,
             title=args.title, content=args.content, test=args.test)


if __name__ == "__main__":
    main()
