import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from fastapi.testclient import TestClient

from scripts import dy_config_ui


class RuntimeTogglesApiTest(unittest.TestCase):
    def test_save_runtime_toggles_updates_yaml(self):
        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "dy_reply.yaml"
            config_path.write_text(
                yaml.safe_dump(
                    {
                        "accounts": [{"name": "dy_acc1"}],
                        "messenger": {"enabled": True, "max_per_day": 200},
                        "commenter": {"enabled": True, "max_per_day": 300},
                        "rules": [{"is_default": True, "reply_text": "hello"}],
                    },
                    allow_unicode=True,
                    sort_keys=False,
                ),
                encoding="utf-8",
            )

            with patch.object(dy_config_ui, "CONFIG_PATH", config_path), patch.object(
                dy_config_ui, "EXAMPLE_PATH", config_path
            ):
                client = TestClient(dy_config_ui.app)
                res = client.post(
                    "/api/runtime-toggles",
                    json={"messenger_enabled": True, "commenter_enabled": True},
                )

            self.assertEqual(200, res.status_code)
            saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            self.assertTrue(saved["messenger"]["enabled"])
            self.assertFalse(saved["commenter"]["enabled"])
            self.assertEqual(300, saved["commenter"]["max_per_day"])

    def test_rejects_disabling_messenger(self):
        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "dy_reply.yaml"
            config_path.write_text("accounts: []\n", encoding="utf-8")

            with patch.object(dy_config_ui, "CONFIG_PATH", config_path), patch.object(
                dy_config_ui, "EXAMPLE_PATH", config_path
            ):
                client = TestClient(dy_config_ui.app)
                res = client.post(
                    "/api/runtime-toggles",
                    json={"messenger_enabled": False, "commenter_enabled": False},
                )

            self.assertEqual(400, res.status_code)


if __name__ == "__main__":
    unittest.main()
