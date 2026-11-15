import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import server
from routes import deps
from routes.telegram_bot import TelegramBotConfig, telegram_bot_service


class UrlTargetResolutionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original_video = server.bili_video.Video

    def tearDown(self):
        server.bili_video.Video = self.original_video

    def mock_video_pages(self, pages):
        class FakeVideo:
            def __init__(self, *args, **kwargs):
                pass

            async def get_pages(self):
                return pages

        server.bili_video.Video = FakeVideo

    async def test_bvid_collection_recommendation_url_resolves_to_single_video(self):
        self.mock_video_pages([{"page": 1}])

        targets = await server._resolve_input_targets(
            "https://www.bilibili.com/video/BV1ScPkz4EPy/"
            "?spm_id_from=333.788.recommend_more_video.6"
            "&trackid=web_related_0.router-related-2479604-9xr68.1781604158115.83"
            "&vd_source=a1e59e548f567f2a07ef571f02e2cfbf"
        )

        self.assertEqual(targets, ["BV1ScPkz4EPy"])

    async def test_multipage_first_page_url_without_page_param_is_normalized(self):
        self.mock_video_pages([{"page": 1}, {"page": 2}])

        targets = await server._resolve_input_targets(
            "https://www.bilibili.com/video/BV1834y1F7nH"
            "?spm_id_from=333.788.videopod.episodes"
            "&vd_source=a1e59e548f567f2a07ef571f02e2cfbf"
        )

        self.assertEqual(targets, ["https://www.bilibili.com/video/BV1834y1F7nH?p=1"])

    async def test_explicit_page_url_keeps_single_page_target(self):
        self.mock_video_pages([{"page": 1}, {"page": 2}])

        targets = await server._resolve_input_targets(
            "https://www.bilibili.com/video/BV1834y1F7nH"
            "?spm_id_from=333.788.videopod.episodes"
            "&vd_source=a1e59e548f567f2a07ef571f02e2cfbf"
            "&p=2"
        )

        self.assertEqual(targets, ["https://www.bilibili.com/video/BV1834y1F7nH?p=2"])


class TaskLogPersistenceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_data_dir = deps.DATA_DIR
        deps.DATA_DIR = Path(self.temp_dir.name)
        deps.task_logs.clear()
        deps.progress_tasks.clear()
        deps.progress_subscribers.clear()

    def tearDown(self):
        deps.DATA_DIR = self.original_data_dir
        deps.task_logs.clear()
        deps.progress_tasks.clear()
        deps.progress_subscribers.clear()
        self.temp_dir.cleanup()

    async def test_task_logs_are_persisted_and_reloaded(self):
        deps.register_task("url-test", "url", "URL 视频总结", total=2, meta={"folder": "默认文件夹"})
        await deps.send_progress("url-test", "start", {
            "total": 2,
            "concurrency": 1,
            "model": "test-model",
            "modules": {"detailed_summary": True},
        })
        await deps.send_progress("url-test", "completed", {
            "title": "测试视频",
            "status": "success",
            "path": "folders/默认文件夹/测试视频.md",
        })
        await deps.send_progress("url-test", "done", {
            "total": 2,
            "success": 1,
            "skipped": 0,
            "no_subtitle": 0,
            "errors": 0,
        })

        self.assertTrue((deps.DATA_DIR / "task_logs.json").is_file())

        deps.task_logs.clear()
        deps.load_task_logs()

        detail = deps.get_task_log_detail("url-test")
        self.assertIsNotNone(detail)
        self.assertEqual(detail["status"], "done")
        self.assertEqual(detail["success"], 1)
        self.assertEqual(detail["progress_percent"], 50)
        self.assertTrue(any(event.get("data", {}).get("path") for event in detail["events"]))

    async def test_running_task_is_marked_failed_after_reload(self):
        deps.register_task("url-running", "url", "URL 视频总结", total=1)
        await deps.send_progress("url-running", "start", {
            "total": 1,
            "concurrency": 1,
            "model": "test-model",
            "modules": {"detailed_summary": True},
        })

        deps.task_logs.clear()
        deps.load_task_logs()

        detail = deps.get_task_log_detail("url-running")
        self.assertIsNotNone(detail)
        self.assertEqual(detail["status"], "failed")
        self.assertTrue(any("服务重启" in event["message"] for event in detail["events"]))


class TaskLogApiTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_data_dir = deps.DATA_DIR
        deps.DATA_DIR = Path(self.temp_dir.name)
        deps.task_logs.clear()
        deps.progress_tasks.clear()
        deps.progress_subscribers.clear()

    def tearDown(self):
        deps.DATA_DIR = self.original_data_dir
        deps.task_logs.clear()
        deps.progress_tasks.clear()
        deps.progress_subscribers.clear()
        self.temp_dir.cleanup()

    def test_task_detail_route_returns_persisted_task(self):
        deps.register_task("url-api-test", "url", "URL 视频总结", total=1)

        with TestClient(server.app) as client:
            response = client.get("/api/tasks/url-api-test")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["task_id"], "url-api-test")
        self.assertEqual(payload["progress_percent"], 0)


class TelegramTaskLogTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_data_dir = deps.DATA_DIR
        self.original_config = telegram_bot_service._config
        self.original_session = telegram_bot_service._session
        self.original_summary_tasks = set(telegram_bot_service._summary_tasks)
        deps.DATA_DIR = Path(self.temp_dir.name)
        deps.task_logs.clear()
        deps.progress_tasks.clear()
        deps.progress_subscribers.clear()
        telegram_bot_service._config = TelegramBotConfig(
            enabled=True,
            token="test-token",
            allowed_user_ids=set(),
            output_folder=deps.default_folder_name(),
        )
        telegram_bot_service._summary_tasks.clear()

    def tearDown(self):
        deps.DATA_DIR = self.original_data_dir
        telegram_bot_service._config = self.original_config
        telegram_bot_service._session = self.original_session
        telegram_bot_service._summary_tasks.clear()
        telegram_bot_service._summary_tasks.update(self.original_summary_tasks)
        deps.task_logs.clear()
        deps.progress_tasks.clear()
        deps.progress_subscribers.clear()
        self.temp_dir.cleanup()

    async def test_telegram_update_registers_persisted_task_log(self):
        sent_messages: list[dict] = []

        async def fake_send_message(chat_id, text, reply_to_message_id=None):
            sent_messages.append({
                "chat_id": chat_id,
                "text": text,
                "reply_to_message_id": reply_to_message_id,
            })
            return {"result": {"message_id": 456}}

        async def fake_run_summary_task(*args, **kwargs):
            return None

        update = {
            "message": {
                "message_id": 123,
                "text": "https://www.bilibili.com/video/BV1xx411c7mD",
                "chat": {"id": 10001},
                "from": {"id": 20002, "is_bot": False},
            }
        }

        with mock.patch.object(telegram_bot_service, "_send_message", side_effect=fake_send_message), \
                mock.patch.object(telegram_bot_service, "_run_summary_task", side_effect=fake_run_summary_task):
            await telegram_bot_service._handle_update(update)
            if telegram_bot_service._summary_tasks:
                await asyncio.gather(*list(telegram_bot_service._summary_tasks))

        self.assertEqual(len(sent_messages), 1)
        self.assertTrue((deps.DATA_DIR / "task_logs.json").is_file())

        logs = deps.list_task_logs(task_type="telegram")
        self.assertEqual(logs["total"], 1)
        item = logs["items"][0]
        self.assertEqual(item["type"], "telegram")
        self.assertEqual(item["title"], "Telegram 视频总结")
        self.assertEqual(item["total"], 1)
        self.assertEqual(item["meta"]["chat_id"], 10001)
        self.assertEqual(item["meta"]["user_id"], 20002)
        self.assertEqual(item["meta"]["bvids"], ["BV1xx411c7mD"])


if __name__ == "__main__":
    unittest.main()
