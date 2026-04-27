"""比特浏览器集成 — 指纹浏览器 + 代理 IP + Playwright CDP 连接

每个账号对应一个比特浏览器窗口，独立指纹+独立代理，平台无法关联。
需要本地运行比特浏览器客户端（默认端口 54345）。

流程：
  1. create_window() — 创建窗口（配置代理+指纹）
  2. open_window() — 打开窗口，获取 ws 地址
  3. connect() — Playwright 通过 CDP 连接
  4. 操作页面（登录/抓 Cookie）
  5. close_window() — 关闭窗口
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_API = "http://127.0.0.1:54345"


class BitBrowserClient:
    """比特浏览器本地 API 客户端"""

    def __init__(self, api_base: str = DEFAULT_API):
        self.api_base = api_base.rstrip("/")

    async def _post(self, path: str, data: dict) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.api_base}{path}",
                json=data,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            return resp.json()

    # ── 窗口管理 ──────────────────────────────────────────

    async def create_window(
        self,
        name: str,
        proxy_type: str = "noproxy",
        proxy_host: str = "",
        proxy_port: str = "",
        proxy_user: str = "",
        proxy_pass: str = "",
        core_version: str = "124",
        group_id: str = "0",
        remark: str = "",
    ) -> dict:
        """创建浏览器窗口

        proxy_type: noproxy / http / https / socks5
        返回: {"success": true, "data": {"id": "窗口ID"}}
        """
        data: dict[str, Any] = {
            "name": name,
            "remark": remark or f"autofish_{name}",
            "groupId": group_id,
            "proxyMethod": 2,  # 自定义代理
            "proxyType": proxy_type,
            "host": proxy_host,
            "port": proxy_port,
            "proxyUserName": proxy_user,
            "proxyPassword": proxy_pass,
            "browserFingerPrint": {
                "coreVersion": core_version,
            },
        }
        result = await self._post("/browser/update", data)
        if result.get("success"):
            window_id = result.get("data", {}).get("id", "")
            logger.info("[bitbrowser] Window created: %s → %s", name, window_id[:12])
        else:
            logger.error("[bitbrowser] Create failed: %s", result)
        return result

    async def open_window(self, window_id: str) -> dict:
        """打开浏览器窗口，返回包含 ws 地址的数据

        返回: {"success": true, "data": {"ws": "ws://...", "http": "http://..."}}
        """
        result = await self._post("/browser/open", {"id": window_id})
        if result.get("success"):
            ws = result.get("data", {}).get("ws", "")
            logger.info("[bitbrowser] Window opened: %s, ws=%s", window_id[:12], ws[:50])
        else:
            logger.error("[bitbrowser] Open failed: %s", result)
        return result

    async def close_window(self, window_id: str) -> dict:
        """关闭浏览器窗口"""
        result = await self._post("/browser/close", {"id": window_id})
        logger.info("[bitbrowser] Window closed: %s", window_id[:12])
        return result

    async def delete_window(self, window_id: str) -> dict:
        """删除浏览器窗口"""
        result = await self._post("/browser/delete", {"id": window_id})
        logger.info("[bitbrowser] Window deleted: %s", window_id[:12])
        return result

    async def list_windows(self, page: int = 0, page_size: int = 100) -> dict:
        """获取窗口列表"""
        return await self._post("/browser/list", {"page": page, "pageSize": page_size})

    async def update_proxy(
        self,
        window_id: str,
        proxy_type: str,
        proxy_host: str,
        proxy_port: str,
        proxy_user: str = "",
        proxy_pass: str = "",
    ) -> dict:
        """更新窗口代理"""
        return await self._post("/browser/update/partial", {
            "ids": [window_id],
            "proxyMethod": 2,
            "proxyType": proxy_type,
            "host": proxy_host,
            "port": proxy_port,
            "proxyUserName": proxy_user,
            "proxyPassword": proxy_pass,
        })

    # ── Playwright 集成 ───────────────────────────────────

    async def connect_playwright(self, window_id: str):
        """打开窗口并通过 Playwright CDP 连接

        返回: (browser, context, page, ws_url)
        """
        from playwright.async_api import async_playwright

        # 打开窗口
        result = await self.open_window(window_id)
        if not result.get("success"):
            raise RuntimeError(f"Failed to open window: {result}")

        ws_url = result["data"]["ws"]

        # CDP 连接
        pw = await async_playwright().start()
        browser = await pw.chromium.connect_over_cdp(ws_url)

        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = context.pages[0] if context.pages else await context.new_page()

        logger.info("[bitbrowser] Playwright connected via CDP: %s", window_id[:12])
        return browser, context, page, ws_url

    async def get_cookies(self, window_id: str, url: str = "") -> str:
        """打开窗口 → 访问 URL → 抓取 cookie → 关闭窗口

        返回 cookie string（用于 HTTP API 发布）
        """
        import asyncio

        browser, context, page, _ = await self.connect_playwright(window_id)
        try:
            if url:
                await page.goto(url)
                await asyncio.sleep(3)

            cookies = await context.cookies()
            cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
            logger.info("[bitbrowser] Got %d cookies from %s", len(cookies), window_id[:12])
            return cookie_str
        finally:
            await browser.close()
            await self.close_window(window_id)

    # ── 便捷方法 ──────────────────────────────────────────

    async def create_and_open(
        self,
        name: str,
        proxy_type: str = "noproxy",
        proxy_host: str = "",
        proxy_port: str = "",
        proxy_user: str = "",
        proxy_pass: str = "",
    ):
        """创建窗口 + 打开 + Playwright 连接（一步到位）

        返回: (browser, context, page, window_id)
        """
        # 创建
        create_result = await self.create_window(
            name=name, proxy_type=proxy_type,
            proxy_host=proxy_host, proxy_port=proxy_port,
            proxy_user=proxy_user, proxy_pass=proxy_pass,
        )
        if not create_result.get("success"):
            raise RuntimeError(f"Create window failed: {create_result}")

        window_id = create_result["data"]["id"]

        # 连接
        browser, context, page, _ = await self.connect_playwright(window_id)
        return browser, context, page, window_id

    async def is_available(self) -> bool:
        """检查比特浏览器是否在运行"""
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                resp = await client.post(
                    f"{self.api_base}/browser/list",
                    json={"page": 0, "pageSize": 1},
                )
                return resp.status_code == 200
        except Exception:
            return False
