"""共享规则匹配引擎 — 三模式匹配

参考 XHS-YYDS 的规则引擎设计，三平台私信自动回复共用。

规则格式:
    {
        "keywords": "价格,多少钱,怎么卖",   # 逗号分隔关键词
        "match_mode": "contains",            # contains | exact | regex
        "reply_text": "您好，请查看商品详情",  # 单条文本回复
        "reply_texts": ["回复1", "回复2"],     # 多条随机选取（优先于 reply_text）
        "reply_image": "data/cards/wechat.png",  # 图片回复（与 text 可组合）
        "reply_text_after": "扫码加我~",         # 图片发完后再发的文字（可选）
        "is_default": false,                  # 是否为默认回复（无匹配时触发）
    }
"""
from __future__ import annotations

import random
import re
import logging

logger = logging.getLogger(__name__)


def match_rule(message: str, rules: list[dict]) -> str | None:
    """根据规则列表匹配消息，返回回复文本（向后兼容，仅返回 text）"""
    action = match_rule_action(message, rules)
    return action.get("text") if action else None


def match_rule_action(
    message: str,
    rules: list[dict],
    mode: str = "keyword",
    brainless_replies: list[str] | None = None,
    keyword_enabled: bool | None = None,
    brainless_enabled: bool | None = None,
) -> dict | None:
    """根据规则列表匹配消息，返回完整动作字典

    优先级：keyword_enabled 命中 > brainless_enabled 兜底 > None

    Args:
        message: 用户发送的消息
        rules: 关键词规则列表
        keyword_enabled: True 启用关键词匹配
        brainless_enabled: True 启用无脑兜底
        brainless_replies: 无脑回复文案池
        mode: 旧参数（兼容），"keyword" 等同 keyword_enabled=True，
              "brainless" 等同 brainless_enabled=True（都不启用关键词）

    Returns:
        {"text", "image", "text_after"} 或 None（都不命中）
    """
    # 兼容旧参数：mode="brainless" → 只启用无脑
    if keyword_enabled is None and brainless_enabled is None:
        if mode == "brainless":
            keyword_enabled, brainless_enabled = False, True
        else:
            keyword_enabled, brainless_enabled = True, False

    if not message:
        return None

    # 1. 关键词匹配（优先级最高）
    if keyword_enabled and rules:
        action = _match_keyword(message, rules)
        if action:
            action["strategy"] = "keyword"
            return action

    # 2. 无脑兜底
    if brainless_enabled:
        texts = [t for t in (brainless_replies or []) if t]
        if texts:
            return {"text": random.choice(texts), "image": "", "text_after": "", "strategy": "brainless"}

    # 两者都没命中
    return None


def _match_keyword(message: str, rules: list[dict]) -> dict | None:
    """关键词匹配（内部函数）"""
    if not message or not rules:
        return None

    msg_lower = message.strip().lower()
    default_action = None

    for rule in rules:
        # 默认回复规则（先记下来，等遍历完如果没有 matched 再用）
        if rule.get("is_default"):
            default_action = _build_action(rule)
            continue

        match_mode = rule.get("match_mode", "contains")
        keywords_str = rule.get("keywords", "")
        if not keywords_str:
            continue

        keywords = [kw.strip() for kw in re.split(r'[,，]', keywords_str) if kw.strip()]

        for kw in keywords:
            matched = False

            if match_mode == "exact":
                matched = kw.lower() == msg_lower
            elif match_mode == "regex":
                try:
                    matched = bool(re.search(kw, message, re.IGNORECASE))
                except re.error:
                    logger.warning("Invalid regex pattern: %s", kw)
                    continue
            else:  # contains
                matched = kw.lower() in msg_lower

            if matched:
                return _build_action(rule)

    return default_action


def _pick_reply(rule: dict) -> str:
    """从规则中选取文本回复（支持多条随机）"""
    texts = rule.get("reply_texts")
    if texts and isinstance(texts, list) and len(texts) > 0:
        return random.choice(texts)
    return rule.get("reply_text", "")


def _build_action(rule: dict) -> dict | None:
    """把 rule 打包成动作字典。若 text/image 全空，返回 None"""
    text = _pick_reply(rule)
    image = (rule.get("reply_image") or "").strip()
    text_after = (rule.get("reply_text_after") or "").strip()
    if not text and not image:
        return None
    return {
        "text": text,
        "image": image,
        "text_after": text_after,
    }
