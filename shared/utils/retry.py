"""共享重试策略 — 指数退避

参考 ShortVideo.AutoPublisher 的 Polly 设计，三平台共用。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)

# 可重试错误码
RETRYABLE_CODES = {"timeout", "browser_error", "network_error", "connection_error"}
# 不可重试错误码（立即停止）
FATAL_CODES = {"login_failed", "content_violation", "account_banned", "not_implemented"}


class RetryPolicy:
    """指数退避重试策略

    Usage:
        policy = RetryPolicy(max_retries=3)
        result = await policy.execute(adapter.publish, account, payload)
    """

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        retryable_codes: set[str] | None = None,
        fatal_codes: set[str] | None = None,
    ):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.retryable_codes = retryable_codes or RETRYABLE_CODES
        self.fatal_codes = fatal_codes or FATAL_CODES

    async def execute(
        self,
        func: Callable[..., Awaitable[dict[str, Any]]],
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """执行带重试的异步函数

        func 应返回 dict，包含 "success" 和可选的 "error_code" 字段。
        """
        last_result: dict[str, Any] = {}

        for attempt in range(self.max_retries + 1):
            try:
                result = await func(*args, **kwargs)
                last_result = result

                if result.get("success"):
                    return result

                error_code = result.get("error_code", "")

                # 不可重试错误，立即返回
                if error_code in self.fatal_codes:
                    logger.info("Fatal error '%s', not retrying", error_code)
                    return result

                # 非可重试错误码且非空，也不重试
                if error_code and error_code not in self.retryable_codes:
                    logger.info("Non-retryable error '%s', not retrying", error_code)
                    return result

            except Exception as exc:
                last_result = {
                    "success": False,
                    "error": str(exc),
                    "error_code": "exception",
                }

            # 还有重试机会
            if attempt < self.max_retries:
                delay = min(self.base_delay * (2 ** attempt), self.max_delay)
                logger.info(
                    "Retry %d/%d after %.1fs (error: %s)",
                    attempt + 1,
                    self.max_retries,
                    delay,
                    last_result.get("error", "unknown"),
                )
                await asyncio.sleep(delay)

        return last_result
