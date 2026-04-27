"""轻量级对话记忆管理

参考 XianyuAutoAgent context_manager + XHS-YYDS 对话记忆。
三平台私信回复共用。

用法:
    memory = ConversationMemory(max_history=10)
    memory.add_message("conv_123", "user", "你好")
    memory.add_message("conv_123", "assistant", "你好，有什么可以帮您？")
    context = memory.get_context("conv_123")
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Message:
    role: str       # "user" | "assistant"
    text: str
    timestamp: float = field(default_factory=time.time)


class ConversationMemory:
    """按会话 ID 管理对话历史"""

    def __init__(self, max_history: int = 10, ttl_seconds: int = 3600):
        """
        Args:
            max_history: 每个会话保留的最大消息条数
            ttl_seconds: 会话过期时间（秒），超过此时间未更新的会话自动清理
        """
        self._histories: dict[str, list[Message]] = {}
        self._last_active: dict[str, float] = {}
        self.max_history = max_history
        self.ttl_seconds = ttl_seconds

    def add_message(self, conv_id: str, role: str, text: str) -> None:
        """添加一条消息到会话"""
        if conv_id not in self._histories:
            self._histories[conv_id] = []

        self._histories[conv_id].append(Message(role=role, text=text))
        # 截断到最大长度
        if len(self._histories[conv_id]) > self.max_history:
            self._histories[conv_id] = self._histories[conv_id][-self.max_history:]

        self._last_active[conv_id] = time.time()

    def get_context(self, conv_id: str) -> list[dict[str, str]]:
        """获取会话上下文（用于构建 LLM prompt）

        Returns:
            [{"role": "user", "text": "..."}, ...]
        """
        self._maybe_expire(conv_id)
        messages = self._histories.get(conv_id, [])
        return [{"role": m.role, "text": m.text} for m in messages]

    def get_last_message(self, conv_id: str) -> dict[str, str] | None:
        """获取会话最后一条消息"""
        messages = self._histories.get(conv_id, [])
        if not messages:
            return None
        m = messages[-1]
        return {"role": m.role, "text": m.text}

    def clear_conversation(self, conv_id: str) -> None:
        """清空指定会话"""
        self._histories.pop(conv_id, None)
        self._last_active.pop(conv_id, None)

    def cleanup_expired(self) -> int:
        """清理所有过期会话，返回清理数量"""
        now = time.time()
        expired = [
            cid for cid, ts in self._last_active.items()
            if now - ts > self.ttl_seconds
        ]
        for cid in expired:
            self._histories.pop(cid, None)
            self._last_active.pop(cid, None)
        return len(expired)

    def _maybe_expire(self, conv_id: str) -> None:
        """检查单个会话是否过期"""
        last = self._last_active.get(conv_id)
        if last and time.time() - last > self.ttl_seconds:
            self._histories.pop(conv_id, None)
            self._last_active.pop(conv_id, None)

    @property
    def active_conversations(self) -> int:
        """当前活跃会话数"""
        return len(self._histories)
