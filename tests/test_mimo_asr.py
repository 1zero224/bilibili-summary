import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from routes.mimo_asr import (
    DEFAULT_MIMO_ASR_MAX_TOKENS,
    _build_mimo_asr_payload,
    _chat_completions_url,
    _chunk_ranges,
    build_mimo_subtitle_segments,
    get_mimo_asr_concurrency,
    normalize_mimo_asr_language,
    parse_mimo_asr_response,
    transcribe_mimo_media,
)
from routes.whisper import get_asr_mode, get_asr_mode_label


class MimoAsrTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.original_env = {
            "ANTHROPIC_AUTH_TOKEN": os.environ.get("ANTHROPIC_AUTH_TOKEN"),
            "ANTHROPIC_BASE_URL": os.environ.get("ANTHROPIC_BASE_URL"),
            "MIMO_API_KEY": os.environ.get("MIMO_API_KEY"),
            "MIMO_ASR_API_KEY": os.environ.get("MIMO_ASR_API_KEY"),
            "MIMO_ASR_BASE_URL": os.environ.get("MIMO_ASR_BASE_URL"),
            "MIMO_ASR_CHUNK_SECONDS": os.environ.get("MIMO_ASR_CHUNK_SECONDS"),
            "MIMO_ASR_CONCURRENCY": os.environ.get("MIMO_ASR_CONCURRENCY"),
            "MIMO_ASR_LANGUAGE": os.environ.get("MIMO_ASR_LANGUAGE"),
            "MIMO_ASR_MAX_TOKENS": os.environ.get("MIMO_ASR_MAX_TOKENS"),
        }

    def tearDown(self):
        for key, value in self.original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temp_dir.cleanup()

    def test_parse_openai_string_content(self):
        data = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "这是一段转写文本。",
                    }
                }
            ]
        }

        self.assertEqual(parse_mimo_asr_response(data), "这是一段转写文本。")

    def test_parse_openai_list_content(self):
        data = {
            "choices": [
                {
                    "message": {
                        "content": [
                            {"type": "text", "text": "第一段"},
                            {"type": "text", "text": "第二段"},
                        ]
                    }
                }
            ]
        }

        self.assertEqual(parse_mimo_asr_response(data), "第一段\n第二段")

    def test_normalize_mimo_asr_language(self):
        self.assertEqual(normalize_mimo_asr_language("zh"), "zh")
        self.assertEqual(normalize_mimo_asr_language("EN"), "en")
        self.assertEqual(normalize_mimo_asr_language(""), "auto")
        self.assertEqual(normalize_mimo_asr_language("ja"), "auto")

    def test_chat_completions_url(self):
        self.assertEqual(
            _chat_completions_url("https://api.xiaomimimo.com"),
            "https://api.xiaomimimo.com/v1/chat/completions",
        )
        self.assertEqual(
            _chat_completions_url("https://api.xiaomimimo.com/v1"),
            "https://api.xiaomimimo.com/v1/chat/completions",
        )

    def test_default_chunk_ranges_limit_single_request_audio(self):
        ranges = _chunk_ranges(934)

        self.assertEqual(ranges, [
            (0.0, 300.0),
            (300.0, 300.0),
            (600.0, 300.0),
            (900.0, 34.0),
        ])

    def test_mimo_asr_concurrency_env_is_bounded(self):
        with mock.patch.dict(os.environ, {"MIMO_ASR_CONCURRENCY": "0"}):
            self.assertEqual(get_mimo_asr_concurrency(), 1)

        with mock.patch.dict(os.environ, {"MIMO_ASR_CONCURRENCY": "3"}):
            self.assertEqual(get_mimo_asr_concurrency(), 3)

        with mock.patch.dict(os.environ, {"MIMO_ASR_CONCURRENCY": "99"}):
            self.assertEqual(get_mimo_asr_concurrency(), 10)

    def test_payload_limits_completion_tokens(self):
        payload = _build_mimo_asr_payload(
            "data:audio/mpeg;base64,AAAA",
            "mimo-v2.5-asr",
            "zh",
        )

        self.assertEqual(payload["max_tokens"], DEFAULT_MIMO_ASR_MAX_TOKENS)
        self.assertEqual(payload["asr_options"], {"language": "zh"})
        self.assertEqual(payload["messages"][0]["content"][0]["type"], "input_audio")

    def test_build_mimo_subtitle_segments_splits_transcript_block(self):
        text = (
            "观众朋友们大家好，这里是小米CS。"
            "我们这期视频是关于如何一个人独立负责一个完整的项目。"
            "首先一个项目的产生，最开始一定是要有需求的。"
            "然后需求会评估，如果觉得可以的话，它就会正式立项。"
        )

        segments = build_mimo_subtitle_segments(text, 300.0, 120.0)

        self.assertGreater(len(segments), 1)
        self.assertEqual(segments[0]["from"], 300.0)
        self.assertEqual(segments[-1]["to"], 420.0)
        self.assertTrue(all(segment["content"] for segment in segments))
        self.assertTrue(all(
            segments[index]["to"] <= segments[index + 1]["from"]
            for index in range(len(segments) - 1)
        ))

    def test_transcribe_mimo_media_processes_chunks_concurrently_in_order(self):
        async def run_case():
            media_path = self.data_dir / "video.mp4"
            media_path.write_bytes(b"placeholder")
            progress_messages = []
            active_requests = 0
            max_active_requests = 0

            async def progress(message: str):
                progress_messages.append(message)

            async def fake_extract_audio(media_path, temp_root, index, start, duration):
                return temp_root / f"chunk-{index}.mp3"

            def fake_audio_data_url(audio_path):
                return f"data:{audio_path.stem}"

            async def fake_transcribe_audio(session, *, api_key, data_url, model, language):
                nonlocal active_requests, max_active_requests
                chunk_index = int(data_url.rsplit("-", 1)[1])
                active_requests += 1
                max_active_requests = max(max_active_requests, active_requests)
                await asyncio.sleep({
                    1: 0.03,
                    2: 0.01,
                    3: 0.02,
                    4: 0.0,
                }[chunk_index])
                active_requests -= 1
                return f"第{chunk_index}段。"

            with mock.patch.dict(os.environ, {
                "MIMO_ASR_API_KEY": "secret",
                "MIMO_ASR_CHUNK_SECONDS": "60",
                "MIMO_ASR_CONCURRENCY": "2",
                "MIMO_ASR_LANGUAGE": "zh",
            }), \
                    mock.patch("routes.mimo_asr._extract_audio_for_mimo", side_effect=fake_extract_audio), \
                    mock.patch("routes.mimo_asr._audio_data_url", side_effect=fake_audio_data_url), \
                    mock.patch("routes.mimo_asr._transcribe_audio_data_url", side_effect=fake_transcribe_audio):
                transcript, segments = await transcribe_mimo_media(
                    media_path,
                    duration=240,
                    progress=progress,
                )

            self.assertEqual(max_active_requests, 2)
            self.assertEqual(transcript, "第1段。\n第2段。\n第3段。\n第4段。")
            self.assertEqual([segment["from"] for segment in segments], [0.0, 60.0, 120.0, 180.0])
            self.assertTrue(any("并发=2" in message for message in progress_messages))

        asyncio.run(run_case())

    def test_mimo_asr_mode_label(self):
        with mock.patch.dict(os.environ, {"ASR_MODE": "mimo"}):
            self.assertEqual(get_asr_mode(), "mimo")
            self.assertEqual(get_asr_mode_label(), "Mimo ASR")

if __name__ == "__main__":
    unittest.main()
