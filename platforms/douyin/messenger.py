"""抖音私信管理 — 消息拉取 + 自动回复

使用统一浏览器引擎，基于 creator.douyin.com/messaging 页面。
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from pathlib import Path
from typing import Any

from platforms.browser.engine import BrowserEngine
from platforms.browser.monitor import health_monitor
from platforms.douyin import selectors as S

logger = logging.getLogger(__name__)


class DouyinMessenger:
    """抖音私信管理器"""

    def __init__(self, engine: BrowserEngine, account_config: dict = None):
        self.engine = engine
        self.page = engine.page
        self._account_config = account_config or {}
        from shared.conversation.memory import ConversationMemory
        self._memory = ConversationMemory(max_history=10, ttl_seconds=3600)
        # 反自触发：记录每个会话最近一次回复时间 + 最近发过的内容
        self._last_reply_ts: dict[str, float] = {}          # conv_name -> monotonic timestamp
        self._recent_sent: dict[str, list[str]] = {}        # conv_name -> [最近 5 条我们发的]
        self.reply_cooldown_sec: float = 15.0               # 冷却时间
        self._observer_lock = asyncio.Lock()                # 防止并发重装 observer

    async def fetch_conversations(self) -> list[dict[str, Any]]:
        """拉取会话列表，返回 [{index, name, last_msg, has_unread}, ...]

        优先从当前 DOM 直接读（不 goto），只有不在消息页才导航一次。
        抖音私信页用 React + 虚拟滚动，只有可见会话在 DOM 中。
        """
        logger.info("[douyin] Fetching conversations...")

        # 当前如果不在消息页才 goto，否则直接读现有 DOM（无流量成本）
        cur_url = self.page.url if self.page else ""
        need_goto = not ("/following/chat" in cur_url or "/messaging" in cur_url)
        if need_goto:
            await self.engine.goto(S.MESSAGING_URL)
            await self.engine.human_delay(2, 3)
            await self.engine.dismiss_popups()
            try:
                await self.page.wait_for_selector(S.IM["conversation_list"], timeout=20000)
                await self.page.wait_for_selector(S.IM["name_selector"], timeout=10000)
                await self.engine.human_delay(1.5, 2.5)
            except Exception as e:
                logger.warning("[douyin] Conversation list not ready (20s): %s", e)
        else:
            # 页面已在消息页 → 确认列表仍在（可能用户手动切了 tab）
            try:
                await self.page.wait_for_selector(S.IM["conversation_list"], timeout=3000)
            except Exception:
                # 列表不见了 → 回退到 goto
                await self.engine.goto(S.MESSAGING_URL)
                await self.engine.human_delay(2, 3)
                await self.engine.dismiss_popups()
                await self.page.wait_for_selector(S.IM["conversation_list"], timeout=15000)

        # 一次性在 JS 里提取全部字段，避免逐个 query_selector 的延迟
        conversations = await self.page.evaluate(r"""() => {
            const items = document.querySelectorAll('.semi-list-item');
            const out = [];
            for (let idx = 0; idx < items.length; idx++) {
                const it = items[idx];
                const name = it.querySelector('[class^="item-header-name-"]')?.textContent?.trim() || '';
                const time = it.querySelector('[class^="item-header-time-"]')?.textContent?.trim() || '';
                const msg = it.querySelector('[class^="item-content-"] [class^="text-"]')?.textContent?.trim() || '';
                // 真正的未读数：badge 内的纯数字
                let unreadCount = 0;
                const countEl = it.querySelector('[class*="badge"][class*="count"]');
                if (countEl) {
                    const t = (countEl.textContent || '').trim();
                    if (/^\d+$/.test(t)) unreadCount = parseInt(t, 10);
                }
                out.push({
                    index: idx,
                    name: name || ('会话' + idx),
                    time,
                    last_msg: msg.slice(0, 80),
                    unread_count: unreadCount,
                    has_unread: unreadCount > 0,
                });
            }
            return out;
        }""")

        logger.info("[douyin] Found %d conversations (%d unread)",
                    len(conversations),
                    sum(1 for c in conversations if c["has_unread"]))
        return conversations

    async def read_messages(self, conversation_index: int, limit: int = 20,
                            conversation_name: str = "") -> list[dict[str, Any]]:
        """读取指定会话的消息

        优先按 NAME 定位会话（列表并发重排时更稳），失败再退回到索引。
        """
        # 等待列表项出现
        try:
            await self.page.wait_for_selector(S.IM["conversation_list"], timeout=15000)
        except Exception:
            logger.warning("[douyin] Conversation list not visible for read_messages (15s)")
            return []

        # 按名字用 Playwright 原生 click 点击（触发完整事件链路）
        target_item = None
        items = await self.page.query_selector_all(S.IM["conversation_list"])
        if conversation_name:
            for it in items:
                try:
                    name_el = await it.query_selector('[class^="item-header-name-"]')
                    if not name_el:
                        continue
                    nm = (await name_el.inner_text()).strip()
                    if nm == conversation_name:
                        target_item = it
                        break
                except Exception:
                    continue

        # 退回到按索引
        if target_item is None:
            if conversation_index < len(items):
                target_item = items[conversation_index]

        if target_item is None:
            logger.warning("[douyin] Target conversation not found: name=%r idx=%d",
                           conversation_name, conversation_index)
            return []

        try:
            await target_item.evaluate("el => el.scrollIntoView({block:'center'})")
            await self.engine.human_delay(0.3, 0.8)
            await target_item.click(timeout=5000)
            logger.info("[douyin] Clicked conversation: %s", conversation_name or f"idx={conversation_index}")
        except Exception as e:
            logger.warning("[douyin] Click failed: %s", e)
            return []

        # 点击后等消息气泡渲染（比 human_delay 更精准）
        try:
            await self.page.wait_for_selector(S.IM["message_item"], timeout=8000)
        except Exception:
            url = self.page.url if self.page else "?"
            logger.warning("[douyin] Message items not visible after click (url=%s, name=%s)",
                          url, conversation_name)
            return []

        # 一次 JS evaluate 抓出 (text, is_self)：用气泡在父容器中线左右位置判断
        # 这比检查 CSS class / flex-direction 更稳——抖音消息气泡向左对齐 = 对方，向右 = 自己
        raw_messages = await self.page.evaluate(r"""(sel) => {
            const out = [];
            const items = document.querySelectorAll(sel);
            for (const el of items) {
                const text = (el.innerText || '').trim();
                if (!text) continue;

                let isSelf = false;
                try {
                    // 找最近的有水平定位语义的祖先（box-item-W 容器 / row 容器）
                    let container = el;
                    for (let i = 0; i < 8 && container; i++) {
                        const cls = (container.className || '').toString().toLowerCase();
                        if (/\bbox-item-w/.test(cls) || /\brow/.test(cls)) break;
                        container = container.parentElement;
                    }
                    if (!container) container = el.parentElement;

                    // 用气泡中心 vs 容器中心判断左右
                    const elRect = el.getBoundingClientRect();
                    const parentRect = (container || el.parentElement).getBoundingClientRect();
                    const elCenter = elRect.left + elRect.width / 2;
                    const parentCenter = parentRect.left + parentRect.width / 2;
                    isSelf = elCenter > parentCenter;
                } catch (e) {}

                out.push({ text: text.slice(0, 500), is_self: isSelf });
            }
            return out;
        }""", S.IM["message_item"])

        messages = raw_messages[-limit:] if raw_messages else []

        if not messages:
            logger.warning("[douyin] No messages parsed for conversation %d (name=%r)",
                           conversation_index, conversation_name)
        else:
            self_count = sum(1 for m in messages if m["is_self"])
            logger.info("[douyin] Read %d messages (self=%d) from %s",
                        len(messages), self_count, conversation_name or f"idx={conversation_index}")
        return messages

    async def send_reply_text(self, text: str) -> bool:
        """在当前会话中发送文本回复

        抖音私信输入框是 contenteditable DIV（不是 textarea），所以用点击+键盘输入。
        发送按钮 .chat-btn 在未输入时是 disabled，输入后才启用。
        """
        t0 = time.monotonic()
        try:
            # 定位输入框容器
            input_el = None
            try:
                input_el = await self.page.wait_for_selector(S.IM["input_selector"], timeout=5000)
            except Exception:
                pass

            if not input_el:
                logger.error("[douyin] Message input not found")
                health_monitor.record("douyin_reply", False)
                return False

            # 点击获得焦点
            await input_el.click()
            await self.engine.human_delay(0.15, 0.3)

            # contenteditable：先全选清空，再键盘输入（不能用 fill）
            await self.page.keyboard.press("Control+A")
            await self.page.keyboard.press("Delete")
            await self.engine.human_delay(0.1, 0.2)
            await self.engine.human_type(text)
            await self.engine.human_delay(0.2, 0.4)

            # 点击发送按钮（优先找未 disabled 的；同一按钮最多等 2 次）
            sent = False
            for sel in S.IM["send_selectors"]:
                try:
                    btn = await self.page.query_selector(sel)
                    if not btn:
                        continue
                    for _ in range(2):
                        is_disabled = await btn.get_attribute("disabled")
                        cls = await btn.get_attribute("class") or ""
                        if is_disabled is not None or "disabled" in cls:
                            logger.warning("[douyin] Send button still disabled, waiting...")
                            await self.engine.human_delay(0.5, 1.0)
                            btn = await self.page.query_selector(sel)
                            if not btn:
                                break
                        else:
                            await btn.click()
                            sent = True
                            break
                    if sent:
                        break
                except Exception:
                    continue

            if not sent:
                logger.info("[douyin] Fallback to Enter key")
                await self.page.keyboard.press("Enter")
                sent = True

            await self.engine.human_delay(0.3, 0.6)

            duration = int((time.monotonic() - t0) * 1000)
            health_monitor.record("douyin_reply", True, duration)
            logger.info("[douyin] Reply sent (%dms): %s", duration, text[:30])
            return True

        except Exception as e:
            health_monitor.record("douyin_reply", False)
            logger.error("[douyin] Send reply failed: %s", e)
            return False

    async def send_reply_image(self, card_path: str) -> bool:
        """发送图片卡片回复（抖音特色：联系方式卡片）"""
        t0 = time.monotonic()
        try:
            p = Path(card_path)
            if not p.exists():
                logger.error("[douyin] Card image not found: %s", card_path)
                health_monitor.record("douyin_reply_image", False)
                return False

            # 查找图片上传 input
            uploaded = False
            for sel in S.IM["upload_image_selectors"]:
                try:
                    el = await self.page.query_selector(sel)
                    if el:
                        await el.set_input_files(str(p.absolute()))
                        uploaded = True
                        break
                except Exception:
                    continue

            if not uploaded:
                logger.error("[douyin] Image upload button not found")
                health_monitor.record("douyin_reply_image", False)
                return False

            await self.engine.human_delay(1.5, 3.0)

            # 发送
            sent = False
            for sel in S.IM["send_selectors"]:
                try:
                    btn = await self.page.query_selector(sel)
                    if btn:
                        await btn.click()
                        sent = True
                        break
                except Exception:
                    continue

            if not sent:
                await self.page.keyboard.press("Enter")

            await self.engine.human_delay(0.5, 1.0)

            duration = int((time.monotonic() - t0) * 1000)
            health_monitor.record("douyin_reply_image", True, duration)
            logger.info("[douyin] Reply image sent (%dms): %s", duration, card_path)
            return True

        except Exception as e:
            health_monitor.record("douyin_reply_image", False)
            logger.error("[douyin] Send reply image failed: %s", e)
            return False

    async def run_realtime(
        self,
        rules_provider,   # 无参可调用，每次返回最新 rules 列表（从 yaml 热读）
        ai_agent=None,
        store=None,
        account_name: str = "default",
        dry_run: bool = False,
        skip_groups: bool = True,
        max_per_day: int = 200,
        max_replies_per_round: int = 20,
        stop_event: asyncio.Event | None = None,
        heartbeat_sec: int = 300,
    ):
        """事件驱动模式：页面常驻 + MutationObserver 秒回

        流程：
          1. 打开私信页 → 等会话列表渲染
          2. 注入 JS observer：监听 .semi-list-item 变化，去抖 500ms 后回调 Python
          3. 等 asyncio.Event → 触发单轮处理（fetch + reply）
          4. 每 heartbeat_sec 秒兜底触发一次（防 observer 丢事件）

        Args:
            rules_provider: callable -> list[dict]，每轮调用获取最新规则（支持热改 YAML）
            stop_event: 外部停止信号
        """
        stop_event = stop_event or asyncio.Event()
        event_queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        processing_lock = asyncio.Lock()

        async def _on_changed(reason: str = ""):
            """JS observer 触发时调用；处理期间也允许入队，但 round 内部会去重"""
            logger.info("[douyin/realtime] 📬 Observer fired: %s", reason or "(no reason)")
            try:
                event_queue.put_nowait(time.time())
            except asyncio.QueueFull:
                pass

        # 暴露给浏览器 JS（重启时可能已注册，忽略重复注册错误）
        try:
            await self.page.expose_function("onConversationChanged", _on_changed)
        except Exception:
            pass

        # 提前调一次 rules_provider 把 _user_reply_cooldown_sec 等配置注入 _account_config
        # 否则 baseline 阶段读到的是默认值
        if callable(rules_provider):
            try:
                rules_provider()
            except Exception:
                pass

        # 打开并等待
        logger.info("[douyin/realtime] Opening %s", S.MESSAGING_URL)
        await self.engine.goto(S.MESSAGING_URL)
        await self.engine.human_delay(3, 5)
        await self.engine.dismiss_popups()
        try:
            await self.page.wait_for_selector(S.IM["conversation_list"], timeout=20000)
            await self.page.wait_for_selector(S.IM["name_selector"], timeout=10000)
            await self.engine.human_delay(1.5, 2.5)
        except Exception as e:
            logger.error("[douyin/realtime] Initial list not ready: %s", e)
            return

        # 基线：只给 **无未读** 的会话设基线；有未读的留空 → 第一轮会处理掉
        if store:
            try:
                baseline_convs = await self.page.evaluate(r"""() => {
                    const items = document.querySelectorAll('.semi-list-item');
                    const out = [];
                    for (const it of items) {
                        const name = it.querySelector('[class^="item-header-name-"]')?.textContent?.trim() || '';
                        const msg = it.querySelector('[class^="item-content-"] [class^="text-"]')?.textContent?.trim() || '';
                        let unread = 0;
                        const c = it.querySelector('[class*="badge"][class*="count"]');
                        if (c) { const t = (c.textContent||'').trim(); if (/^\d+$/.test(t)) unread = parseInt(t); }
                        out.push({ name, msg, unread });
                    }
                    return out;
                }""")
                cooldown_user = int(self._account_config.get("_user_reply_cooldown_sec",
                                                            self.reply_cooldown_sec))
                silent = 0; active = 0; history = 0
                for c in baseline_convs:
                    if not c["name"]:
                        continue
                    if store.get_last_seen_msg(account_name, c["name"]) is not None:
                        continue
                    conv_id = f"dy_{c['name']}"
                    since = store.seconds_since_last_reply(account_name, conv_id)
                    in_cooldown = since is not None and since < cooldown_user
                    if c.get("unread", 0) > 0:
                        # 有未读 → 不基线，留给下一轮处理（last_seen = None，diff 视为"新"）
                        active += 1
                    elif not in_cooldown:
                        # 不在冷却期（含历史未回复或已过冷却时间）→ 留给第一轮处理
                        history += 1
                    else:
                        # 冷却期内且无红点 → 静默基线
                        await store.update_last_seen(account_name, c["name"], c.get("msg", ""))
                        silent += 1
                logger.info("[douyin/realtime] Baseline done: %d silent, %d unread, %d historical-unreplied will be processed",
                            silent, active, history)
            except Exception as e:
                logger.warning("[douyin/realtime] Baseline failed: %s", e)

        # 注入 observer（baseline 不刷页后再装；通过锁串行防并发）
        async with self._observer_lock:
            await self._do_install_observer()
        logger.info("[douyin/realtime] MutationObserver installed (on body, subtree)")

        # 启动后立刻触发一次首轮，处理历史未回复 + 当前未读
        try:
            event_queue.put_nowait(time.time())
        except asyncio.QueueFull:
            pass

        # 事件处理循环
        last_process = 0.0
        while not stop_event.is_set():
            try:
                # 等 observer 事件 OR 心跳超时
                try:
                    await asyncio.wait_for(event_queue.get(), timeout=heartbeat_sec)
                except asyncio.TimeoutError:
                    logger.debug("[douyin/realtime] Heartbeat tick")
                # 合并 100ms 内的连续事件（够用且几乎零延迟）
                await asyncio.sleep(0.1)
                while not event_queue.empty():
                    try: event_queue.get_nowait()
                    except asyncio.QueueEmpty: break

                # 日上限
                if store and store.today_reply_count(account_name, "pm") >= max_per_day:
                    logger.warning("[douyin/realtime] Daily cap reached, sleeping 1h")
                    await asyncio.wait_for(stop_event.wait(), timeout=3600)
                    continue

                async with processing_lock:
                    rules = rules_provider() if callable(rules_provider) else rules_provider
                    await self._run_one_round(
                        rules=rules,
                        ai_agent=ai_agent,
                        store=store,
                        account_name=account_name,
                        dry_run=dry_run,
                        skip_groups=skip_groups,
                        max_replies=max_replies_per_round,
                    )
                last_process = time.time()

                # 停留在当前页面，让 observer 继续监听
                # 只有 observer 不见了才补装（比如页面被跳走）
                still_attached = await self.page.evaluate(
                    "() => !!(window.__dyObs && window.__dyObsInstalled)"
                )
                if not still_attached:
                    logger.info("[douyin/realtime] Observer lost, reinstalling")
                    await self._reinstall_observer()

            except Exception as e:
                logger.error("[douyin/realtime] Loop error: %s", e, exc_info=True)
                await asyncio.sleep(5)

        logger.info("[douyin/realtime] Stopped")

    async def _reinstall_observer(self):
        """页面切换后 observer 丢了，用同样逻辑补装一次（串行，防并发重装）"""
        async with self._observer_lock:
            await self._do_install_observer()

    async def _do_install_observer(self):
        """实际执行 observer 安装（调用者持有锁）"""
        await self.page.evaluate(r"""() => {
            if (window.__dyObs) {
                try { window.__dyObs.disconnect(); } catch (e) {}
                delete window.__dyObs;
            }
            window.__dyObsInstalled = true;
            let timer = null, muCount = 0;
            const trigger = (muts) => {
                muCount += (muts?.length || 1);
                clearTimeout(timer);
                timer = setTimeout(() => {
                    const reason = 'muts=' + muCount;
                    muCount = 0;
                    try { window.onConversationChanged && window.onConversationChanged(reason); }
                    catch (e) {}
                }, 150);
            };
            const obs = new MutationObserver((muts) => trigger(muts));
            obs.observe(document.body, { childList: true, subtree: true, characterData: true });
            window.__dyObs = obs;
        }""")

    async def _ensure_on_messaging(self) -> bool:
        """确保当前在消息列表页，只在 URL 不对时才 goto（避免无意义刷新）"""
        try:
            cur = self.page.url
            if "/following/chat" in cur or "/messaging" in cur:
                return True
            logger.info("[douyin] Not on messaging page (%s), navigating once", cur)
            await self.engine.goto(S.MESSAGING_URL)
            await self.engine.human_delay(2, 3)
            await self.engine.dismiss_popups()
            await self.page.wait_for_selector(S.IM["conversation_list"], timeout=15000)
            return True
        except Exception as e:
            logger.error("[douyin] ensure_on_messaging failed: %s", e)
            return False

    async def _run_one_round(
        self,
        rules: list[dict],
        ai_agent,
        store,
        account_name: str,
        dry_run: bool,
        skip_groups: bool,
        max_replies: int,
    ) -> int:
        """提取自 auto_reply_loop 的单轮处理逻辑（fetch + 过滤 + 回复）"""
        replied_count = 0
        ai = ai_agent
        if ai is None:
            try:
                from shared.ai.agent import load_ai_agent
                ai = load_ai_agent(self._account_config)
            except Exception:
                ai = None

        cooldown_user = int(self._account_config.get("_user_reply_cooldown_sec",
                                                    self.reply_cooldown_sec))
        conversations = await self.fetch_conversations()
        unread_by_badge = {c["index"]: c for c in conversations if c["has_unread"]}
        unread_by_diff: dict[int, dict] = {}
        unread_by_history: dict[int, dict] = {}
        if store:
            for c in conversations:
                conv_id = f"dy_{c['name']}"
                prev = (store.get_last_seen_msg(account_name, c["name"]) or "").strip() or None
                current = (c.get("last_msg", "") or "").strip()
                since = store.seconds_since_last_reply(account_name, conv_id)
                in_cooldown = since is not None and since < cooldown_user
                if prev is None:
                    # 首次见到该会话：
                    #   - 冷却中 → 静默基线（避免反复触发被 _process_conv 拦下）
                    #   - 否则 → 触发处理（含历史未回复消息）
                    if in_cooldown:
                        await store.update_last_seen(account_name, c["name"], current)
                    else:
                        unread_by_history[c["index"]] = c
                elif current and current != prev:
                    # 看到过但 last_msg 变了 → 新消息
                    unread_by_diff[c["index"]] = c

        unread_map = dict(unread_by_diff)
        unread_map.update(unread_by_history)
        unread_map.update(unread_by_badge)
        unread = list(unread_map.values())
        if unread:
            logger.info("[douyin] Triggers: badge=%d, diff=%d, history=%d, total=%d",
                        len(unread_by_badge), len(unread_by_diff),
                        len(unread_by_history), len(unread))
        if skip_groups:
            before = len(unread)
            unread = [c for c in unread if "群" not in c.get("name", "")]
            if before - len(unread):
                logger.info("[douyin] Skipped %d group conversation(s)", before - len(unread))
        if not unread:
            return 0

        for conv in unread:
            if replied_count >= max_replies:
                break
            success = await self._process_conv(conv, rules, ai, store, account_name, dry_run)
            if success:
                replied_count += 1
        logger.info("[douyin] Round done: %d/%d replied", replied_count, len(unread))
        return replied_count

    async def _process_conv(self, conv, rules, ai, store, account_name, dry_run):
        """处理单条会话：读消息 → 匹配规则 → 发送"""
        logger.info("[douyin] Processing unread: %s", conv["name"])
        messages = await self.read_messages(conv["index"], conversation_name=conv.get("name", ""))
        if not messages:
            return False

        # 最后一条是自己发的 → 是用户手动发消息触发的变化，不回复，更新基线防重复触发
        if messages[-1].get("is_self"):
            if store:
                await store.update_last_seen(account_name, conv.get("name", ""), conv.get("last_msg", ""))
            logger.info("[douyin] Skip: last msg is self-sent (%s)", conv.get("name", ""))
            return False

        incoming = [m for m in messages if not m["is_self"]]
        if not incoming:
            if store:
                await store.update_last_seen(account_name, conv.get("name", ""), conv.get("last_msg", ""))
            return False

        last_msg = incoming[-1]["text"]
        conv_id = f"dy_{conv['name']}"

        # 用户级冷却（同一用户 N 秒内不重复回，跨重启持久）
        cooldown_user = int(self._account_config.get("_user_reply_cooldown_sec",
                                                    self.reply_cooldown_sec))
        if store:
            since = store.seconds_since_last_reply(account_name, conv_id)
            if since is not None and since < cooldown_user:
                logger.info("[douyin] User cooldown (%ds < %ds) for %s, skip",
                            since, cooldown_user, conv_id)
                await store.update_last_seen(account_name, conv.get("name", ""), conv.get("last_msg", ""))
                return False

        # 反自触发保险 1：内存冷却（防同一进程内 race，比 store 冷却更短）
        name = conv["name"]
        now_mono = time.monotonic()
        last_ts = self._last_reply_ts.get(name, 0)
        if now_mono - last_ts < self.reply_cooldown_sec:
            logger.info("[douyin] Cooldown (%.1fs) for %s, skip",
                        self.reply_cooldown_sec - (now_mono - last_ts), name)
            return False

        # 反自触发保险 2：对比真实最后一条消息和我们最近发的内容，完全匹配则跳过
        # 用 read_messages 拿到的 last_msg（不是 conversation list 的 snippet）
        last_stripped = last_msg.strip()
        recent_mine = self._recent_sent.get(name, [])
        if last_stripped and any(
            last_stripped == t or last_stripped.startswith(t) or t.startswith(last_stripped)
            for t in recent_mine
        ):
            logger.info("[douyin] Last msg matches recent self-reply, skip: %s", name)
            if store:
                await store.update_last_seen(account_name, name, last_stripped)
            return False

        self._memory.add_message(conv_id, "user", last_msg)

        from shared.rules.engine import match_rule_action
        # 两个独立开关（runner 每轮热读 YAML 时注入到 _account_config）
        kw_on = self._account_config.get("_keyword_enabled", True)
        br_on = self._account_config.get("_brainless_enabled", False)
        brainless = self._account_config.get("_brainless_replies", [])

        # 两个都关 → 不回复
        if not kw_on and not br_on:
            logger.debug("[douyin] Both modes off, no reply for: %s", conv["name"])
            if store:
                await store.update_last_seen(account_name, conv["name"], conv.get("last_msg", ""))
            return False

        action = match_rule_action(
            last_msg, rules,
            keyword_enabled=kw_on,
            brainless_enabled=br_on,
            brainless_replies=brainless,
        )
        strategy = (action or {}).get("strategy", "rule")
        if not action and ai:
            try:
                context = self._memory.get_context(conv_id)
                ai_text = await ai.generate_reply(last_msg, context)
                if ai_text:
                    action = {"text": ai_text, "image": "", "text_after": ""}
                    strategy = "ai"
            except Exception as e:
                logger.warning("[douyin] AI reply failed: %s", e)
        if not action:
            logger.info("[douyin] Skip: no rule/ai matched for %s — msg=%r", conv["name"], last_msg[:60])
            if store:
                await store.update_last_seen(account_name, conv["name"], conv.get("last_msg", ""))
            return False

        await self.engine.human_delay(0.5, 2)

        log_reply_str = ""
        if action.get("image"):
            log_reply_str = f"[IMG:{action['image']}]"
        if action.get("text"):
            log_reply_str = (action["text"] + " " + log_reply_str).strip()
        if action.get("text_after"):
            log_reply_str = (log_reply_str + " / " + action["text_after"]).strip()

        if dry_run:
            logger.info("[douyin][DRY] Would reply to %s: %s → %s",
                        conv["name"], last_msg[:40], log_reply_str[:80])
            success = True
        else:
            sent_any = False
            if action.get("text"):
                if await self.send_reply_text(action["text"]):
                    sent_any = True
            if action.get("image"):
                await self.engine.human_delay(1, 2)
                if await self.send_reply_image(action["image"]):
                    sent_any = True
                else:
                    logger.warning("[douyin] Image send failed (PC not supported?), continuing to text_after")
            if action.get("text_after"):
                await self.engine.human_delay(1, 2)
                if await self.send_reply_text(action["text_after"]):
                    sent_any = True
            success = sent_any

        if store:
            if success and not dry_run:
                await store.mark_user_replied(account_name, conv_id, conv["name"])
            await store.log_reply(
                account=account_name,
                source="pm",
                target=conv_id,
                target_name=conv["name"],
                incoming_msg=last_msg,
                reply_text=log_reply_str,
                strategy=("dry:" + strategy) if dry_run else strategy,
                success=success,
            )

        if success:
            if action.get("text"):
                self._memory.add_message(conv_id, "assistant", action["text"])

            # 记录冷却时间 + 最近发过的内容（反自触发）
            self._last_reply_ts[conv["name"]] = time.monotonic()
            sent_pieces = [action.get("text"), action.get("text_after")]
            sent_pieces = [s for s in sent_pieces if s]
            mine_list = self._recent_sent.setdefault(conv["name"], [])
            mine_list.extend(sent_pieces)
            if len(mine_list) > 5:
                del mine_list[:-5]

            if store and not dry_run:
                # 基线设为我们发的内容（先 text_after 再 text 兜底）
                sent_text = (
                    action.get("text_after")
                    or action.get("text")
                    or conv.get("last_msg", "")
                )
                await store.update_last_seen(account_name, conv["name"], sent_text)

        return success

    async def auto_reply_loop(
        self,
        rules: list[dict],
        max_replies: int = 50,
        poll_interval: int = 30,
        ai_agent=None,
        store=None,
        account_name: str = "default",
        dry_run: bool = False,
        skip_groups: bool = True,
    ) -> list[dict]:
        """自动回复循环（单轮）

        Args:
            rules: [{keywords, reply_text|reply_texts, match_mode, is_default}, ...]
            max_replies: 最大回复数
            poll_interval: 保留字段（当前由调用方控制轮询节奏）
            ai_agent: 显式传入的 AIReplyAgent；为 None 时回退到账号配置里的 load_ai_agent
            store: 可选 ReplyStore，做用户级去重 + 日志
            account_name: 账号名（store 多账号隔离）
            dry_run: 真实拉取，但不真发送

        Returns:
            回复记录列表
        """
        logger.info("[douyin] Starting auto-reply loop (max=%d, dry_run=%s)",
                    max_replies, dry_run)
        reply_log = []
        replied_count = 0

        # AI agent 解析：优先用显式传入的
        ai = ai_agent
        if ai is None:
            try:
                from shared.ai.agent import load_ai_agent
                ai = load_ai_agent(self._account_config)
            except Exception:
                ai = None

        conversations = await self.fetch_conversations()

        # 触发源 1：badge 有未读计数
        unread_by_badge = {c["index"]: c for c in conversations if c["has_unread"]}

        # 触发源 2：last_msg 相比上次记录有变化（抗抖音 badge 不准问题）
        unread_by_diff: dict[int, dict] = {}
        if store:
            for c in conversations:
                prev = store.get_last_seen_msg(account_name, c["name"])
                current = c.get("last_msg", "") or ""
                if prev is None:
                    # 首次见到 → 只记录基线，不回复（避免对历史会话刷屏）
                    await store.update_last_seen(account_name, c["name"], current)
                elif current and current != prev:
                    unread_by_diff[c["index"]] = c

        # 合并两个来源（以 badge 为主）
        unread_map = dict(unread_by_diff)
        unread_map.update(unread_by_badge)
        unread = list(unread_map.values())
        if unread_by_diff or unread_by_badge:
            logger.info("[douyin] Triggers: badge=%d, diff=%d, total=%d",
                        len(unread_by_badge), len(unread_by_diff), len(unread))

        # 过滤群聊（名字含"群"）— 默认开启，避免误发群公告
        if skip_groups:
            before = len(unread)
            unread = [c for c in unread if "群" not in c.get("name", "")]
            skipped = before - len(unread)
            if skipped:
                logger.info("[douyin] Skipped %d group conversation(s)", skipped)

        for conv in unread:
            if replied_count >= max_replies:
                break

            logger.info("[douyin] Processing unread: %s", conv["name"])
            messages = await self.read_messages(conv["index"], conversation_name=conv.get("name", ""))

            if not messages:
                continue

            # 最后一条非自己的消息
            incoming = [m for m in messages if not m["is_self"]]
            if not incoming:
                continue

            last_msg = incoming[-1]["text"]
            conv_id = f"dy_{conv['name']}"

            # 用户级去重（按 conv name 作为 user_key）
            if store and store.is_user_replied(account_name, conv_id):
                logger.debug("[douyin] Skip already-replied user: %s", conv_id)
                await self.engine.goto(S.MESSAGING_URL)
                await self.engine.human_delay(2, 4)
                continue

            self._memory.add_message(conv_id, "user", last_msg)

            # 匹配规则动作 → AI 兜底（AI 仅返回文字）
            from shared.rules.engine import match_rule_action
            # 模式通过 account_config 注入（runner 每轮热读 YAML 时刷新）
            mode = self._account_config.get("_reply_mode", "keyword")
            brainless = self._account_config.get("_brainless_replies", [])
            action = match_rule_action(last_msg, rules, mode=mode, brainless_replies=brainless)
            strategy = "brainless" if mode == "brainless" else "rule"
            if not action and ai:
                try:
                    context = self._memory.get_context(conv_id)
                    ai_text = await ai.generate_reply(last_msg, context)
                    if ai_text:
                        action = {"text": ai_text, "image": "", "text_after": ""}
                        strategy = "ai"
                except Exception as e:
                    logger.warning("[douyin] AI reply failed: %s", e)
            if not action:
                continue

            # 随机延迟（模拟人类思考）
            await self.engine.human_delay(0.5, 2)

            # 构造日志用的字符串
            log_reply_str = ""
            if action.get("image"):
                log_reply_str = f"[IMG:{action['image']}]"
            if action.get("text"):
                log_reply_str = (action["text"] + " " + log_reply_str).strip()
            if action.get("text_after"):
                log_reply_str = (log_reply_str + " / " + action["text_after"]).strip()

            # 发送（三段式：text → image → text_after）
            # 注：抖音 PC 私信不支持上传图片，image 失败时降级到 text_after
            if dry_run:
                logger.info("[douyin][DRY] Would reply to %s: %s → %s",
                            conv["name"], last_msg[:40], log_reply_str[:80])
                success = True
            else:
                success = False
                sent_any = False
                if action.get("text"):
                    if await self.send_reply_text(action["text"]):
                        sent_any = True
                if action.get("image"):
                    await self.engine.human_delay(1, 2)
                    img_ok = await self.send_reply_image(action["image"])
                    if img_ok:
                        sent_any = True
                    else:
                        logger.warning("[douyin] Image send failed (PC not supported?), continuing to text_after")
                if action.get("text_after"):
                    await self.engine.human_delay(1, 2)
                    if await self.send_reply_text(action["text_after"]):
                        sent_any = True
                success = sent_any

            if store:
                if success and not dry_run:
                    await store.mark_user_replied(account_name, conv_id, conv["name"])
                await store.log_reply(
                    account=account_name,
                    source="pm",
                    target=conv_id,
                    target_name=conv["name"],
                    incoming_msg=last_msg,
                    reply_text=log_reply_str,
                    strategy=("dry:" + strategy) if dry_run else strategy,
                    success=success,
                )

            if success:
                if action.get("text"):
                    self._memory.add_message(conv_id, "assistant", action["text"])
                # 更新 last_seen 为对方的 last_msg，防止下轮把自己的回复当作新消息
                if store and not dry_run:
                    await store.update_last_seen(account_name, conv["name"], conv.get("last_msg", ""))
                replied_count += 1
                reply_log.append({
                    "conversation": conv["name"],
                    "incoming_msg": last_msg[:100],
                    "reply_text": log_reply_str[:120],
                    "strategy": strategy,
                    "timestamp": time.time(),
                })

            # 回到会话列表
            await self.engine.goto(S.MESSAGING_URL)
            await self.engine.human_delay(2, 4)

        logger.info("[douyin] Auto-reply done: %d/%d replied", replied_count, len(unread))
        return reply_log

    def _match_rule(self, message: str, rules: list[dict]) -> str | None:
        """匹配回复规则（使用共享规则引擎）"""
        from shared.rules.engine import match_rule
        return match_rule(message, rules)

    async def _is_self_message(self, el) -> bool:
        """判断消息是否是自己发的（JS 遍历 DOM 向上找布局标记，比 CSS class 更稳）"""
        try:
            result = await el.evaluate(r"""(node) => {
                let el = node;
                for (let i = 0; i < 6; i++) {
                    if (!el) break;
                    const cls = (el.className || '').toLowerCase();
                    if (/\b(self|mine|right|sender)\b/.test(cls)) return true;
                    // 抖音用 flex-direction:row-reverse 表示自己发的气泡
                    const style = window.getComputedStyle(el);
                    if (style.flexDirection === 'row-reverse') return true;
                    el = el.parentElement;
                }
                return false;
            }""")
            return bool(result)
        except Exception:
            pass
        return False
