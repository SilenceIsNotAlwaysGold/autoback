"""浏览器实例池 — 保持登录态不丢失

解决问题：小红书等平台的登录态是 session cookie（浏览器关闭即失效）。
方案：登录后不关闭浏览器，后续操作复用同一个实例。

用法：
    engine = await BrowserPool.get("xiaohongshu", "account1")
    # 用完不要 stop，pool 会管理生命周期
    await BrowserPool.release("xiaohongshu", "account1")
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from platforms.browser.engine import BrowserEngine

logger = logging.getLogger(__name__)

# 全局实例池：key = "platform/account_name"
_pool: dict[str, BrowserEngine] = {}
_lock = asyncio.Lock()

# 空闲超时（秒），超过这个时间没使用就关闭
IDLE_TIMEOUT = 1800  # 30 分钟


class BrowserPool:
    """浏览器实例池"""

    @staticmethod
    async def get(platform: str, account_name: str, headless: bool = True) -> BrowserEngine:
        """获取或创建浏览器实例"""
        key = f"{platform}/{account_name}"
        async with _lock:
            engine = _pool.get(key)
            if engine and engine.page:
                # 检查是否还活着
                try:
                    await engine.page.title()
                    logger.info("[pool] Reuse %s", key)
                    return engine
                except Exception:
                    # 实例已死，清理
                    logger.warning("[pool] %s is dead, recreating", key)
                    try:
                        await engine.stop()
                    except Exception:
                        pass
                    del _pool[key]

            # 创建新实例
            engine = BrowserEngine(platform=platform, account_name=account_name, headless=headless)
            await engine.start()
            _pool[key] = engine
            logger.info("[pool] Created %s (total: %d)", key, len(_pool))
            return engine

    @staticmethod
    async def release(platform: str, account_name: str):
        """标记实例为空闲（不关闭）"""
        key = f"{platform}/{account_name}"
        logger.debug("[pool] Released %s", key)

    @staticmethod
    async def destroy(platform: str, account_name: str):
        """强制关闭并移除实例"""
        key = f"{platform}/{account_name}"
        async with _lock:
            engine = _pool.pop(key, None)
            if engine:
                try:
                    await engine.stop()
                except Exception:
                    pass
                logger.info("[pool] Destroyed %s", key)

    @staticmethod
    async def destroy_all():
        """关闭所有实例"""
        async with _lock:
            for key, engine in _pool.items():
                try:
                    await engine.stop()
                except Exception:
                    pass
            count = len(_pool)
            _pool.clear()
            logger.info("[pool] Destroyed all (%d instances)", count)

    @staticmethod
    def status() -> dict[str, Any]:
        """返回池状态"""
        return {
            "total": len(_pool),
            "instances": list(_pool.keys()),
        }
