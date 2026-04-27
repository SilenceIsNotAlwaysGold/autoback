"""抖音自动回复脚本入口（多账号矩阵版）

用法：
    # 1. 复制并编辑配置
    cp config/dy_reply.example.yaml config/dy_reply.yaml

    # 2. 首次为每个账号扫码登录（依次弹出浏览器）
    python scripts/dy_auto_reply.py --login

    # 3. 正式跑（所有账号并发）
    python scripts/dy_auto_reply.py

    # 4. 干跑（只抓不发）
    python scripts/dy_auto_reply.py --dry-run

    # 5. 只跑单个账号（调试用）
    python scripts/dy_auto_reply.py --account dy_acc1

架构：
    MultiAccountOrchestrator
      └─ AccountRunner (per account)
            ├─ BrowserEngine (支持 proxy_url / bitbrowser_id)
            ├─ DouyinMessenger  → 私信回复循环
            ├─ DouyinCommenter  → 评论回复循环
            └─ 共享：ReplyStore + AIReplyAgent + rules
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from platforms.browser.engine import BrowserEngine
from platforms.douyin import selectors as S
from platforms.douyin.commenter import DouyinCommenter
from platforms.douyin.messenger import DouyinMessenger
from scripts.dy_reply_store import ReplyStore
from shared.ai.agent import AIReplyAgent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("dy_auto_reply")


# ── 登录检测（创作者中心） ─────────────────────────────

async def _check_login(engine: BrowserEngine) -> bool:
    if not engine.page:
        return False
    url = engine.page.url
    if any(ind in url for ind in S.LOGIN_INDICATORS):
        return False
    try:
        landing = engine.page.get_by_text("助力创作者高效运营", exact=False)
        if await landing.count() > 0:
            return False
    except Exception:
        pass
    for selector in [
        '[class*="avatar"]',
        '[data-e2e="user-info"]',
        '[class*="sidebar"] [class*="menu"]',
        '[class*="creator-layout"]',
    ]:
        try:
            if await engine.page.query_selector(selector):
                return True
        except Exception:
            continue
    return False


async def ensure_login(engine: BrowserEngine, timeout: int, prompt_scan: bool) -> bool:
    """确保登录。prompt_scan=False 时若未登录直接返回 False（不弹扫码）"""
    await engine.goto(S.HOME_URL)
    await asyncio.sleep(3)
    if await _check_login(engine):
        return True
    if not prompt_scan:
        return False
    logger.info("Not logged in, clicking login button...")
    await engine.click_text("登录")
    await asyncio.sleep(3)
    logger.info("Please scan QR code to login (timeout=%ds)...", timeout)
    ok = await engine.wait_for_login(lambda: _check_login(engine), timeout=timeout)

    # Fallback：抖音可能改版让 _check_login 失效，只要关键 cookie 产生就算登录成功
    if not ok:
        logger.info("Timeout but checking cookies as fallback...")
        try:
            cookies = await engine.context.cookies()
            key_names = {"sessionid", "sessionid_ss", "sid_tt", "passport_csrf_token"}
            found = [c["name"] for c in cookies if c["name"] in key_names]
            url_now = engine.page.url if engine.page else "?"
            logger.info("Current URL=%s, cookies matched=%s", url_now, found)
            if found:
                logger.info("Found login cookies → treat as logged in")
                try:
                    await engine.save_state()
                except Exception:
                    pass
                return True
        except Exception as e:
            logger.warning("cookie fallback failed: %s", e)
    return ok


# ── 单账号执行器 ───────────────────────────────────────

class AccountRunner:
    def __init__(
        self,
        account_cfg: dict,
        shared_cfg: dict,
        store: ReplyStore,
        ai: AIReplyAgent | None,
        op_semaphore: asyncio.Semaphore,
        dry_run: bool,
    ):
        self.cfg = account_cfg
        self.shared = shared_cfg
        self.name = account_cfg["name"]
        self.store = store
        self.ai = ai
        self.op_sem = op_semaphore  # 全局并发闸门（限制同时操作浏览器的账号数）
        self.dry_run = dry_run
        self._stop = asyncio.Event()
        self._page_lock = asyncio.Lock()  # 同账号内 pm/comment 不能同时操作 page

        self.engine: BrowserEngine | None = None
        self.messenger: DouyinMessenger | None = None
        self.commenter: DouyinCommenter | None = None
        self._log = logging.getLogger(f"runner[{self.name}]")
        # 退出原因：None=正常/运行中；"not_logged_in"=登录态缺失或失效（等 profile 更新，不做退避重启）
        # "proxy_unreachable"=代理不通（走退避重试）；None 也用于 run() 正常结束
        self._exit_reason: str | None = None

    def _mask_proxy(self, url: str) -> str:
        """脱敏代理凭据"""
        if not url:
            return ""
        try:
            if "@" in url:
                scheme, rest = url.split("://", 1)
                _, host = rest.split("@", 1)
                return f"{scheme}://***@{host}"
        except Exception:
            pass
        return url

    async def _preflight_proxy(self, proxy_url: str) -> bool:
        """用 httpx 快速验证代理：需要同时能走 HTTP 和 HTTPS（抖音是 HTTPS 站）"""
        import httpx
        ok_http = ok_https = False
        try:
            with httpx.Client(proxy=proxy_url, timeout=8, verify=False) as c:
                # HTTP 通路
                for url in ("http://ip-api.com/json/", "http://httpbin.org/ip"):
                    try:
                        if c.get(url).status_code == 200:
                            ok_http = True
                            break
                    except Exception:
                        continue
                # HTTPS CONNECT 通路（关键：抖音必须用 HTTPS）
                for url in ("https://creator.douyin.com/", "https://www.baidu.com/"):
                    try:
                        if c.get(url).status_code < 500:
                            ok_https = True
                            break
                    except Exception:
                        continue
        except Exception as e:
            self._log.error("Proxy preflight error: %s", e)
            return False

        if not ok_http:
            self._log.error("Proxy 不通 HTTP")
            return False
        if not ok_https:
            self._log.error("Proxy 不支持 HTTPS CONNECT（抖音是 HTTPS 站，必须支持）")
            return False
        self._log.info("Proxy OK (HTTP + HTTPS 双通)")
        return True

    async def setup(self) -> bool:
        proxy_url = self.cfg.get("proxy_url", "") or ""
        bitbrowser_id = self.cfg.get("bitbrowser_id", "") or ""
        headless = self.shared.get("browser", {}).get("headless", False)

        self._exit_reason = None

        # 代理预检
        if proxy_url and not await self._preflight_proxy(proxy_url):
            self._log.warning("Proxy unreachable, skip account: %s",
                             self._mask_proxy(proxy_url))
            self._exit_reason = "proxy_unreachable"
            return False

        self._log.info(
            "Starting engine (headless=%s, proxy=%s, bitbrowser=%s)",
            headless,
            self._mask_proxy(proxy_url) or "none",
            (bitbrowser_id[:8] + "...") if bitbrowser_id else "none",
        )

        self.engine = BrowserEngine(
            platform="douyin",
            account_name=self.name,
            headless=headless,
            proxy_url=proxy_url,
            bitbrowser_id=bitbrowser_id,
        )
        await self.engine.start()

        # 复用已有登录态；未登录则跳过（让用户先跑 --login）
        logged = await ensure_login(
            self.engine,
            timeout=self.cfg.get("login_timeout", 180),
            prompt_scan=False,
        )
        if not logged:
            self._log.warning("Not logged in. Skip this account. Run `--login --account %s` first.", self.name)
            await self.engine.stop()
            self.engine = None
            self._exit_reason = "not_logged_in"
            return False

        self.messenger = DouyinMessenger(self.engine)
        self.commenter = DouyinCommenter(self.engine)
        # 继承热重载前保存的反自触发状态
        inherited = getattr(self, "_inherited_messenger_state", None)
        if inherited:
            self.messenger._last_reply_ts.update(inherited.get("last_reply_ts", {}))
            self.messenger._recent_sent.update(inherited.get("recent_sent", {}))
            del self._inherited_messenger_state
        self._log.info("Ready")
        return True

    async def login_interactive(self):
        """首次交互式登录：弹浏览器等扫码"""
        proxy_url = self.cfg.get("proxy_url", "") or ""
        bitbrowser_id = self.cfg.get("bitbrowser_id", "") or ""

        # 代理预检：登录前 httpx 先试一下，代理不通就别浪费 Chromium 启动时间
        if proxy_url:
            self._log.info("Pre-flighting proxy...")
            if not await self._preflight_proxy(proxy_url):
                self._log.error("Proxy unreachable, abort login: %s",
                               self._mask_proxy(proxy_url))
                return False

        self._log.info("Interactive login (proxy=%s, bitbrowser=%s)",
                       self._mask_proxy(proxy_url) or "none",
                       (bitbrowser_id[:8] + "...") if bitbrowser_id else "none")

        self.engine = BrowserEngine(
            platform="douyin",
            account_name=self.name,
            headless=False,  # 登录必须可见
            proxy_url=proxy_url,
            bitbrowser_id=bitbrowser_id,
        )
        try:
            await self.engine.start()
            ok = await ensure_login(
                self.engine,
                timeout=self.cfg.get("login_timeout", 180),
                prompt_scan=True,
            )
            if ok:
                await self.engine.save_state()
                self._log.info("Login OK, state saved")
            else:
                self._log.error("Login failed/timeout")
            return ok
        finally:
            if self.engine:
                await self.engine.stop()
                self.engine = None

    async def _run_messenger(self):
        cfg = self.shared.get("messenger", {})
        if not cfg.get("enabled"):
            return
        mode = cfg.get("mode", "realtime")    # realtime | poll
        max_round = cfg.get("max_replies_per_round", 20)
        cap = cfg.get("max_per_day", 200)

        # rules 从 YAML 热读（配置 UI 改后无需重启主脚本）
        import yaml as _yaml
        cfg_path = Path(self.shared.get("_config_path", "config/dy_reply.yaml"))
        def rules_provider():
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    c = _yaml.safe_load(f) or {}
                # 兼容旧字段：reply_mode 只在没有新字段时才生效
                if "keyword_enabled" in c or "brainless_enabled" in c:
                    kw = bool(c.get("keyword_enabled", True))
                    br = bool(c.get("brainless_enabled", False))
                else:
                    old = c.get("reply_mode", "keyword")
                    kw = (old == "keyword")
                    br = (old == "brainless")
                if self.messenger:
                    self.messenger._account_config["_keyword_enabled"] = kw
                    self.messenger._account_config["_brainless_enabled"] = br
                    self.messenger._account_config["_brainless_replies"] = c.get("brainless_reply_texts", [])
                    # 用户级回复冷却（秒），跨重启持久。默认 30s
                    self.messenger._account_config["_user_reply_cooldown_sec"] = int(c.get("user_reply_cooldown_sec", 30))
                return c.get("rules", [])
            except Exception:
                return self.shared.get("rules", [])

        if mode == "realtime":
            self._log.info("[pm] Mode: realtime (MutationObserver)")
            async with self.op_sem, self._page_lock:
                try:
                    await self.messenger.run_realtime(
                        rules_provider=rules_provider,
                        ai_agent=self.ai,
                        store=self.store,
                        account_name=self.name,
                        dry_run=self.dry_run,
                        skip_groups=cfg.get("skip_groups", True),
                        max_per_day=cap,
                        max_replies_per_round=max_round,
                        stop_event=self._stop,
                        heartbeat_sec=cfg.get("heartbeat_sec", 300),
                    )
                except Exception as e:
                    self._log.error("[pm/realtime] error: %s", e, exc_info=True)
            return

        # 轮询模式（兼容旧行为）
        interval = cfg.get("poll_interval", 60)
        while not self._stop.is_set():
            try:
                today = self.store.today_reply_count(self.name, "pm")
                if today >= cap:
                    self._log.warning("[pm] daily cap %d reached, sleep 1h", cap)
                    await asyncio.wait_for(self._stop.wait(), timeout=3600)
                    continue

                async with self.op_sem, self._page_lock:
                    await self.messenger.auto_reply_loop(
                        rules=rules_provider(),
                        max_replies=min(max_round, cap - today),
                        ai_agent=self.ai,
                        store=self.store,
                        account_name=self.name,
                        dry_run=self.dry_run,
                        skip_groups=cfg.get("skip_groups", True),
                    )
            except Exception as e:
                self._log.error("[pm] loop error: %s", e, exc_info=True)

            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    async def _run_commenter(self):
        cfg = self.shared.get("commenter", {})
        if not cfg.get("enabled"):
            return
        interval = cfg.get("poll_interval", 120)
        max_round = cfg.get("max_replies_per_round", 30)
        cap = cfg.get("max_per_day", 300)
        delay = tuple(cfg.get("per_reply_delay", [5, 15]))
        rules = self.shared.get("rules", [])

        while not self._stop.is_set():
            try:
                today = self.store.today_reply_count(self.name, "comment")
                if today >= cap:
                    self._log.warning("[comment] daily cap %d reached, sleep 1h", cap)
                    await asyncio.wait_for(self._stop.wait(), timeout=3600)
                    continue

                async with self.op_sem, self._page_lock:
                    await self.commenter.auto_reply_loop(
                        rules=rules,
                        ai_agent=self.ai,
                        store=self.store,
                        account_name=self.name,
                        max_replies=min(max_round, cap - today),
                        per_reply_delay=delay,
                        dry_run=self.dry_run,
                    )
            except Exception as e:
                self._log.error("[comment] loop error: %s", e, exc_info=True)

            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    async def run(self):
        if not await self.setup():
            return
        try:
            await asyncio.gather(self._run_messenger(), self._run_commenter())
        finally:
            await self.teardown()

    async def teardown(self):
        try:
            if self.engine:
                await self.engine.save_state()
                await self.engine.stop()
        except Exception as e:
            self._log.warning("teardown error: %s", e)

    def request_stop(self):
        self._stop.set()


# ── 多账号编排 ─────────────────────────────────────────

class MultiAccountOrchestrator:
    def __init__(self, cfg: dict, dry_run: bool, only_account: str | None):
        self.cfg = cfg
        self.dry_run = dry_run
        self.only_account = only_account

        # 过滤账号
        all_accounts = cfg.get("accounts", [])
        if not all_accounts:
            raise ValueError("Config has no `accounts:` list")
        if only_account:
            accounts = [a for a in all_accounts if a.get("name") == only_account]
            if not accounts:
                raise ValueError(f"Account '{only_account}' not in config")
        else:
            accounts = all_accounts

        # 并发限制
        runtime = cfg.get("runtime", {})
        max_concurrent = runtime.get("max_concurrent") or len(accounts)
        self.op_sem = asyncio.Semaphore(max_concurrent)
        self.startup_stagger = runtime.get("startup_stagger", 3)

        # 共享资源
        store_cfg = cfg.get("store", {})
        self.store = ReplyStore(store_cfg.get("db_path", "data/dy_reply.db"))

        ai_cfg = cfg.get("ai", {})
        if ai_cfg.get("enabled") and ai_cfg.get("api_key"):
            ai_cfg = dict(ai_cfg)
            ai_cfg.setdefault("platform", "douyin")
            self.ai = AIReplyAgent(ai_cfg)
            logger.info("AI agent enabled (model=%s)", ai_cfg.get("model"))
        else:
            self.ai = None

        # 编排器级 stop 信号
        self._stop: asyncio.Event | None = None  # 在 run() 首行初始化

        # 构造 runners
        self.runners = [
            AccountRunner(
                account_cfg=a,
                shared_cfg=cfg,
                store=self.store,
                ai=self.ai,
                op_semaphore=self.op_sem,
                dry_run=dry_run,
            )
            for a in accounts
        ]
        logger.info("Configured %d account(s), max_concurrent=%d, stagger=%ds",
                    len(self.runners), max_concurrent, self.startup_stagger)

    async def run(self):
        # 延迟创建 stop event（要在 asyncio loop 里）
        self._stop = asyncio.Event()
        # 任务字典：account_name -> asyncio.Task
        self._tasks: dict[str, asyncio.Task] = {}

        async def start_one(r: AccountRunner, delay: float = 0):
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                await r.run()
            except asyncio.CancelledError:
                logger.info("[%s] runner cancelled", r.name)
                raise
            except Exception as e:
                logger.error("[%s] runner crashed: %s", r.name, e, exc_info=True)

        # 初始启动（带错峰）
        for i, r in enumerate(self.runners):
            t = asyncio.create_task(start_one(r, i * self.startup_stagger), name=f"runner:{r.name}")
            self._tasks[r.name] = t

        # 启动配置热加载 watcher
        watcher_task = asyncio.create_task(self._watch_config(), name="config-watcher")

        try:
            # 等全部 runner 或 stop 事件
            await self._stop.wait()
        finally:
            watcher_task.cancel()
            for t in self._tasks.values():
                if not t.done():
                    t.cancel()
            await asyncio.gather(*self._tasks.values(), watcher_task, return_exceptions=True)
            self.store.close()

    # ── 配置热加载 ──────────────────────────────────

    async def _watch_config(self):
        """检查 YAML 变更 + 用指数退避方式复活失败账号

        两条触发源：
          1) YAML 文件变化（用户在 UI 加/删/改账号，或刚扫码登录触发 profile 时间戳变）
          2) profile 目录变化（某账号刚扫码登录，profile 文件会更新）
          3) 死任务指数退避重试（避免狂开浏览器）
        """
        cfg_path = Path(self.cfg.get("_config_path", "config/dy_reply.yaml"))
        profile_base = Path("data/browser_profiles/douyin")
        import yaml as _yaml
        last_mtime = 0.0
        last_accounts: dict[str, dict] = {a["name"]: a for a in self.cfg.get("accounts", [])}

        # 每账号：{next_retry_at, attempts, profile_mtime}
        retry_state: dict[str, dict] = {}
        INITIAL_BACKOFF = 30          # 首次重试等 30s
        MAX_BACKOFF = 600             # 上限 10 分钟，永远重试

        def _profile_mtime(name: str) -> float:
            """profile 目录（或其下最近修改的 Cookies 文件）的 mtime"""
            p = profile_base / name / "Default" / "Cookies"
            try:
                return p.stat().st_mtime if p.exists() else 0.0
            except Exception:
                return 0.0

        while not self._stop.is_set():
            try:
                await asyncio.sleep(5)
                now = time.monotonic()

                # ── 死任务检测 ──
                for name, t in list(self._tasks.items()):
                    if not t.done():
                        continue
                    if name not in last_accounts:
                        continue  # 已从 yaml 里删除
                    state = retry_state.setdefault(name, {
                        "next_retry_at": now + INITIAL_BACKOFF,
                        "attempts": 0,
                        "profile_mtime": _profile_mtime(name),
                        "warned_not_logged_in": False,
                    })

                    # 1) profile 文件刚更新（用户扫码了）→ 立即重试并重置退避
                    cur_prof_mtime = _profile_mtime(name)
                    if cur_prof_mtime > state["profile_mtime"] + 1:
                        logger.info("[hot-reload] %s profile updated (likely just logged in), retry now", name)
                        state["profile_mtime"] = cur_prof_mtime
                        state["attempts"] = 0
                        state["next_retry_at"] = now
                        state["warned_not_logged_in"] = False

                    # 2) 如果该 runner 是因为"未登录"退出的，不做退避重启
                    #    只等 profile 文件更新（见上面）触发重试，避免狂开 Chromium
                    runner = next((r for r in self.runners if r.name == name), None)
                    exit_reason = getattr(runner, "_exit_reason", None) if runner else None
                    if exit_reason == "not_logged_in":
                        if not state["warned_not_logged_in"]:
                            logger.info(
                                "[hot-reload] %s not logged in, waiting for scan login "
                                "(run `--login --account %s` or login via UI)",
                                name, name,
                            )
                            state["warned_not_logged_in"] = True
                        continue

                    # 3) 到了退避时间点才重试（永远不放弃）
                    if now < state["next_retry_at"]:
                        continue

                    state["attempts"] += 1
                    # 指数退避：30s, 60s, 120s, 240s, 480s, 600s...（封顶 600）
                    backoff = min(INITIAL_BACKOFF * (2 ** min(state["attempts"], 5)), MAX_BACKOFF)
                    state["next_retry_at"] = now + backoff

                    acc_cfg = last_accounts[name]
                    logger.info(
                        "[hot-reload] Retrying %s (attempt %d, next backoff %ds)",
                        name, state["attempts"], backoff,
                    )
                    old_runner = next((r for r in self.runners if r.name == name), None)
                    self.runners = [r for r in self.runners if r.name != name]
                    r = AccountRunner(
                        account_cfg=acc_cfg,
                        shared_cfg={**self.cfg, "_config_path": str(cfg_path)},
                        store=self.store,
                        ai=self.ai,
                        op_semaphore=self.op_sem,
                        dry_run=self.dry_run,
                    )
                    # 继承旧 runner 的反自触发状态，避免重启后冷却状态丢失
                    if old_runner and old_runner.messenger:
                        r._inherited_messenger_state = {
                            "last_reply_ts": dict(old_runner.messenger._last_reply_ts),
                            "recent_sent": {k: list(v) for k, v in old_runner.messenger._recent_sent.items()},
                        }
                    self.runners.append(r)
                    self._tasks[name] = asyncio.create_task(
                        self._start_runner_safely(r), name=f"runner:{name}")

                # ── YAML 变更 ──
                if not cfg_path.exists():
                    continue
                mtime = cfg_path.stat().st_mtime
                if mtime == last_mtime:
                    continue
                last_mtime = mtime
                # 配置变了：重置所有退避计数
                retry_state.clear()
                with open(cfg_path, "r", encoding="utf-8") as f:
                    new_cfg = _yaml.safe_load(f) or {}
                new_accounts = {a["name"]: a for a in new_cfg.get("accounts", []) if a.get("name")}

                # 新增的账号 → 启动新 runner
                added = set(new_accounts) - set(last_accounts)
                for name in added:
                    if self.only_account and name != self.only_account:
                        continue
                    acc_cfg = new_accounts[name]
                    r = AccountRunner(
                        account_cfg=acc_cfg,
                        shared_cfg={**new_cfg, "_config_path": str(cfg_path)},
                        store=self.store,
                        ai=self.ai,
                        op_semaphore=self.op_sem,
                        dry_run=self.dry_run,
                    )
                    self.runners.append(r)
                    t = asyncio.create_task(self._start_runner_safely(r), name=f"runner:{name}")
                    self._tasks[name] = t
                    logger.info("[hot-reload] Added account: %s", name)

                # 删除的账号 → 取消 runner
                removed = set(last_accounts) - set(new_accounts)
                for name in removed:
                    t = self._tasks.pop(name, None)
                    if t and not t.done():
                        t.cancel()
                    self.runners = [r for r in self.runners if r.name != name]
                    logger.info("[hot-reload] Removed account: %s", name)

                # 配置改了（proxy/bitbrowser）→ 重启该 runner
                changed = [
                    name for name in set(new_accounts) & set(last_accounts)
                    if new_accounts[name] != last_accounts[name]
                ]
                for name in changed:
                    if self.only_account and name != self.only_account:
                        continue
                    old_t = self._tasks.get(name)
                    if old_t:
                        old_t.cancel()
                        try:
                            await asyncio.wait_for(old_t, timeout=15)
                        except (asyncio.CancelledError, asyncio.TimeoutError):
                            pass
                    # 重建 runner
                    self.runners = [r for r in self.runners if r.name != name]
                    r = AccountRunner(
                        account_cfg=new_accounts[name],
                        shared_cfg={**new_cfg, "_config_path": str(cfg_path)},
                        store=self.store,
                        ai=self.ai,
                        op_semaphore=self.op_sem,
                        dry_run=self.dry_run,
                    )
                    self.runners.append(r)
                    self._tasks[name] = asyncio.create_task(self._start_runner_safely(r), name=f"runner:{name}")
                    logger.info("[hot-reload] Reloaded account: %s", name)

                last_accounts = new_accounts
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("[hot-reload] watcher error: %s", e, exc_info=True)

    async def _start_runner_safely(self, r: AccountRunner, delay: float = 2):
        if delay > 0:
            await asyncio.sleep(delay)
        try:
            await r.run()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("[%s] runner crashed: %s", r.name, e, exc_info=True)

    async def login_all(self):
        """逐个账号交互式登录（不能并行，要依次扫码）"""
        for r in self.runners:
            await r.login_interactive()
        self.store.close()

    def request_stop(self):
        logger.info("Stop signal received, shutting down all runners")
        if self._stop is not None:
            self._stop.set()
        for r in self.runners:
            r.request_stop()


# ── 入口 ───────────────────────────────────────────────

def load_config(path: Path) -> dict:
    if not path.exists():
        logger.error("Config not found: %s", path)
        logger.error("Hint: cp config/dy_reply.example.yaml %s", path)
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


async def main():
    parser = argparse.ArgumentParser(description="抖音自动回复脚本（多账号）")
    parser.add_argument("--config", default="config/dy_reply.yaml", help="YAML 配置路径")
    parser.add_argument("--dry-run", action="store_true", help="只抓不发")
    parser.add_argument("--login", action="store_true",
                        help="交互式登录所有账号（首次或 cookie 过期时用）")
    parser.add_argument("--account", default=None,
                        help="只对指定账号生效（搭配 --login 或主循环）")
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    cfg["_config_path"] = args.config  # 让 runner 热读 rules
    orch = MultiAccountOrchestrator(cfg, dry_run=args.dry_run, only_account=args.account)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, orch.request_stop)
        except NotImplementedError:
            pass

    if args.login:
        await orch.login_all()
    else:
        await orch.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
