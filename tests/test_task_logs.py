import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import server
from routes import deps


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


if __name__ == "__main__":
    unittest.main()
