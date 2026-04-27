"""AI 素材生成器 — OpenAI 兼容接口

支持任意 OpenAI 兼容 API（通义千问/GPT/DeepSeek/Moonshot 等）。
配置存在系统设置 DB 中（key=ai_content）。
"""
import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen-plus"

PLATFORM_PROMPTS = {
    "xianyu": "你是一位闲鱼卖货高手。请根据用户提供的信息，生成闲鱼商品文案。标题要简洁有吸引力（≤30字），描述要真实接地气，突出性价比和成色。",
    "douyin": "你是一位抖音内容创作者。请根据用户提供的信息，生成抖音图文笔记文案。标题要有吸引力和话题性（≤20字），描述适合年轻人阅读，带节奏感。",
    "xiaohongshu": "你是一位小红书博主。请根据用户提供的信息，生成小红书笔记文案。标题用emoji和感叹号增加吸引力（≤20字），描述要精致、有生活感。",
}

GENERATE_SYSTEM = """你是一位专业的社交电商文案专家。请根据用户的需求生成发布文案。

输出格式（严格 JSON）：
```json
{
  "items": [
    {
      "title": "标题",
      "description": "描述正文",
      "tags": ["标签1", "标签2", "标签3"]
    }
  ]
}
```

要求：
- 生成指定数量的文案变体
- 每条文案风格略有不同（口语/正式/幽默等）
- 标签 3-5 个，用于平台话题
- 不要输出 JSON 以外的内容
"""


class ContentGenerator:
    """AI 素材文案生成器"""

    def __init__(self, config: dict):
        self.api_base = config.get("api_base", DEFAULT_API_BASE).rstrip("/")
        self.api_key = config.get("api_key", "")
        self.model = config.get("model", DEFAULT_MODEL)
        self.temperature = config.get("temperature", 0.8)

    async def generate(
        self,
        prompt: str,
        platform: str = "",
        count: int = 3,
        extra_context: str = "",
    ) -> list[dict[str, Any]]:
        """生成文案

        Args:
            prompt: 用户输入（如"iPhone 15 二手 95新"）
            platform: 目标平台（xianyu/douyin/xiaohongshu）
            count: 生成数量
            extra_context: 额外上下文（如图片描述）

        Returns: [{"title": ..., "description": ..., "tags": [...]}, ...]
        """
        if not self.api_key:
            raise ValueError("AI API Key 未配置，请在系统设置中配置")

        platform_hint = PLATFORM_PROMPTS.get(platform, "请生成通用社交平台发布文案。")
        system = GENERATE_SYSTEM + "\n\n" + platform_hint

        user_msg = f"请生成 {count} 条文案变体。\n\n用户需求：{prompt}"
        if extra_context:
            user_msg += f"\n\n补充信息：{extra_context}"

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ]

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self.api_base}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": messages,
                        "max_tokens": 2000,
                        "temperature": self.temperature,
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            content = data["choices"][0]["message"]["content"].strip()
            # 提取 JSON
            items = self._parse_response(content)
            logger.info("[ai_content] Generated %d items for '%s'", len(items), prompt[:30])
            return items

        except httpx.HTTPStatusError as e:
            logger.error("[ai_content] API error %s: %s", e.response.status_code, e.response.text[:200])
            raise RuntimeError(f"AI API 调用失败: {e.response.status_code}")
        except Exception as e:
            logger.error("[ai_content] Generate failed: %s", e)
            raise

    def _parse_response(self, content: str) -> list[dict]:
        """解析 AI 返回的 JSON"""
        # 尝试直接解析
        try:
            data = json.loads(content)
            if isinstance(data, dict) and "items" in data:
                return data["items"]
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

        # 提取 ```json ... ``` 块
        import re
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
        if match:
            try:
                data = json.loads(match.group(1))
                if isinstance(data, dict) and "items" in data:
                    return data["items"]
                if isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                pass

        # 提取 { ... } 或 [ ... ]
        for pattern in [r'\{[\s\S]*\}', r'\[[\s\S]*\]']:
            match = re.search(pattern, content)
            if match:
                try:
                    data = json.loads(match.group(0))
                    if isinstance(data, dict) and "items" in data:
                        return data["items"]
                    if isinstance(data, list):
                        return data
                    return [data]
                except json.JSONDecodeError:
                    continue

        raise ValueError(f"无法解析 AI 返回内容: {content[:200]}")


def load_generator() -> ContentGenerator | None:
    """从系统配置加载生成器"""
    try:
        from apps.api.routes.settings import get_qiniu_config_from_db
        from infrastructure.db.session import SessionLocal
        from apps.api.routes.settings import _get_sys_config

        db = SessionLocal()
        try:
            cfg = _get_sys_config(db, "ai_content")
            if cfg and cfg.get("api_key"):
                return ContentGenerator(cfg)
        finally:
            db.close()
    except Exception as e:
        logger.warning("[ai_content] Load config failed: %s", e)
    return None
