import asyncio
import tempfile
import unittest
from pathlib import Path

from routes import bilibili_downloader as downloader


class BilibiliDownloaderLogicTest(unittest.TestCase):
    def test_extracts_bvid_and_page_index(self):
        target = "https://www.bilibili.com/video/BV1xx411c7mD?p=3&vd_source=test"

        self.assertEqual(downloader.extract_bvid(target), "BV1xx411c7mD")
        self.assertEqual(downloader.page_index_from_target(target), 2)

    def test_page_index_defaults_to_first_page(self):
        self.assertEqual(downloader.page_index_from_target("BV1xx411c7mD"), 0)
        self.assertEqual(downloader.page_index_from_target("https://example.test/?p=bad"), 0)

    def test_parse_dash_streams_includes_backup_dolby_and_flac(self):
        playurl = {
            "dash": {
                "video": [
                    {
                        "base_url": "https://video.example/base.m4s",
                        "backup_url": ["https://upos-video.example/base.m4s"],
                        "id": 80,
                        "codecid": 7,
                        "mime_type": "video/mp4",
                        "bandwidth": 100,
                    }
                ],
                "audio": [
                    {
                        "base_url": "https://audio.example/base.m4s",
                        "backup_url": None,
                        "id": 30280,
                        "codecid": 0,
                    }
                ],
                "dolby": {
                    "audio": [
                        {
                            "base_url": "https://audio.example/dolby.m4s",
                            "id": 30250,
                            "codecid": 0,
                        }
                    ]
                },
                "flac": {
                    "audio": {
                        "base_url": "https://audio.example/flac.m4s",
                        "id": 30251,
                        "codecid": 0,
                    }
                },
            }
        }

        videos, audios = downloader._parse_dash_streams(playurl)

        self.assertEqual(videos[0].urls, ["https://upos-video.example/base.m4s", "https://video.example/base.m4s"])
        self.assertEqual([audio.quality for audio in audios], [30280, 30250, 30251])

    def test_parse_durl_streams(self):
        playurl = {
            "quality": 80,
            "video_codecid": 7,
            "format": "mp4",
            "durl": [
                {
                    "url": "https://durl.example/video.mp4",
                    "backup_url": ["https://upos-durl.example/video.mp4"],
                }
            ],
        }

        streams = downloader._parse_durl_streams(playurl)

        self.assertEqual(streams[0].quality, 80)
        self.assertEqual(streams[0].codec, 7)
        self.assertEqual(streams[0].urls, ["https://upos-durl.example/video.mp4", "https://durl.example/video.mp4"])

    def test_stream_priority_prefers_quality_then_codec(self):
        streams = [
            downloader.MediaStream("u1", (), 80, 12, "video/mp4", 300),
            downloader.MediaStream("u2", (), 80, 7, "video/mp4", 200),
            downloader.MediaStream("u3", (), 64, 7, "video/mp4", 100),
        ]

        selected = sorted(streams, key=downloader._video_stream_sort_key)[0]

        self.assertEqual(selected.url, "u2")

    def test_prefer_upos_url(self):
        urls = [
            "https://cn.example/video.m4s",
            "https://upos-example/video.m4s",
        ]

        self.assertEqual(downloader._prefer_upos_url(urls)[0], "https://upos-example/video.m4s")

    def test_build_chunks(self):
        self.assertEqual(
            downloader._build_chunks(content_length=10, chunk_size=4),
            [(0, 3), (4, 7), (8, 9)],
        )

    def test_write_chunk_at_offset(self):
        with tempfile.TemporaryDirectory() as temp_root:
            path = Path(temp_root) / "media.bin"
            path.write_bytes(b"\x00" * 6)

            downloader._write_chunk(path, 2, b"abc")

            self.assertEqual(path.read_bytes(), b"\x00\x00abc\x00")


class BilibiliDownloaderConcurrencyTest(unittest.IsolatedAsyncioTestCase):
    async def test_same_output_path_is_serialized(self):
        calls = []
        active = 0
        max_active = 0
        original = downloader._download_bilibili_video_unlocked

        async def fake_download(target, output_path, temp_root, progress):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            calls.append(target)
            await asyncio.sleep(0.02)
            active -= 1
            return output_path.with_suffix(output_path.suffix + ".download")

        downloader._download_bilibili_video_unlocked = fake_download
        try:
            with tempfile.TemporaryDirectory() as temp_root:
                output_path = Path(temp_root) / "same.mp4"
                await asyncio.gather(
                    downloader.download_bilibili_video("BV1xx411c7mD", output_path, Path(temp_root) / "a"),
                    downloader.download_bilibili_video("BV1yy411c7mD", output_path, Path(temp_root) / "b"),
                )
        finally:
            downloader._download_bilibili_video_unlocked = original

        self.assertEqual(calls, ["BV1xx411c7mD", "BV1yy411c7mD"])
        self.assertEqual(max_active, 1)


if __name__ == "__main__":
    unittest.main()
