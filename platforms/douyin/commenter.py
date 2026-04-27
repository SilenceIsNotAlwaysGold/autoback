"""抖音评论管理 — 创作中心评论拉取 + 自动回复

依赖 BrowserEngine 的持久化 profile 和人类行为模拟。
数据来源：创作中心 → 互动管理 → 评论（统一处理自己所有视频下的评论）。

设计原则：
- 抗改版：同一语义多选择器兜底，定位策略依次尝试
- 去重：通过 ReplyStore 确保同一条评论不重复回复
- 安全：每次回复后随机延迟，日上限保护
"""
from __future__ import annotations

import logging
import time
from typing import Any

from platforms.browser.engine import BrowserEngine
from platforms.douyin import selectors as S

logger = logging.getLogger(__name__)


class DouyinCommenter:
    def __init__(self, engine: BrowserEngine, account_config: dict | None = None):
        self.engine = engine
        self.page = engine.page
        self._account_config = account_config or {}

    async def fetch_comments(self, limit: int = 30) -> list[dict[str, Any]]:
        """拉取评论管理页的评论列表

        Returns:
            [{index, author, text, comment_id, already_replied}, ...]
        """
        logger.info("[douyin-comment] Fetching comments...")
        await self.engine.goto(S.COMMENT_MANAGE_URL)
        await self.engine.human_delay(2, 4)
        await self.engine.dismiss_popups()

        # 优先点击"未回复"Tab（若存在）
        try:
            await self.engine.click_text("未回复")
            await self.engine.human_delay(1, 2)
        except Exception:
            pass

        rows = await self.page.query_selector_all(S.COMMENT["comment_row"])
        comments: list[dict[str, Any]] = []

        for idx, row in enumerate(rows[:limit]):
            try:
                # 评论 ID：尝试几个可能的属性
                comment_id = None
                for attr in S.COMMENT["comment_id_attr"]:
                    comment_id = await row.get_attribute(attr)
                    if comment_id:
                        break

                # 作者
                author = ""
                try:
                    el = await row.query_selector(S.COMMENT["author_name"])
                    if el:
                        author = (await el.inner_text()).strip()
                except Exception:
                    pass

                # 评论内容
                text = ""
                try:
                    el = await row.query_selector(S.COMMENT["comment_text"])
                    if el:
                        text = (await el.inner_text()).strip()
                except Exception:
                    pass

                # 如果主选择器没抓到，fallback：用整行文本的第二段
                if not text:
                    raw = (await row.inner_text()).strip()
                    parts = [p.strip() for p in raw.split("\n") if p.strip()]
                    if len(parts) >= 2:
                        author = author or parts[0]
                        text = parts[1]

                # 已回复标记
                already_replied = False
                try:
                    badge = await row.query_selector(S.COMMENT["replied_badge"])
                    already_replied = badge is not None
                except Exception:
                    pass

                if not text:
                    continue

                comments.append({
                    "index": idx,
                    "author": author,
                    "text": text[:500],
                    "comment_id": comment_id,
                    "already_replied": already_replied,
                })
            except Exception as e:
                logger.warning("[douyin-comment] Parse row %d failed: %s", idx, e)

        logger.info("[douyin-comment] Found %d comments (%d already replied)",
                    len(comments),
                    sum(1 for c in comments if c["already_replied"]))
        return comments

    async def reply_comment(self, comment_index: int, text: str) -> bool:
        """点开第 N 条评论的"回复"按钮，输入并发送

        抖音评论页结构：
        - 回复按钮：evaluate 找 operations 容器里文字为"回复"的 item-*（排除删除/举报）
        - 回复输入框：contenteditable DIV，placeholder 以"回复 "开头（区分页面顶部默认输入框）
        - 发送按钮：button.douyin-creator-interactive-button（未 disabled 的那个）
        """
        rows = await self.page.query_selector_all(S.COMMENT["comment_row"])
        if comment_index >= len(rows):
            logger.warning("[douyin-comment] Index %d out of range (total=%d)",
                           comment_index, len(rows))
            return False

        row = rows[comment_index]
        t0 = time.monotonic()

        try:
            # 在当前行的 operations 容器里找文字恰为"回复"的 item
            clicked = await row.evaluate(r"""(row) => {
                const ops = row.querySelector('[class^="operations-"]');
                if (!ops) return { ok: false, reason: 'no-operations' };
                for (const item of ops.querySelectorAll('[class^="item-"]')) {
                    if (item.textContent.trim() === '回复') {
                        item.click();
                        return { ok: true };
                    }
                }
                return { ok: false, reason: 'no-reply-item' };
            }""")
            if not clicked.get("ok"):
                logger.warning("[douyin-comment] Reply button click failed: %s",
                               clicked.get("reason"))
                return False

            await self.engine.human_delay(1, 2)

            # 弹出的输入框 placeholder 以"回复 "开头
            input_el = None
            try:
                input_el = await self.page.wait_for_selector(
                    S.COMMENT["reply_input"], timeout=5000
                )
            except Exception:
                # 兜底
                logger.debug("[douyin-comment] Primary reply input selector miss, trying fallback")

            if not input_el:
                logger.warning("[douyin-comment] Reply input not found")
                return False

            # contenteditable div：点击聚焦 → 键盘输入
            await input_el.click()
            await self.engine.human_delay(0.3, 0.8)
            await self.page.keyboard.press("Control+A")
            await self.page.keyboard.press("Delete")
            await self.engine.human_delay(0.2, 0.4)
            await self.engine.human_type(text)
            await self.engine.human_delay(0.6, 1.2)

            # 发送：找未 disabled 的按钮（这时候才启用）
            sent = False
            try:
                send_btn = await self.page.wait_for_selector(
                    S.COMMENT["reply_submit"], timeout=3000
                )
                if send_btn:
                    await send_btn.click()
                    sent = True
            except Exception:
                pass

            if not sent:
                logger.warning("[douyin-comment] Send button not clickable (still disabled?)")
                return False

            await self.engine.human_delay(1.0, 2.0)
            duration = int((time.monotonic() - t0) * 1000)
            logger.info("[douyin-comment] Reply sent (%dms): %s", duration, text[:30])
            return True

        except Exception as e:
            logger.error("[douyin-comment] Reply failed: %s", e)
            return False

    async def auto_reply_loop(
        self,
        rules: list[dict],
        ai_agent=None,
        store=None,
        account_name: str = "default",
        max_replies: int = 30,
        per_reply_delay: tuple[int, int] = (5, 15),
        dry_run: bool = False,
    ) -> list[dict]:
        """评论自动回复主循环（单轮）

        Args:
            rules: 规则列表，见 shared/rules/engine.py
            ai_agent: 可选 AIReplyAgent 实例（规则未命中时兜底）
            store: 可选 ReplyStore，做去重 + 日志
            account_name: 账号名（store 多账号隔离）
            max_replies: 本轮最多回复几条
            per_reply_delay: 每条回复成功后的随机延迟（秒）
            dry_run: 真实拉取，但不真发送
        """
        from shared.rules.engine import match_rule_action

        reply_log: list[dict] = []
        replied_count = 0

        comments = await self.fetch_comments()
        for c in comments:
            if replied_count >= max_replies:
                break
            if c["already_replied"]:
                continue

            key = None
            if store:
                key = store.comment_key(c["comment_id"], c["author"], c["text"])
                if store.is_comment_replied(account_name, key):
                    logger.debug("[douyin-comment] Skip already-replied: %s", key)
                    continue

            # 规则匹配（评论场景：不支持图片，只拼 text + text_after）
            action = match_rule_action(c["text"], rules)
            reply_text = ""
            if action:
                parts = [p for p in [action.get("text"), action.get("text_after")] if p]
                reply_text = " ".join(parts)
                if action.get("image") and not reply_text:
                    logger.debug("[douyin-comment] Rule is image-only, skipping in comment context")
            strategy = "rule"

            # AI 兜底
            if not reply_text and ai_agent:
                try:
                    reply_text = await ai_agent.generate_reply(c["text"], context=None)
                    strategy = "ai"
                except Exception as e:
                    logger.warning("[douyin-comment] AI failed: %s", e)

            if not reply_text:
                continue

            # 发送
            if dry_run:
                logger.info("[douyin-comment][DRY] Would reply to %s: %s → %s",
                            c["author"], c["text"][:40], reply_text[:40])
                success = True
            else:
                success = await self.reply_comment(c["index"], reply_text)
                if success:
                    await self.engine.human_delay(*per_reply_delay)

            if store:
                # dry-run 不写去重表（避免影响后续真跑），但仍记日志便于观察
                if success and key and not dry_run:
                    await store.mark_comment_replied(account_name, key, c["author"])
                await store.log_reply(
                    account=account_name,
                    source="comment",
                    target=key or "",
                    target_name=c["author"],
                    incoming_msg=c["text"],
                    reply_text=reply_text if reply_text else "",
                    strategy=("dry:" + strategy) if dry_run else strategy,
                    success=success,
                )

            if success:
                replied_count += 1
                reply_log.append({
                    "author": c["author"],
                    "incoming": c["text"][:100],
                    "reply": reply_text[:100],
                    "strategy": strategy,
                    "timestamp": time.time(),
                })

                # 发成功后会弹出 sidesheet 浮层挡住后续点击，且 comments 索引会失效
                # （已回复的被过滤后，后续 c["index"] 不再对应原评论）
                # 简单可靠的做法：break 退出本轮，让 orchestrator 下一轮重新 fetch
                try:
                    await self.page.keyboard.press("Escape")
                except Exception:
                    pass
                break

        logger.info("[douyin-comment] Loop done: %d replied", replied_count)
        return reply_log
