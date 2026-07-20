import unittest

from platforms.douyin import selectors as S
from platforms.douyin.messenger import DouyinMessenger


class FakeStore:
    def get_last_seen_msg(self, account_name, conversation_name):
        return "old"

    def seconds_since_last_reply(self, account_name, conversation_id):
        return None


class FakeEngine:
    def __init__(self):
        self.goto_urls = []
        self.delay_calls = []
        self.dismissed = False

    async def goto(self, url):
        self.goto_urls.append(url)

    async def human_delay(self, low, high):
        self.delay_calls.append((low, high))

    async def dismiss_popups(self):
        self.dismissed = True


class FakePage:
    def __init__(self, url, fail_waits=0):
        self.url = url
        self.fail_waits = fail_waits
        self.waits = []

    async def wait_for_selector(self, selector, timeout=None):
        self.waits.append((selector, timeout))
        if self.fail_waits:
            self.fail_waits -= 1
            raise TimeoutError(selector)
        return object()


class MessengerRealtimeTest(unittest.IsolatedAsyncioTestCase):
    async def test_run_one_round_restores_list_after_processing_conversation(self):
        messenger = DouyinMessenger.__new__(DouyinMessenger)
        messenger._account_config = {"_user_reply_cooldown_sec": 15}
        messenger.reply_cooldown_sec = 15

        events = []
        conversations = [
            {"index": 0, "name": "alice", "last_msg": "new-a", "has_unread": False},
            {"index": 1, "name": "bob", "last_msg": "new-b", "has_unread": False},
        ]

        async def fetch_conversations():
            return conversations

        async def process_conv(conv, rules, ai, store, account_name, dry_run):
            events.append(("process", conv["name"]))
            return True

        async def ensure_on_messaging():
            events.append(("ensure", None))
            return True

        messenger.fetch_conversations = fetch_conversations
        messenger._process_conv = process_conv
        messenger._ensure_on_messaging = ensure_on_messaging

        replied = await messenger._run_one_round(
            rules=[],
            ai_agent=object(),
            store=FakeStore(),
            account_name="dy_acc1",
            dry_run=False,
            skip_groups=False,
            max_replies=10,
        )

        self.assertEqual(2, replied)
        self.assertEqual(
            [
                ("process", "alice"),
                ("ensure", None),
                ("process", "bob"),
                ("ensure", None),
            ],
            events,
        )

    async def test_ensure_on_messaging_restores_when_url_matches_but_list_missing(self):
        messenger = DouyinMessenger.__new__(DouyinMessenger)
        messenger.engine = FakeEngine()
        messenger.page = FakePage(S.MESSAGING_URL, fail_waits=1)

        ok = await messenger._ensure_on_messaging()

        self.assertTrue(ok)
        self.assertEqual([S.MESSAGING_URL], messenger.engine.goto_urls)
        self.assertTrue(messenger.engine.dismissed)
        self.assertEqual(
            [
                (S.IM["conversation_list"], 3000),
                (S.IM["conversation_list"], 15000),
            ],
            messenger.page.waits,
        )


if __name__ == "__main__":
    unittest.main()
