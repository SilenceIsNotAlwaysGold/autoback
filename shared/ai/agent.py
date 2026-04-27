"""共享 AI 回复代理 — 三平台通用

支持多专家路由：议价专家、技术专家、通用客服
可选接入：OpenAI 兼容 API（通义千问/GPT/DeepSeek 等）

从 platforms/xianyu/ai_agent.py 提取核心逻辑，去掉闲鱼专属部分，
让抖音、小红书也能接入 AI 自动回复。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# 默认 LLM 配置（OpenAI 兼容接口）
DEFAULT_API_BASE = os.getenv("LLM_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
DEFAULT_API_KEY = os.getenv("LLM_API_KEY", "")
DEFAULT_MODEL = os.getenv("LLM_MODEL", "qwen-plus")


# ── 默认提示词模板（从 xianyu/ai_agent.py 移植，平台字段动态替换）────

CLASSIFY_PROMPT = """你是一个客服消息分类器。根据买家消息判断应该由哪个专家处理。

分类规则：
- "price": 涉及价格、议价、砍价、打折、优惠、包邮等
- "tech": 涉及商品技术问题、规格、参数、使用方法、质量、成色等
- "default": 其他所有消息（问候、物流、售后等）

只返回分类标签，不要解释。

买家消息：{message}
商品信息：{product_info}

分类："""

PRICE_EXPERT_PROMPT = """你是{platform}上一位经验丰富的卖家，正在和买家议价。

策略：
1. 态度友好但坚定，不轻易降价
2. 如果买家诚心想买，可以适当让步（最多降 {max_discount_percent}%）
3. 可以用包邮、赠品等方式替代直接降价
4. 底价是 {floor_price} 元，绝不低于底价
5. 回复简短自然，像真人聊天

商品信息：
- 标题：{product_title}
- 价格：{product_price} 元
- 描述：{product_desc}

对话历史：
{chat_history}

买家最新消息：{message}

你的回复："""

TECH_EXPERT_PROMPT = """你是{platform}上一位专业的卖家，正在回答买家关于商品的技术问题。

要求：
1. 基于商品信息如实回答，不夸大
2. 如果不确定，诚实说明
3. 回复专业但不啰嗦
4. 突出商品优势

商品信息：
- 标题：{product_title}
- 价格：{product_price} 元
- 描述：{product_desc}

对话历史：
{chat_history}

买家最新消息：{message}

你的回复："""

DEFAULT_EXPERT_PROMPT = """你是{platform}上一位友好的卖家，正在回复买家的消息。

要求：
1. 回复简短友好，像真人聊天
2. 积极引导买家下单
3. 如果买家问到不了解的问题，友好引导

商品信息：
- 标题：{product_title}
- 价格：{product_price} 元

对话历史：
{chat_history}

买家最新消息：{message}

