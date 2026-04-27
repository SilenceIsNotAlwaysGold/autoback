"""统一浏览器执行引擎

三平台共享的 Playwright 操作层。
提供：生命周期管理、反检测、人类行为模拟、会话持久化、截图/快照。
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from pathlib import Path
from typing import Any

from platforms.browser.stealth import STEALTH_FULL, USER_AGENTS, VIEWPORTS

logger = logging.getLogger(__name__)

STATE_BASE_DIR = Path("data/browser_state")


class BrowserEngine:
    """统一浏览器引擎 — 三平台共享

    支持两种模式：
    1. 内置 Playwright（默认）— 直接启动 Chromium
    2. 比特浏览器 — 通过 CDP 连接比特浏览器窗口（独立指纹+代理）
    """

    def __init__(self, platform: str, account_name: str, headless: bool = True,
                 bitbrowser_id: str = "", proxy_url: str = ""):
        self.platform = platform
        self.account_name = account_name
        self.headless = headless
        self.bitbrowser_id = bitbrowser_id  # 比特浏览器窗口 ID（非空则用比特浏览器）
        self.proxy_url = proxy_url  # 代理 URL（http://user:pass@ip:port）
        self._playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self._start_time = 0.0
        self._bitbrowser_client = None
        self._proxy_bridge = None  # 可选：SOCKS5 认证 → 本地 HTTP 桥

    # ── 生命周期 ───────────────────────────────────────────

    async def start(self):
        """启动浏览器

        优先级：比特浏览器（有 bitbrowser_id）→ 内置 Playwright
        """
        # 比特浏览器模式
        if self.bitbrowser_id:
            return await self._start_bitbrowser()

        # 内置 Playwright 模式
        try:
            from patchright.async_api import async_playwright
        except ImportError:
            from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        self._start_time = time.monotonic()

        # 持久化 profile 目录（像真人 Chrome 一样保留缓存/历史/指纹）
        profile_dir = Path("data/browser_profiles") / self.platform / self.account_name
        profile_dir.mkdir(parents=True, exist_ok=True)

        # 账号专属指纹（每个账号 UA/视口/指纹 固定且唯一）
        from platforms.browser.fingerprint import generate_fingerprint, generate_stealth_script
        fp = generate_fingerprint(self.account_name)
        ua = USER_AGENTS[hash(self.account_name) % len(USER_AGENTS)]
        vp = {"width": fp["screen_width"], "height": fp["screen_height"]}

        chrome_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        if self.headless:
            chrome_args.append("--headless=new")

        # 代理配置
        # Chromium 不支持带认证的 SOCKS5，也不太稳定处理 HTTP 认证弹窗
        # → 用 pproxy 起本地 HTTP 桥接，对 Chromium 透明
        proxy = None
        if self.proxy_url:
            from urllib.parse import urlparse, unquote
            from platforms.browser.proxy_bridge import ProxyBridge

            try:
                parsed = urlparse(self.proxy_url)
                if not parsed.hostname or not parsed.port:
                    raise ValueError("proxy_url 缺少 host 或 port")

                # 判断是否需要桥接（带认证必须桥接）
                self._proxy_bridge = ProxyBridge(self.proxy_url)
                if self._proxy_bridge.needed:
                    local_url = await self._proxy_bridge.start()   # in-process asyncio
                    proxy = {"server": local_url}            # Chromium 连本地无认证 HTTP
                    logger.info("[%s/%s] Proxy bridged: %s → %s:%s",
                                self.platform, self.account_name, local_url,
                                parsed.hostname, parsed.port)
                else:
                    # 无认证，直接给 Chromium
                    scheme = parsed.scheme or "http"
                    proxy = {"server": f"{scheme}://{parsed.hostname}:{parsed.port}"}
                    logger.info("[%s/%s] Proxy direct: %s:%s",
                                self.platform, self.account_name,
                                parsed.hostname, parsed.port)
            except Exception as e:
                logger.error("[%s/%s] Invalid proxy_url %r: %s",
                             self.platform, self.account_name, self.proxy_url, e)
                proxy = None
                self._proxy_bridge = None

        self.context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,
            viewport=vp,
            user_agent=ua,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            args=chrome_args,
            proxy=proxy,
            ignore_https_errors=True,
        )
        # 注入账号专属反检测脚本（每个账号指纹不同）
        await self.context.add_init_script(generate_stealth_script(self.account_name))

        # persistent context 自动创建一个 page
        if self.context.pages:
            self.page = self.context.pages[0]
        else:
            self.page = await self.context.new_page()
        self.page.set_default_timeout(20000)

        logger.info("[%s/%s] Browser started (persistent, headless=%s, vp=%dx%d)",
                    self.platform, self.account_name, self.headless,
                    vp["width"], vp["height"])

    async def _start_bitbrowser(self):
        """通过比特浏览器 CDP 连接（独立指纹+代理）"""
        from platforms.browser.bitbrowser import BitBrowserClient

        self._start_time = time.monotonic()
        self._bitbrowser_client = BitBrowserClient()

        self.browser, self.context, self.page, _ = await self._bitbrowser_client.connect_playwright(
            self.bitbrowser_id
        )
        self.page.set_default_timeout(20000)

        logger.info("[%s/%s] BitBrowser connected (window=%s)",
                    self.platform, self.account_name, self.bitbrowser_id[:12])

    async def stop(self):
        # 比特浏览器模式：关闭 CDP 连接 + 关闭窗口
        if self._bitbrowser_client and self.bitbrowser_id:
            try:
                if self.browser:
                    await self.browser.close()
                await self._bitbrowser_client.close_window(self.bitbrowser_id)
            except Exception:
                pass
            elapsed = time.monotonic() - self._start_time if self._start_time else 0
            self.page = self.context = self.browser = None
            logger.info("[%s/%s] BitBrowser stopped (%.1fs)", self.platform, self.account_name, elapsed)
            return

        # 内置 Playwright 模式
        if self.context:
            try:
                await self.context.close()
            except Exception:
                pass
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
        elapsed = time.monotonic() - self._start_time if self._start_time else 0
        self.page = self.context = self.browser = self._playwright = None
        # 关掉代理桥（in-process asyncio server）
        if self._proxy_bridge:
            try:
                await self._proxy_bridge.stop()
            except Exception:
                pass
            self._proxy_bridge = None
        logger.info("[%s/%s] Browser stopped (%.1fs)", self.platform, self.account_name, elapsed)

    # ── State 持久化 ──────────────────────────────────────

    @property
    def state_path(self) -> Path:
        d = STATE_BASE_DIR / self.platform
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{self.account_name}.json"

    async def save_state(self):
        """保存浏览器状态，session cookie 转为持久 cookie"""
        if not self.context:
            return
        # 先把 session cookie 改成持久 cookie（加 30 天过期时间）
        await self._persist_session_cookies()
        await self.context.storage_state(path=str(self.state_path))
        logger.info("[%s/%s] State saved", self.platform, self.account_name)

    async def export_state_string(self) -> str:
        if not self.context:
            return ""
        await self._persist_session_cookies()
        state = await self.context.storage_state()
        return json.dumps(state, ensure_ascii=False)

    async def _persist_session_cookies(self):
        """把 session cookie（无 expires）改成持久 cookie，防止关浏览器丢失"""
        if not self.context:
            return
        try:
            cookies = await self.context.cookies()
            expire_ts = time.time() + 30 * 86400  # 30 天后过期
            updated = []
            for c in cookies:
                if c.get("expires", -1) < 0:
                    # session cookie，加上过期时间
                    c["expires"] = expire_ts
                    updated.append(c)
            if updated:
                await self.context.add_cookies(updated)
                logger.info("[%s/%s] Persisted %d session cookies", self.platform, self.account_name, len(updated))
        except Exception as e:
            logger.warning("[%s/%s] Persist cookies failed: %s", self.platform, self.account_name, e)

    async def load_state_from_string(self, state_str: str) -> bool:
        if not state_str or not self.context:
            return False
        try:
            state = json.loads(state_str)
            if "cookies" in state:
                await self.context.add_cookies(state["cookies"])
            # 也恢复 localStorage
            if "origins" in state:
                for origin in state["origins"]:
                    url = origin.get("origin", "")
                    items = origin.get("localStorage", [])
                    if url and items:
                        try:
                            await self.page.goto(url)
                            for item in items:
                                await self.page.evaluate(
                                    "([k, v]) => localStorage.setItem(k, v)",
                                    [item["name"], item["value"]]
                                )
                        except Exception:
                            pass
                return True
            return bool(state.get("cookies"))
        except Exception as e:
            logger.warning("[%s/%s] Load state failed: %s", self.platform, self.account_name, e)
        return False

    # ── 导航 ──────────────────────────────────────────────

    async def goto(self, url: str, wait_until: str = "domcontentloaded"):
        if not self.page:
            raise RuntimeError("Browser not started")
        if self._proxy_bridge and not self._proxy_bridge.is_alive():
            logger.warning("[%s/%s] Proxy bridge died unexpectedly — navigation may fail",
                           self.platform, self.account_name)
        try:
            await self.page.goto(url, wait_until=wait_until, timeout=30000)
        except Exception as e:
            if self.page and not self.page.is_closed():
                raise
            logger.debug("[%s/%s] goto ignored (page closing): %s",
                         self.platform, self.account_name, e)

    # ── 人类行为模拟 ──────────────────────────────────────

    async def human_delay(self, min_sec: float = 1.0, max_sec: float = 3.0):
        """随机延迟，模拟人类操作节奏"""
        await asyncio.sleep(random.uniform(min_sec, max_sec))

    async def human_type(self, text: str, delay_ms: int = 80):
        """逐字输入，模拟人类打字速度"""
        if not self.page:
            return
        for char in text:
            await self.page.keyboard.type(char, delay=delay_ms + random.randint(-20, 40))
            if random.random() < 0.05:  # 5% 概率打字停顿
                await asyncio.sleep(random.uniform(0.3, 0.8))

    async def human_scroll(self, distance: int = 300):
        """模拟人类滚动"""
        if not self.page:
            return
        await self.page.mouse.wheel(0, distance + random.randint(-50, 50))
        await self.human_delay(0.5, 1.5)

    # ── 语义定位操作（抗改版） ─────────────────────────────

    async def fill_by_placeholder(self, placeholder: str, value: str) -> bool:
        try:
            loc = self.page.get_by_placeholder(placeholder, exact=False)
            if await loc.count() > 0:
                await loc.first.click()
                await self.human_delay(0.3, 0.8)
                await loc.first.fill(value)
                return True
        except Exception as e:
            logger.debug("[%s] fill_by_placeholder '%s': %s", self.platform, placeholder, e)
        return False

    async def fill_by_label(self, label: str, value: str) -> bool:
        try:
            loc = self.page.get_by_label(label, exact=False)
            if await loc.count() > 0:
                await loc.first.click()
                await self.human_delay(0.3, 0.8)
                await loc.first.fill(value)
                return True
        except Exception as e:
            logger.debug("[%s] fill_by_label '%s': %s", self.platform, label, e)
        return False

    async def click_text(self, text: str, timeout: int = 5000) -> bool:
        try:
            loc = self.page.get_by_text(text, exact=False)
            if await loc.count() > 0:
                await self.human_delay(0.2, 0.5)
                await loc.first.click(timeout=timeout)
                return True
        except Exception as e:
            logger.debug("[%s] click_text '%s': %s", self.platform, text, e)
        return False

    async def click_role(self, role: str, name: str, timeout: int = 5000) -> bool:
        try:
            loc = self.page.get_by_role(role, name=name)
            if await loc.count() > 0:
                await self.human_delay(0.2, 0.5)
                await loc.first.click(timeout=timeout)
                return True
        except Exception as e:
            logger.debug("[%s] click_role %s '%s': %s", self.platform, role, name, e)
        return False

    async def click_selector(self, selector: str, timeout: int = 5000) -> bool:
        """CSS 选择器兜底"""
        try:
            el = await self.page.wait_for_selector(selector, timeout=timeout)
            if el:
                await self.human_delay(0.2, 0.5)
                await el.click()
                return True
        except Exception:
            pass
        return False

    async def upload_file(self, file_path: str) -> bool:
        """通过 input[type=file] 上传"""
        p = Path(file_path)
        if not p.exists():
            logger.warning("[%s] File not found: %s", self.platform, file_path)
            return False
        try:
            file_input = self.page.locator('input[type="file"]')
            if await file_input.count() > 0:
                await file_input.first.set_input_files(str(p.absolute()))
                return True
        except Exception as e:
            logger.warning("[%s] Upload failed: %s", self.platform, e)
        return False

    async def type_in_editor(self, selectors: list[str], text: str) -> bool:
        """在 contenteditable 编辑器中输入（适合小红书/抖音）"""
        for sel in selectors:
            try:
                loc = self.page.locator(sel)
                if await loc.count() > 0:
                    await loc.first.click()
                    await self.human_delay(0.3, 0.8)
                    await self.human_type(text)
                    return True
            except Exception:
                continue
        return False

    # ── 安全弹窗处理（闲鱼特有） ──────────────────────────

    async def dismiss_popups(self):
        """关闭常见的安全/提示弹窗"""
        dismiss_texts = ["我知道了", "确定", "关闭", "知道了", "好的"]
        for text in dismiss_texts:
            try:
                loc = self.page.get_by_text(text, exact=True)
                if await loc.count() > 0:
                    await loc.first.click()
                    await self.human_delay(0.5, 1.0)
                    logger.info("[%s] Dismissed popup: '%s'", self.platform, text)
            except Exception:
                pass
        # Ant Design modal 特殊处理
        try:
            modal_btn = self.page.locator(".ant-modal .ant-btn-primary")
            if await modal_btn.count() > 0:
                await modal_btn.first.click()
                await self.human_delay(0.5, 1.0)
        except Exception:
            pass

    # ── 截图 / HTML 快照 ─────────────────────────────────

    async def screenshot(self, name: str = "screenshot") -> str:
        """截图并返回文件路径"""
        if not self.page:
            return ""
        path = f"data/screenshots/{self.platform}/{self.account_name}/{name}_{int(time.time())}.png"
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        await self.page.screenshot(path=path)
        return path

    async def html_snapshot(self, name: str = "snapshot") -> str:
        """保存页面 HTML"""
        if not self.page:
            return ""
        path = f"data/snapshots/{self.platform}/{self.account_name}/{name}_{int(time.time())}.html"
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        html = await self.page.content()
        Path(path).write_text(html, encoding="utf-8")
        return path

    # ── 登录通用流程 ──────────────────────────────────────

    async def wait_for_login(self, check_fn, timeout: int = 120) -> bool:
        """等待用户扫码登录

        Args:
            check_fn: async callable，返回 True 表示已登录
            timeout: 超时秒数
        """
        for i in range(timeout):
            await asyncio.sleep(1)
            try:
                if await check_fn():
                    await self.save_state()
                    return True
            except Exception:
                pass
            if i % 30 == 29:
                logger.info("[%s/%s] Waiting for login... (%ds)",
                            self.platform, self.account_name, i + 1)
        logger.error("[%s/%s] Login timeout (%ds)", self.platform, self.account_name, timeout)
        return False
