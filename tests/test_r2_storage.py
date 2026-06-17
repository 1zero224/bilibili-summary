import os
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest import mock

from routes import r2_storage


class CloudflareR2StorageTest(unittest.TestCase):
    def test_endpoint_url_is_derived_from_account_id(self):
        with mock.patch.dict(os.environ, {
            "CLOUDFLARE_R2_ACCOUNT_ID": "abc123",
            "CLOUDFLARE_R2_ENDPOINT_URL": "",
        }, clear=False):
            self.assertEqual(
                r2_storage.get_cloudflare_r2_endpoint_url(),
                "https://abc123.r2.cloudflarestorage.com",
            )

    def test_build_object_key_uses_prefix_hint_and_hash(self):
        key = r2_storage.build_r2_object_key(
            Path("视频 标题.m4a"),
            key_prefix="/bilibili summary/asr/",
            object_name_hint="BV1xx411c7mD",
            file_hash="0123456789abcdef9999",
        )

        self.assertEqual(key, "bilibili-summary/asr/BV1xx411c7mD-0123456789abcdef.m4a")

    def test_build_public_url_quotes_object_key(self):
        url = r2_storage.build_r2_public_url(
            "https://pub.example.com/",
            "bilibili-summary/asr/a b.m4a",
        )

        self.assertEqual(url, "https://pub.example.com/bilibili-summary/asr/a%20b.m4a")

    def test_create_presigned_get_url_uses_s3_endpoint_and_signed_query(self):
        with mock.patch.dict(os.environ, {
            "CLOUDFLARE_R2_ENDPOINT_URL": "https://abc123.r2.cloudflarestorage.com",
            "CLOUDFLARE_R2_BUCKET": "bucket",
            "CLOUDFLARE_R2_ACCESS_KEY_ID": "access-key-id",
            "CLOUDFLARE_R2_SECRET_ACCESS_KEY": "secret-key",
            "CLOUDFLARE_R2_PUBLIC_BASE_URL": "https://pub.example.com",
        }, clear=False):
            url = r2_storage.create_cloudflare_r2_presigned_get_url(
                "bilibili-summary/asr/a b.m4a",
                expires_seconds=120,
            )

        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "abc123.r2.cloudflarestorage.com")
        self.assertEqual(parsed.path, "/bucket/bilibili-summary/asr/a%20b.m4a")
        self.assertEqual(query["X-Amz-Algorithm"], ["AWS4-HMAC-SHA256"])
        self.assertEqual(query["X-Amz-Expires"], ["120"])
        self.assertEqual(query["X-Amz-SignedHeaders"], ["host"])
        self.assertIn("X-Amz-Signature", query)

    def test_delete_after_use_defaults_to_enabled(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(r2_storage.get_cloudflare_r2_delete_after_use())


if __name__ == "__main__":
    unittest.main()