你的回复："""

PLATFORM_NAMES = {
    "xianyu": "闲鱼",
    "douyin": "抖音",
    "xiaohongshu": "小红书",
}


class AIReplyAgent:
    """平台无关的 AI 回复代理"""

    def __init__(self, config: dict):
        """
        config keys:
          - api_base: str (e.g. "https://dashscope.aliyuncs.com/compatible-mode/v1")
          - api_key: str
          - model: str (default "qwen-plus")
          - max_discount_percent: int (default 10)
          - experts: list[str] (default ["price", "tech", "default"])
          - custom_prompts: dict (optional, override default prompts)
          - platform: str (xianyu/douyin/xiaohongshu, for context)
        """
        self.api_base = config.get("api_base", DEFAULT_API_BASE)
        self.api_key = config.get("api_key", DEFAULT_API_KEY)
        self.model = config.get("model", DEFAULT_MODEL)
        self.max_discount_percent = config.get("max_discount_percent", 10)
        self.experts = config.get("experts", ["price", "tech", "default"])
        self.platform = config.get("platform", "unknown")
        self.platform_name = PLATFORM_NAMES.get(self.platform, self.platform)

        # 提示词模板（支持自定义覆盖）
        self.prompts = {
            "classify": CLASSIFY_PROMPT,
            "price": PRICE_EXPERT_PROMPT,
            "tech": TECH_EXPERT_PROMPT,
            "default": DEFAULT_EXPERT_PROMPT,
        }
        if config.get("custom_prompts"):
            self.prompts.update(config["custom_prompts"])

        # 对话记忆
        from shared.conversation.memory import ConversationMemory
        self._memory = ConversationMemory(max_history=20, ttl_seconds=7200)

        # 商品信息缓存 {conversation_id: product_dict}
        self._product_cache: dict[str, dict] = {}

    def classify_intent(self, message: str) -> str:
        """快速关键词意图分类（不调用 LLM，用于 fallback 场景）"""
        msg = message.lower()
        if any(kw in msg for kw in ["价格", "多少钱", "便宜", "优惠", "打折", "包邮", "减", "降"]):
            return "price" if "price" in self.experts else "default"
        if any(kw in msg for kw in ["怎么用", "说明", "参数", "配置", "安装", "质量", "材质", "尺寸"]):
            return "tech" if "tech" in self.experts else "default"
        return "default"

    async def generate_reply(
        self,
        message: str,
        context: list[dict] | None = None,
        product_info: dict | None = None,
        conversation_id: str | None = None,
    ) -> str | None:
        """生成 AI 回复

        Args:
            message: 用户消息
            context: 对话上下文 [{"role": "user"/"assistant", "text": "..."}]
            product_info: 商品信息 {"title", "price", "description"}
            conversation_id: 会话 ID（用于缓存商品信息）

        Returns:
            AI 生成的回复文本，失败返回 None
        """
        if not self.api_key:
            return None

        # 缓存商品信息
        if conversation_id and product_info:
            self._product_cache[conversation_id] = product_info
        product = (self._product_cache.get(conversation_id, {}) if conversation_id else {}) or {}
        if product_info:
            product = product_info

        # 意图分类（使用快速关键词方式）
        expert_type = self.classify_intent(message)

        # 格式化对话历史
        chat_history = "（无历史对话）"
        if context:
            lines = []
            for msg in context[-6:]:
                role = "买家" if msg.get("role") == "user" else "你"
                lines.append(f"{role}：{msg.get('text', '')}")
            if lines:
                chat_history = "\n".join(lines)

        # 构建 prompt
        if expert_type == "price":
            price = float(product.get("price", 0) or 0)
            floor_price = price * (1 - self.max_discount_percent / 100)
            prompt = self.prompts["price"].format(
                message=message,
                platform=self.platform_name,
                product_title=product.get("title", ""),
                product_price=product.get("price", ""),
                product_desc=str(product.get("description", ""))[:200],
                floor_price=f"{floor_price:.0f}",
                max_discount_percent=self.max_discount_percent,
                chat_history=chat_history,
            )
        elif expert_type == "tech":
            prompt = self.prompts["tech"].format(
                message=message,
                platform=self.platform_name,
                product_title=product.get("title", ""),
                product_price=product.get("price", ""),
                product_desc=str(product.get("description", ""))[:500],
                chat_history=chat_history,
            )
        else:
            prompt = self.prompts["default"].format(
                message=message,
                platform=self.platform_name,
                product_title=product.get("title", ""),
                product_price=product.get("price", ""),
                chat_history=chat_history,
            )

        try:
            reply = await self._call_llm(prompt, max_tokens=200)
            logger.info("[ai_agent] %s/%s reply: %s → %s",
                        self.platform, expert_type, message[:30], reply[:30])
            return reply
        except Exception as e:
            logger.warning("[ai_agent] LLM call failed: %s", e)
            return None

    async def _call_llm(self, prompt: str, max_tokens: int = 200) -> str:
        """调用 LLM API（OpenAI 兼容格式）"""
        import httpx

        url = f"{self.api_base.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.7,
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()

    def set_product_info(self, conversation_id: str, product: dict):
        """设置会话关联的商品信息"""
        self._product_cache[conversation_id] = product

    def clear_conversation(self, conversation_id: str):
        """清除会话状态"""
        self._memory.clear_conversation(conversation_id)
        self._product_cache.pop(conversation_id, None)

    @property
    def active_conversations(self) -> int:
        return self._memory.active_conversations


def load_ai_agent(account: dict) -> AIReplyAgent | None:
    """从账号配置加载 AI agent（如果启用）

    账号 dict 需要包含:
      - ai_enabled: bool
      - ai_config: dict 或 JSON 字符串，包含 api_key 等配置
      - platform: str (xianyu/douyin/xiaohongshu)
    """
    if not account.get("ai_enabled"):
        return None
    ai_config = account.get("ai_config") or {}
    if isinstance(ai_config, str):
        try:
            ai_config = json.loads(ai_config)
        except Exception:
            return None
    if not ai_config.get("api_key"):
        return None
    ai_config.setdefault("platform", account.get("platform", "unknown"))
    return AIReplyAgent(ai_config)
