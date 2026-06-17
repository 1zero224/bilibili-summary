"""
阿里云百炼非实时语音识别客户端。
"""

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Awaitable, Callable

import aiohttp

from routes.r2_storage import (
    create_cloudflare_r2_presigned_get_url,
    delete_file_from_cloudflare_r2,
    get_cloudflare_r2_delete_after_use,
    upload_file_to_cloudflare_r2,
)


ProgressCallback = Callable[[str], Awaitable[None]]

DEFAULT_BAILIAN_ASR_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"
DEFAULT_BAILIAN_ASR_MODEL = "qwen3-asr-flash-filetrans"
DEFAULT_BAILIAN_ASR_POLL_INTERVAL_SECONDS = 2.0
DEFAULT_BAILIAN_ASR_TIMEOUT_SECONDS = 7200.0
BAILIAN_AUDIO_EXTRACT_TIMEOUT_SECONDS = 1800.0


def _clean_env(value: str | None) -> str:
    return (value or "").strip().strip("'\"")


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _env_float(name: str, default: float, min_value: float, max_value: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(min_value, min(max_value, value))


def get_bailian_api_key() -> str:
    return _clean_env(os.getenv("BAILIAN_API_KEY")) or _clean_env(os.getenv("DASHSCOPE_API_KEY"))


def get_bailian_asr_base_url() -> str:
    return _clean_env(os.getenv("BAILIAN_ASR_BASE_URL")) or DEFAULT_BAILIAN_ASR_BASE_URL


def get_bailian_asr_model() -> str:
    return _clean_env(os.getenv("BAILIAN_ASR_MODEL")) or DEFAULT_BAILIAN_ASR_MODEL


def get_bailian_asr_language() -> str:
    return _clean_env(os.getenv("BAILIAN_ASR_LANGUAGE"))


def get_bailian_asr_enable_itn() -> bool:
    return _env_bool("BAILIAN_ASR_ENABLE_ITN", False)


def get_bailian_asr_enable_words() -> bool:
    return _env_bool("BAILIAN_ASR_ENABLE_WORDS", True)


def get_bailian_asr_poll_interval() -> float:
    return _env_float(
        "BAILIAN_ASR_POLL_INTERVAL_SECONDS",
        DEFAULT_BAILIAN_ASR_POLL_INTERVAL_SECONDS,
        1.0,
        30.0,
    )


def get_bailian_asr_timeout() -> float:
    return _env_float(
        "BAILIAN_ASR_TIMEOUT_SECONDS",
        DEFAULT_BAILIAN_ASR_TIMEOUT_SECONDS,
        60.0,
        24 * 3600.0,
    )


def _api_url(path: str) -> str:
    return f"{get_bailian_asr_base_url().rstrip('/')}/{path.lstrip('/')}"


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }


async def _notify(progress: ProgressCallback | None, message: str):
    if progress:
        await progress(message)


def _trim_command_output(output: str, limit: int = 1200) -> str:
    output = output.strip()
    if len(output) <= limit:
        return output
    return output[-limit:]


async def _run_media_command(cmd: list[str], timeout: float) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        stdout, stderr = await proc.communicate()
        raise RuntimeError("百炼音频提取超时")
    return (
        proc.returncode,
        stdout.decode(errors="ignore"),
        stderr.decode(errors="ignore"),
    )


async def _extract_audio_for_bailian(
    media_path: Path,
    temp_root: Path,
    progress: ProgressCallback | None = None,
) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg，无法提取百炼转写音频")

    audio_path = temp_root / f"{media_path.stem}.m4a"
    await _notify(progress, "提取百炼转写音频")

    copy_cmd = [
        ffmpeg,
        "-hide_banner",
        "-y",
        "-i",
        str(media_path),
        "-vn",
        "-map",
        "0:a:0",
        "-c:a",
        "copy",
        str(audio_path),
    ]
    returncode, stdout, stderr = await _run_media_command(copy_cmd, BAILIAN_AUDIO_EXTRACT_TIMEOUT_SECONDS)
    if returncode == 0 and audio_path.is_file() and audio_path.stat().st_size > 0:
        return audio_path

    audio_path.unlink(missing_ok=True)
    transcode_cmd = [
        ffmpeg,
        "-hide_banner",
        "-y",
        "-i",
        str(media_path),
        "-vn",
        "-map",
        "0:a:0",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        str(audio_path),
    ]
    returncode, fallback_stdout, fallback_stderr = await _run_media_command(
        transcode_cmd,
        BAILIAN_AUDIO_EXTRACT_TIMEOUT_SECONDS,
    )
    if returncode != 0 or not audio_path.is_file() or audio_path.stat().st_size <= 0:
        detail = _trim_command_output("\n".join(
            part for part in (fallback_stderr, fallback_stdout, stderr, stdout)
            if part.strip()
        ))
        raise RuntimeError(f"百炼音频提取失败: {detail or media_path.name}")

    return audio_path


async def _read_json_response(resp: aiohttp.ClientResponse) -> dict:
    text = await resp.text()
    if resp.status < 200 or resp.status >= 300:
        raise RuntimeError(f"百炼 API 返回 HTTP {resp.status}: {text[:500]}")
    try:
        return json.loads(text)
    except Exception as exc:
        raise RuntimeError(f"百炼 API 返回无效 JSON: {text[:500]}") from exc


def _task_error(output: dict) -> str:
    code = output.get("code") or output.get("task_code") or ""
    message = output.get("message") or output.get("task_message") or "百炼转写任务失败"
    return f"{code}: {message}" if code else message


def _transcription_url(output: dict) -> str:
    result = output.get("result") or {}
    if isinstance(result, dict) and result.get("transcription_url"):
        return result["transcription_url"]

    results = output.get("results") or []
    if results and isinstance(results[0], dict):
        return results[0].get("transcription_url", "")
    return ""


def _milliseconds_to_seconds(value) -> float:
    try:
        return max(0.0, float(value) / 1000.0)
    except (TypeError, ValueError):
        return 0.0


def parse_bailian_transcription_result(data: dict, duration: int | float = 0) -> tuple[str, list[dict]]:
    subtitle_segments: list[dict] = []
    transcripts = data.get("transcripts") or []

    for transcript in transcripts:
        if not isinstance(transcript, dict):
            continue
        sentences = transcript.get("sentences") or []
        for sentence in sentences:
            if not isinstance(sentence, dict):
                continue
            content = (sentence.get("text") or "").strip()
            if not content:
                continue
            start = _milliseconds_to_seconds(sentence.get("begin_time"))
            end = _milliseconds_to_seconds(sentence.get("end_time"))
            subtitle_segments.append({
                "from": start,
                "to": max(end, start),
                "content": content,
            })

        if not sentences:
            content = (transcript.get("text") or "").strip()
            if content:
                subtitle_segments.append({
                    "from": 0.0,
                    "to": float(duration or 0),
                    "content": content,
                })

    subtitle_segments.sort(key=lambda item: (item["from"], item["to"]))
    transcript_text = "\n".join(item["content"] for item in subtitle_segments).strip()
    if not transcript_text:
        raise RuntimeError("百炼 ASR 返回空文本")
    return transcript_text, subtitle_segments


async def transcribe_bailian_media(
    media_path: Path,
    duration: int = 0,
    progress: ProgressCallback | None = None,
    *,
    source_url: str = "",
    bvid: str = "",
    title: str = "",
    output_subdir: str = "",
    media_rel_path: str = "",
) -> tuple[str, list[dict]]:
    api_key = get_bailian_api_key()
    if not api_key:
        raise RuntimeError("未配置百炼 API Key，请在设置中填写百炼 API Key")

    model = get_bailian_asr_model()
    upload_object_key = ""
    parameters = {
        "channel_id": [0],
        "enable_itn": get_bailian_asr_enable_itn(),
        "enable_words": get_bailian_asr_enable_words(),
    }
    language = get_bailian_asr_language()
    if language:
        parameters["language"] = language

    try:
        with tempfile.TemporaryDirectory(prefix="bilisummary-bailian-audio-") as temp_root:
            audio_path = await _extract_audio_for_bailian(media_path, Path(temp_root), progress)
            upload_result = await upload_file_to_cloudflare_r2(
                audio_path,
                object_name_hint=bvid or title or media_path.stem,
                content_type="audio/mp4",
                progress=progress,
            )
            upload_object_key = upload_result.object_key
            file_url = create_cloudflare_r2_presigned_get_url(upload_object_key)

            payload = {
                "model": model,
                "input": {"file_url": file_url},
                "parameters": parameters,
            }

            await _notify(progress, f"百炼转写提交中 ({model})")
            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    _api_url("/services/audio/asr/transcription"),
                    headers=_headers(api_key),
                    json=payload,
                ) as resp:
                    submit_data = await _read_json_response(resp)

                task_id = (submit_data.get("output") or {}).get("task_id")
                if not task_id:
                    raise RuntimeError(f"百炼未返回 task_id: {submit_data}")

                await _notify(progress, f"百炼任务已提交: {task_id}")
                result_url = ""
                elapsed = 0.0
                poll_interval = get_bailian_asr_poll_interval()
                timeout_seconds = get_bailian_asr_timeout()
                last_status = ""
                while elapsed < timeout_seconds:
                    await asyncio.sleep(poll_interval)
                    elapsed += poll_interval
                    async with session.get(
                        _api_url(f"/tasks/{task_id}"),
                        headers=_headers(api_key),
                    ) as resp:
                        query_data = await _read_json_response(resp)

                    output = query_data.get("output") or {}
                    status = (output.get("task_status") or "").upper()
                    if status and status != last_status:
                        await _notify(progress, f"百炼任务状态: {status}")
                        last_status = status
                    if status == "SUCCEEDED":
                        result_url = _transcription_url(output)
                        break
                    if status in {"FAILED", "UNKNOWN"}:
                        raise RuntimeError(_task_error(output))

                if not result_url:
                    raise RuntimeError(f"百炼转写任务超时或未返回 transcription_url: {task_id}")

                await _notify(progress, "下载百炼转写结果")
                async with session.get(result_url) as resp:
                    result_data = await _read_json_response(resp)

        return parse_bailian_transcription_result(result_data, duration)
    finally:
        if upload_object_key and get_cloudflare_r2_delete_after_use():
            try:
                await delete_file_from_cloudflare_r2(upload_object_key, progress=progress)
            except Exception as exc:
                await _notify(progress, f"Cloudflare R2 临时音频删除失败: {exc}")
