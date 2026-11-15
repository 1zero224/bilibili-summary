"""
小米 Mimo 语音识别客户端。
"""

import asyncio
import base64
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Awaitable, Callable

import aiohttp


ProgressCallback = Callable[[str], Awaitable[None]]

DEFAULT_MIMO_ASR_BASE_URL = "https://api.xiaomimimo.com/v1"
DEFAULT_MIMO_ASR_MODEL = "mimo-v2.5-asr"
DEFAULT_MIMO_ASR_LANGUAGE = "auto"
MIMO_ASR_ALLOWED_LANGUAGES = {"auto", "zh", "en"}
MIMO_ASR_AUDIO_BITRATE = "24k"
MIMO_ASR_AUDIO_MIME_TYPE = "audio/mpeg"
DEFAULT_MIMO_ASR_CHUNK_SECONDS = 300.0
DEFAULT_MIMO_ASR_CONCURRENCY = 5
DEFAULT_MIMO_ASR_MAX_TOKENS = 2048
MIMO_ASR_MAX_BASE64_BYTES = 10_000_000
MIMO_ASR_SUBTITLE_TARGET_CHARS = 42
MIMO_ASR_SUBTITLE_MAX_CHARS = 64
MIMO_ASR_SUBTITLE_MIN_SECONDS = 1.2
MIMO_AUDIO_EXTRACT_TIMEOUT_SECONDS = 1800.0
SENTENCE_END_RE = re.compile(r"([^。！？!?；;\n]+[。！？!?；;]?)")
STRONG_SENTENCE_ENDINGS = "。！？!?"


def _clean_env(value: str | None) -> str:
    return (value or "").strip().strip("'\"")


def _positive_float(value) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed > 0 else 0.0


def _env_float(name: str, default: float, min_value: float, max_value: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(min_value, min(max_value, value))


def _env_int(name: str, default: int, min_value: int, max_value: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(min_value, min(max_value, value))


def get_mimo_asr_api_key() -> str:
    return _clean_env(os.getenv("MIMO_ASR_API_KEY"))


def get_mimo_api_key() -> str:
    return get_mimo_asr_api_key()


def get_mimo_asr_base_url() -> str:
    return _clean_env(os.getenv("MIMO_ASR_BASE_URL")) or DEFAULT_MIMO_ASR_BASE_URL


def get_mimo_asr_model() -> str:
    return DEFAULT_MIMO_ASR_MODEL


def get_mimo_asr_language() -> str:
    value = _clean_env(os.getenv("MIMO_ASR_LANGUAGE")).lower()
    return value if value in MIMO_ASR_ALLOWED_LANGUAGES else DEFAULT_MIMO_ASR_LANGUAGE


def get_mimo_asr_chunk_seconds() -> float:
    return _env_float("MIMO_ASR_CHUNK_SECONDS", DEFAULT_MIMO_ASR_CHUNK_SECONDS, 60.0, 600.0)


def get_mimo_asr_concurrency() -> int:
    return _env_int("MIMO_ASR_CONCURRENCY", DEFAULT_MIMO_ASR_CONCURRENCY, 1, 10)


def get_mimo_asr_max_tokens() -> int:
    return _env_int("MIMO_ASR_MAX_TOKENS", DEFAULT_MIMO_ASR_MAX_TOKENS, 256, 2048)


def normalize_mimo_asr_language(language: str) -> str:
    value = _clean_env(language).lower()
    return value if value in MIMO_ASR_ALLOWED_LANGUAGES else DEFAULT_MIMO_ASR_LANGUAGE


def _chat_completions_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def _headers(api_key: str) -> dict[str, str]:
    return {
        "api-key": api_key,
        "Content-Type": "application/json",
    }


async def _notify(progress: ProgressCallback | None, message: str):
    if progress:
        await progress(message)


def _trim_command_output(output: str, limit: int = 1200) -> str:
    output = output.strip()
    if len(output) <= limit:
        return output
    return output[-limit:]


async def _run_media_command(cmd: list[str], timeout: float, timeout_message: str) -> tuple[int, str, str]:
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
        raise RuntimeError(timeout_message)
    return (
        proc.returncode,
        stdout.decode(errors="ignore"),
        stderr.decode(errors="ignore"),
    )


async def _probe_media_duration(media_path: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0

    returncode, stdout, _ = await _run_media_command(
        [
            ffprobe,
            "-hide_banner",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(media_path),
        ],
        timeout=30.0,
        timeout_message="Mimo ASR 音频时长探测超时",
    )
    if returncode != 0:
        return 0.0

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return 0.0
    return _positive_float((data.get("format") or {}).get("duration"))


def _chunk_ranges(duration: float) -> list[tuple[float, float]]:
    if duration <= 0:
        return [(0.0, 0.0)]

    ranges: list[tuple[float, float]] = []
    start = 0.0
    chunk_seconds = get_mimo_asr_chunk_seconds()
    while start < duration:
        chunk_duration = min(chunk_seconds, duration - start)
        ranges.append((start, chunk_duration))
        start += chunk_duration
    return ranges or [(0.0, 0.0)]


async def _extract_audio_for_mimo(
    media_path: Path,
    temp_root: Path,
    index: int,
    start: float,
    duration: float,
) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg，无法提取 Mimo ASR 音频")

    audio_path = temp_root / f"{media_path.stem}-{index:03d}.mp3"
    cmd = [ffmpeg, "-hide_banner", "-y"]
    if start > 0:
        cmd.extend(["-ss", f"{start:.3f}"])
    cmd.extend([
        "-i",
        str(media_path),
        "-vn",
        "-map",
        "0:a:0",
    ])
    if duration > 0:
        cmd.extend(["-t", f"{duration:.3f}"])
    cmd.extend([
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "libmp3lame",
        "-b:a",
        MIMO_ASR_AUDIO_BITRATE,
        str(audio_path),
    ])

    returncode, stdout, stderr = await _run_media_command(
        cmd,
        MIMO_AUDIO_EXTRACT_TIMEOUT_SECONDS,
        "Mimo ASR 音频提取超时",
    )
    if returncode != 0 or not audio_path.is_file() or audio_path.stat().st_size <= 0:
        detail = _trim_command_output("\n".join(part for part in (stderr, stdout) if part.strip()))
        raise RuntimeError(f"Mimo ASR 音频提取失败: {detail or media_path.name}")

    return audio_path


def _audio_data_url(audio_path: Path) -> str:
    audio_base64 = base64.b64encode(audio_path.read_bytes()).decode("utf-8")
    if len(audio_base64.encode("ascii")) > MIMO_ASR_MAX_BASE64_BYTES:
        raise RuntimeError(
            "Mimo ASR 音频超过 10MB Base64 限制，请缩短视频或调整分段策略"
        )
    return f"data:{MIMO_ASR_AUDIO_MIME_TYPE};base64,{audio_base64}"


def _content_to_text(content) -> str:
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, dict):
        for key in ("text", "content", "transcript"):
            value = content.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    if isinstance(content, list):
        parts = []
        for item in content:
            text = _content_to_text(item)
            if text:
                parts.append(text)
        return "\n".join(parts).strip()

    return ""


def parse_mimo_asr_response(data: dict) -> str:
    choices = data.get("choices") or []
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message") or {}
        text = _content_to_text(message.get("content"))
        if text:
            return text
        text = _content_to_text(choice.get("delta", {}).get("content"))
        if text:
            return text

    output = data.get("output") or {}
    text = _content_to_text(output.get("text") or output.get("content"))
    if text:
        return text

    raise RuntimeError("Mimo ASR 返回空文本")


def _split_long_text(text: str, max_chars: int = MIMO_ASR_SUBTITLE_MAX_CHARS) -> list[str]:
    text = text.strip()
    if len(text) <= max_chars:
        return [text] if text else []

    parts: list[str] = []
    current = ""
    for token in re.split(r"([，,、：:\s]+)", text):
        if not token:
            continue
        if current and len(current) + len(token) > max_chars:
            parts.append(current.strip())
            current = token.lstrip()
        else:
            current += token

    if current.strip():
        parts.append(current.strip())

    result: list[str] = []
    for part in parts:
        if len(part) <= max_chars:
            result.append(part)
            continue
        for idx in range(0, len(part), max_chars):
            chunk = part[idx:idx + max_chars].strip()
            if chunk:
                result.append(chunk)
    return result


def split_mimo_asr_text(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text or "").strip()
    if not normalized:
        return []

    sentences: list[str] = []
    for match in SENTENCE_END_RE.finditer(normalized):
        sentence = match.group(1).strip()
        if sentence:
            sentences.extend(_split_long_text(sentence))
    if not sentences:
        sentences = _split_long_text(normalized)

    merged: list[str] = []
    current = ""
    for sentence in sentences:
        if not current:
            current = sentence
            continue
        if (
            not current.endswith(tuple(STRONG_SENTENCE_ENDINGS))
            and len(current) + len(sentence) <= MIMO_ASR_SUBTITLE_TARGET_CHARS
        ):
            current += sentence
        else:
            merged.append(current)
            current = sentence
    if current:
        merged.append(current)
    return merged


def build_mimo_subtitle_segments(text: str, start: float, duration: float) -> list[dict]:
    sentences = split_mimo_asr_text(text)
    if not sentences:
        return []

    if duration <= 0:
        return [{"from": start, "to": start, "content": sentence} for sentence in sentences]

    weights = [max(1, len(sentence)) for sentence in sentences]
    total_weight = sum(weights)
    cursor = start
    end_limit = start + duration
    segments: list[dict] = []

    for index, (sentence, weight) in enumerate(zip(sentences, weights)):
        if index == len(sentences) - 1:
            end = end_limit
        else:
            share = duration * weight / total_weight
            end = min(end_limit, cursor + max(MIMO_ASR_SUBTITLE_MIN_SECONDS, share))
        if end < cursor:
            end = cursor
        segments.append({
            "from": cursor,
            "to": end,
            "content": sentence,
        })
        cursor = end

    return segments


def _build_mimo_asr_payload(data_url: str, model: str, language: str) -> dict:
    return {
        "model": model,
        "max_tokens": get_mimo_asr_max_tokens(),
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {"data": data_url},
                    }
                ],
            }
        ],
        "asr_options": {"language": language},
    }


async def _read_json_response(resp: aiohttp.ClientResponse) -> dict:
    text = await resp.text()
    if resp.status < 200 or resp.status >= 300:
        if "maximum context length" in text or "Requested token count" in text:
            raise RuntimeError(
                f"Mimo ASR API 返回 HTTP {resp.status}: 音频分段仍超过模型上下文限制，"
                "请调小 MIMO_ASR_CHUNK_SECONDS 或 MIMO_ASR_MAX_TOKENS。"
            )
        raise RuntimeError(f"Mimo ASR API 返回 HTTP {resp.status}: {text[:500]}")
    try:
        return json.loads(text)
    except Exception as exc:
        raise RuntimeError(f"Mimo ASR API 返回无效 JSON: {text[:500]}") from exc


async def _transcribe_audio_data_url(
    session: aiohttp.ClientSession,
    *,
    api_key: str,
    data_url: str,
    model: str,
    language: str,
) -> str:
    payload = _build_mimo_asr_payload(data_url, model, language)

    async with session.post(
        _chat_completions_url(get_mimo_asr_base_url()),
        headers=_headers(api_key),
        json=payload,
    ) as resp:
        data = await _read_json_response(resp)
    return parse_mimo_asr_response(data)


async def _transcribe_mimo_chunk(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    *,
    media_path: Path,
    temp_root: Path,
    index: int,
    total_chunks: int,
    start: float,
    chunk_duration: float,
    api_key: str,
    model: str,
    language: str,
    progress: ProgressCallback | None = None,
) -> list[dict]:
    async with semaphore:
        if total_chunks > 1:
            await _notify(progress, f"Mimo ASR 分段 {index}/{total_chunks}")

        audio_path = await _extract_audio_for_mimo(
            media_path,
            temp_root,
            index,
            start,
            chunk_duration,
        )
        try:
            data_url = _audio_data_url(audio_path)
            text = await _transcribe_audio_data_url(
                session,
                api_key=api_key,
                data_url=data_url,
                model=model,
                language=language,
            )
        finally:
            audio_path.unlink(missing_ok=True)

    content = text.strip()
    if not content:
        return []
    return build_mimo_subtitle_segments(content, start, chunk_duration)


async def transcribe_mimo_media(
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
    api_key = get_mimo_asr_api_key()
    if not api_key:
        raise RuntimeError("未配置 Mimo ASR API Key，请在设置中填写 Mimo ASR API Key")

    model = get_mimo_asr_model()
    language = get_mimo_asr_language()
    concurrency = get_mimo_asr_concurrency()
    total_duration = _positive_float(duration)
    if total_duration <= 0:
        total_duration = await _probe_media_duration(media_path)

    ranges = _chunk_ranges(total_duration)
    await _notify(progress, f"Mimo ASR 转录中 ({model}, language={language}, 并发={concurrency})")

    subtitle_segments: list[dict] = []
    timeout = aiohttp.ClientTimeout(total=600)
    with tempfile.TemporaryDirectory(prefix="bilisummary-mimo-audio-") as temp_root:
        temp_root_path = Path(temp_root)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            semaphore = asyncio.Semaphore(concurrency)
            tasks = [
                asyncio.create_task(_transcribe_mimo_chunk(
                    session,
                    semaphore,
                    media_path=media_path,
                    temp_root=temp_root_path,
                    index=index,
                    total_chunks=len(ranges),
                    start=start,
                    chunk_duration=chunk_duration,
                    api_key=api_key,
                    model=model,
                    language=language,
                    progress=progress,
                ))
                for index, (start, chunk_duration) in enumerate(ranges, start=1)
            ]

            try:
                chunk_results = await asyncio.gather(*tasks)
            except Exception:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise

            for chunk_segments in chunk_results:
                subtitle_segments.extend(chunk_segments)

    transcript_text = "\n".join(item["content"] for item in subtitle_segments).strip()
    if not transcript_text:
        raise RuntimeError("Mimo ASR 返回空文本")
    return transcript_text, subtitle_segments
