import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import server
from routes import deps


class SettingsR2Test(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_data_dir = deps.DATA_DIR
        self.data_dir = Path(self.temp_dir.name)
        deps.DATA_DIR = self.data_dir
        self.env_keys = [
            "CLOUDFLARE_R2_ACCOUNT_ID",
            "CLOUDFLARE_R2_ENDPOINT_URL",
            "CLOUDFLARE_R2_BUCKET",
            "CLOUDFLARE_R2_ACCESS_KEY_ID",
            "CLOUDFLARE_R2_SECRET_ACCESS_KEY",
            "CLOUDFLARE_R2_PUBLIC_BASE_URL",
            "CLOUDFLARE_R2_KEY_PREFIX",
            "CLOUDFLARE_R2_DELETE_AFTER_USE",
        ]
        self.original_env = {key: os.environ.get(key) for key in self.env_keys}

    def tearDown(self):
        deps.DATA_DIR = self.original_data_dir
        for key, value in self.original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temp_dir.cleanup()

    def test_save_settings_keeps_masked_r2_secret_and_delete_flag(self):
        os.environ["CLOUDFLARE_R2_SECRET_ACCESS_KEY"] = "existing-secret"

        with mock.patch("routes.settings.init_ai_client"), \
                mock.patch("routes.settings.DATA_DIR", self.data_dir), \
                mock.patch("routes.settings.telegram_bot_service.reload_from_env") as reload_bot, \
                TestClient(server.app) as client:
            reload_bot.return_value = None
            response = client.post("/api/settings", json={
                "cloudflare_r2_account_id": "account-id",
                "cloudflare_r2_endpoint_url": "",
                "cloudflare_r2_bucket": "bucket",
                "cloudflare_r2_access_key_id": "access-key-id",
                "cloudflare_r2_secret_access_key": "existing***cret",
                "cloudflare_r2_public_base_url": "https://pub.example.com/",
                "cloudflare_r2_key_prefix": "tmp/asr/",
                "cloudflare_r2_delete_after_use": False,
            })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["success"], True)
        self.assertEqual(os.environ["CLOUDFLARE_R2_SECRET_ACCESS_KEY"], "existing-secret")
        self.assertEqual(os.environ["CLOUDFLARE_R2_PUBLIC_BASE_URL"], "https://pub.example.com")
        self.assertEqual(os.environ["CLOUDFLARE_R2_KEY_PREFIX"], "tmp/asr")
        self.assertEqual(os.environ["CLOUDFLARE_R2_DELETE_AFTER_USE"], "false")


if __name__ == "__main__":
    unittest.main()
