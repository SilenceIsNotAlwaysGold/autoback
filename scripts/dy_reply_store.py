"""抖音自动回复 — SQLite 状态存储

职责：
- 私信去重：同一用户只回一次（或按天重置）
- 评论去重：按 comment_id 或 (author + text_hash) 去重
- 回复日志：完整记录发生的每一次回复，便于后续统计

单文件工具，脚本模式使用。后续若接入 SaaS，可被 tenant 级 service 替换。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class ReplyStore:
    def __init__(self, db_path: str = "data/dy_reply.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = asyncio.Lock()
        self._init_schema()

    def _init_schema(self):
        cur = self._conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS replied_users (
                account       TEXT NOT NULL,
                user_key      TEXT NOT NULL,
                username      TEXT,
                replied_at    INTEGER NOT NULL,
                PRIMARY KEY (account, user_key)
            );

            CREATE TABLE IF NOT EXISTS replied_comments (
                account       TEXT NOT NULL,
                comment_key   TEXT NOT NULL,
                author        TEXT,
                replied_at    INTEGER NOT NULL,
                PRIMARY KEY (account, comment_key)
            );

            CREATE TABLE IF NOT EXISTS reply_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                account       TEXT NOT NULL,
                source        TEXT NOT NULL,          -- 'pm' | 'comment'
                target        TEXT,                   -- user_key / comment_key
                target_name   TEXT,                   -- username / author
                incoming_msg  TEXT,
                reply_text    TEXT,
                strategy      TEXT,                   -- 'rule' | 'ai' | 'default'
                success       INTEGER NOT NULL,       -- 1/0
                created_at    INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_reply_log_account_time
                ON reply_log(account, created_at DESC);

            -- 会话最后一条消息快照（用于检测新消息，不依赖 badge）
            CREATE TABLE IF NOT EXISTS conversation_seen (
                account       TEXT NOT NULL,
                conv_name     TEXT NOT NULL,
                last_msg      TEXT,
                updated_at    INTEGER NOT NULL,
                PRIMARY KEY (account, conv_name)
            );
            """
        )
        self._conn.commit()

    # ── 会话新消息检测 ────────────────────────────────────

    def get_last_seen_msg(self, account: str, conv_name: str) -> str | None:
        """返回上次记录的 last_msg；首次见到返回 None"""
        cur = self._conn.cursor()
        row = cur.execute(
            "SELECT last_msg FROM conversation_seen WHERE account=? AND conv_name=?",
            (account, conv_name),
        ).fetchone()
        return row[0] if row else None

    async def update_last_seen(self, account: str, conv_name: str, last_msg: str):
        async with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO conversation_seen(account, conv_name, last_msg, updated_at) VALUES (?,?,?,?)",
                (account, conv_name, last_msg or "", int(time.time())),
            )
            self._conn.commit()

    # ── 私信去重 ───────────────────────────────────────────

    def is_user_replied(self, account: str, user_key: str) -> bool:
        cur = self._conn.cursor()
        row = cur.execute(
            "SELECT 1 FROM replied_users WHERE account=? AND user_key=?",
            (account, user_key),
        ).fetchone()
        return row is not None

    def seconds_since_last_reply(self, account: str, user_key: str) -> int | None:
        """距离上次回复该用户多少秒。从未回过返回 None。"""
        cur = self._conn.cursor()
        row = cur.execute(
            "SELECT replied_at FROM replied_users WHERE account=? AND user_key=?",
            (account, user_key),
        ).fetchone()
        if not row:
            return None
        return int(time.time()) - int(row[0])

    async def mark_user_replied(self, account: str, user_key: str, username: str = ""):
        async with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO replied_users(account, user_key, username, replied_at) VALUES (?,?,?,?)",
                (account, user_key, username, int(time.time())),
            )
            self._conn.commit()

    # ── 评论去重 ───────────────────────────────────────────

    @staticmethod
    def comment_key(comment_id: Optional[str], author: str, text: str) -> str:
        """优先用平台 ID，否则用 (author + text) hash 兜底"""
        if comment_id:
            return f"id:{comment_id}"
        raw = f"{author}|{text}".encode("utf-8")
        return "h:" + hashlib.md5(raw).hexdigest()[:16]

    def is_comment_replied(self, account: str, comment_key: str) -> bool:
        cur = self._conn.cursor()
        row = cur.execute(
            "SELECT 1 FROM replied_comments WHERE account=? AND comment_key=?",
            (account, comment_key),
        ).fetchone()
        return row is not None

    async def mark_comment_replied(self, account: str, comment_key: str, author: str = ""):
        async with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO replied_comments(account, comment_key, author, replied_at) VALUES (?,?,?,?)",
                (account, comment_key, author, int(time.time())),
            )
            self._conn.commit()

    # ── 回复日志 ───────────────────────────────────────────

    async def log_reply(
        self,
        account: str,
        source: str,
        target: str,
        target_name: str,
        incoming_msg: str,
        reply_text: str,
        strategy: str,
        success: bool,
    ):
        async with self._lock:
            self._conn.execute(
                """INSERT INTO reply_log
                   (account, source, target, target_name, incoming_msg, reply_text, strategy, success, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    account,
                    source,
                    target,
                    target_name,
                    (incoming_msg or "")[:500],
                    (reply_text or "")[:500],
                    strategy,
                    1 if success else 0,
                    int(time.time()),
                ),
            )
            self._conn.commit()

    # ── 统计 ───────────────────────────────────────────────

    def today_reply_count(self, account: str, source: str = "") -> int:
        """今日已成功回复数（按自然日 0:00 分界）"""
        today_start = int(time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d")))
        sql = "SELECT COUNT(*) FROM reply_log WHERE account=? AND success=1 AND created_at>=?"
        params: tuple = (account, today_start)
        if source:
            sql += " AND source=?"
            params = (account, today_start, source)
        cur = self._conn.cursor()
        return cur.execute(sql, params).fetchone()[0]

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass
