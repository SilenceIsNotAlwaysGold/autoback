import unittest

from scripts.dy_config_ui import _setup_status


class SetupStatusTest(unittest.TestCase):
    def test_blocks_placeholder_values(self):
        status = _setup_status({
            "accounts": [
                {
                    "name": "dy_acc2",
                    "proxy_url": "http://user:pass@1.2.3.4:8080",
                    "bitbrowser_id": "",
                },
                {
                    "name": "dy_acc3",
                    "bitbrowser_id": "abc123def456",
                },
            ],
            "rules": [{"is_default": True, "reply_text": "hello"}],
            "ai": {"enabled": False, "api_key": ""},
        })

        self.assertFalse(status["ready_to_start"])
        self.assertGreaterEqual(len(status["warnings"]), 2)

    def test_allows_clean_minimal_config(self):
        status = _setup_status({
            "accounts": [{"name": "dy_acc1", "login_timeout": 180}],
            "rules": [{"is_default": True, "reply_text": "hello"}],
            "ai": {"enabled": False, "api_key": ""},
        })

        self.assertTrue(status["ready_to_start"])
        self.assertEqual([], status["warnings"])

    def test_blocks_enabled_ai_without_key(self):
        status = _setup_status({
            "accounts": [{"name": "dy_acc1"}],
            "rules": [{"is_default": True, "reply_text": "hello"}],
            "ai": {"enabled": True, "api_key": ""},
        })

        self.assertFalse(status["ready_to_start"])
        self.assertIn("AI 已开启但没有填写 API Key。", status["warnings"])

    def test_ignores_placeholder_key_when_ai_disabled(self):
        status = _setup_status({
            "accounts": [{"name": "dy_acc1"}],
            "rules": [{"is_default": True, "reply_text": "hello"}],
            "ai": {"enabled": False, "api_key": "sk-xxx"},
        })

        self.assertTrue(status["ready_to_start"])
        self.assertEqual([], status["warnings"])

    def test_blocks_when_messenger_is_disabled(self):
        status = _setup_status({
            "accounts": [{"name": "dy_acc1"}],
            "messenger": {"enabled": False},
            "commenter": {"enabled": False},
            "rules": [{"is_default": True, "reply_text": "hello"}],
            "ai": {"enabled": False, "api_key": ""},
        })

        self.assertFalse(status["ready_to_start"])
        self.assertIn("私信自动回复已关闭，启动后不会监听私信。", status["warnings"])


if __name__ == "__main__":
    unittest.main()
