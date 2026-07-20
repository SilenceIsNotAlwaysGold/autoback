import unittest
from unittest.mock import patch

from shared.rules.engine import match_rule_action


RULES = [
    {
        "keywords": "价格,多少钱",
        "match_mode": "contains",
        "reply_text": "价格回复",
    },
    {
        "keywords": "好看",
        "match_mode": "contains",
        "reply_text": "夸夸回复",
    },
    {
        "is_default": True,
        "reply_text": "默认回复",
    },
]


class RuleEngineTest(unittest.TestCase):
    def test_keyword_match_wins(self):
        action = match_rule_action(
            "这个多少钱",
            RULES,
            keyword_enabled=True,
            brainless_enabled=False,
        )

        self.assertEqual("价格回复", action["text"])
        self.assertEqual("keyword", action["strategy"])

    def test_default_rule_used_when_keyword_enabled_and_no_brainless(self):
        action = match_rule_action(
            "随便问一句",
            RULES,
            keyword_enabled=True,
            brainless_enabled=False,
        )

        self.assertEqual("默认回复", action["text"])
        self.assertEqual("default", action["strategy"])

    def test_brainless_fallback_wins_over_default_rule(self):
        with patch("shared.rules.engine.random.choice", return_value="无脑回复"):
            action = match_rule_action(
                "随便问一句",
                RULES,
                keyword_enabled=True,
                brainless_enabled=True,
                brainless_replies=["无脑回复"],
            )

        self.assertEqual("无脑回复", action["text"])
        self.assertEqual("brainless", action["strategy"])

    def test_keyword_still_wins_when_brainless_enabled(self):
        with patch("shared.rules.engine.random.choice", return_value="无脑回复"):
            action = match_rule_action(
                "好看",
                RULES,
                keyword_enabled=True,
                brainless_enabled=True,
                brainless_replies=["无脑回复"],
            )

        self.assertEqual("夸夸回复", action["text"])
        self.assertEqual("keyword", action["strategy"])

    def test_brainless_only_ignores_keyword_rules(self):
        with patch("shared.rules.engine.random.choice", return_value="无脑回复"):
            action = match_rule_action(
                "好看",
                RULES,
                keyword_enabled=False,
                brainless_enabled=True,
                brainless_replies=["无脑回复"],
            )

        self.assertEqual("无脑回复", action["text"])
        self.assertEqual("brainless", action["strategy"])

    def test_all_off_returns_none(self):
        action = match_rule_action(
            "好看",
            RULES,
            keyword_enabled=False,
            brainless_enabled=False,
            brainless_replies=["无脑回复"],
        )

        self.assertIsNone(action)


if __name__ == "__main__":
    unittest.main()
