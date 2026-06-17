"""
Concurrent Bilibili media downloader used by the Whisper pipeline.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import parse_qs, urlparse

import aiohttp
from bilibili_api import video
from bilibili_api.utils.network import Credential


ProgressCallback = Callable[[str], Awaitable[None]]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)
REFERER = "https://www.bilibili.com/"
BVID_RE = re.compile(r"BV[0-9A-Za-z]+")

VIDEO_QUALITY_PRIORITY = (127, 126, 125, 120, 116, 112, 80, 100, 74, 64, 32, 16, 6)
VIDEO_CODEC_PRIORITY = (7, 12, 13)
VIDEO_QUALITY_ORDER = {quality: index for index, quality in enumerate(VIDEO_QUALITY_PRIORITY)}
VIDEO_CODEC_ORDER = {codec: index for index, codec in enumerate(VIDEO_CODEC_PRIORITY)}
AUDIO_QUALITY_PRIORITY = (30251, 30250, 30280, 30232, 100009, 30216, 100008)
AUDIO_QUALITY_ORDER = {quality: index for index, quality in enumerate(AUDIO_QUALITY_PRIORITY)}

DEFAULT_CHUNK_SIZE = 2 * 1024 * 1024
DEFAULT_CHUNK_CONCURRENCY = 16
DEFAULT_TASK_CONCURRENCY = 3
DEFAULT_HTTP_TIMEOUT_SECONDS = 60
DEFAULT_ATTEMPTS = 2
MAX_CHUNK_CONCURRENCY = 64
MAX_TASK_CONCURRENCY = 20

_OUTPUT_LOCKS: dict[Path, asyncio.Lock] = {}
_OUTPUT_LOCKS_GUARD = asyncio.Lock()
_ACTIVE_DOWNLOAD_TASKS = 0
_ACTIVE_DOWNLOAD_CHUNKS = 0
_TASK_LIMIT_CONDITION = asyncio.Condition()
_CHUNK_LIMIT_CONDITION = asyncio.Condition()


@dataclass(frozen=True)
class MediaStream:
    url: str
    backup_urls: tuple[str, ...]
    quality: int
    codec: int
    mime_type: str
    bandwidth: int

    @property
    def urls(self) -> list[str]:
        candidates = [*self.backup_urls, self.url]
        return [url for url in candidates if url]


@dataclass(frozen=True)
class PreparedStream:
    url: str
    content_length: int
    quality: int
    codec: int
    mime_type: str


def get_download_attempts() -> int:
    return _env_int("BILISUMMARY_DOWNLOAD_ATTEMPTS", DEFAULT_ATTEMPTS, 1, 5)


def get_chunk_size() -> int:
    mib = _env_int("BILISUMMARY_DOWNLOAD_CHUNK_SIZE", DEFAULT_CHUNK_SIZE // 1024 // 1024, 1, 64)
    return mib * 1024 * 1024


def get_chunk_concurrency() -> int:
    return _env_int(
        "BILISUMMARY_DOWNLOAD_CHUNK_CONCURRENCY",
        DEFAULT_CHUNK_CONCURRENCY,
        1,
        MAX_CHUNK_CONCURRENCY,
    )


def get_task_concurrency() -> int:
    return _env_int(
        "BILISUMMARY_DOWNLOAD_TASK_CONCURRENCY",
        DEFAULT_TASK_CONCURRENCY,
        1,
        MAX_TASK_CONCURRENCY,
    )


def get_http_timeout_seconds() -> int:
    return _env_int("BILISUMMARY_DOWNLOAD_HTTP_TIMEOUT", DEFAULT_HTTP_TIMEOUT_SECONDS, 10, 600)


def extract_bvid(target: str) -> str:
    match = BVID_RE.search(str(target or ""))
    if not match:
        raise RuntimeError(f"无法从目标中提取 BV 号: {target}")
    return match.group(0)


def page_index_from_target(target: str) -> int:
    parsed = urlparse(str(target or ""))
    values = parse_qs(parsed.query).get("p")
    if not values:
        return 0
    try:
        return max(0, int(values[0]) - 1)
    except (TypeError, ValueError):
        return 0


async def download_bilibili_video(
    target: str,
    output_path: Path,
    temp_root: Path,
    progress: ProgressCallback | None = None,
) -> Path:
    """Download and mux one ordinary Bilibili video page to an mp4 file."""

    output_path = output_path.resolve(strict=False)
    async with await _output_lock(output_path):
        async with _task_slot():
            return await _download_bilibili_video_unlocked(target, output_path, temp_root, progress)


async def _download_bilibili_video_unlocked(
    target: str,
    output_path: Path,
    temp_root: Path,
    progress: ProgressCallback | None,
) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg，无法合并 Bilibili DASH 音视频")

    bvid = extract_bvid(target)
    page_index = page_index_from_target(target)
    await _notify(progress, f"Bilibili 下载准备: {bvid} P{page_index + 1}")

    playurl = await _get_playurl(bvid, page_index)
    videos, audios = _parse_dash_streams(playurl)
    durls = _parse_durl_streams(playurl)
    if not videos:
        if not durls:
            raise RuntimeError("Bilibili playurl 未返回可下载的视频流")
    if not audios:
        if not durls:
            raise RuntimeError("Bilibili playurl 未返回可下载的音频流")

    timeout = aiohttp.ClientTimeout(total=None, sock_connect=20, sock_read=get_http_timeout_seconds())
    headers = {"User-Agent": USER_AGENT, "Referer": REFERER}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        temp_root.mkdir(parents=True, exist_ok=True)
        merged_path = temp_root / "merged.mp4"

        if not videos or not audios:
            await _download_durl_media(session, durls, ffmpeg, temp_root, merged_path, progress)
            return _stage_output(output_path, merged_path)

        await _notify(progress, "选择可用音视频流")
        video_stream = await _prepare_stream(session, videos, _video_stream_sort_key)
        audio_stream = await _prepare_stream(session, audios, _audio_stream_sort_key)

        await _notify(
            progress,
            "已选择媒体流: "
            f"video q={video_stream.quality}, codec={video_stream.codec}; "
            f"audio q={audio_stream.quality}",
        )

        video_path = temp_root / "video.m4s"
        audio_path = temp_root / "audio.m4s"

        await _download_stream(session, video_stream, video_path, "视频", progress)
        await _download_stream(session, audio_stream, audio_path, "音频", progress)

    await _notify(progress, "合并音视频")
    await _merge_media(ffmpeg, video_path, audio_path, merged_path)
    return _stage_output(output_path, merged_path)


def _stage_output(output_path: Path, merged_path: Path) -> Path:
    staging_path = output_path.with_suffix(output_path.suffix + ".download")
    if staging_path.exists():
        staging_path.unlink()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(merged_path), str(staging_path))
    return staging_path


async def _get_playurl(bvid: str, page_index: int) -> dict:
    credential = _current_credential()
    v = video.Video(bvid=bvid, credential=credential)
    try:
        return await v.get_download_url(page_index=page_index)
    except Exception as exc:
        raise RuntimeError(f"获取 Bilibili playurl 失败: {exc}") from exc


def _parse_dash_streams(playurl: dict) -> tuple[list[MediaStream], list[MediaStream]]:
    dash = playurl.get("dash") or {}
    videos = [_media_stream(item) for item in dash.get("video") or []]
    audios = [_media_stream(item) for item in dash.get("audio") or []]

    dolby = dash.get("dolby") or {}
    audios.extend(_media_stream(item) for item in dolby.get("audio") or [])

    flac = dash.get("flac") or {}
    flac_audio = flac.get("audio")
    if isinstance(flac_audio, dict):
        audios.append(_media_stream(flac_audio))

    return videos, audios


def _parse_durl_streams(playurl: dict) -> list[MediaStream]:
    quality = _safe_int(playurl.get("quality"))
    codec = _safe_int(playurl.get("video_codecid"))
    mime_type = str(playurl.get("format") or "")
    return [
        _media_stream({**item, "id": quality, "codecid": codec, "mime_type": mime_type})
        for item in playurl.get("durl") or []
    ]


def _media_stream(item: dict) -> MediaStream:
    backup_urls = item.get("backup_url") or item.get("backupUrl") or []
    if not isinstance(backup_urls, list):
        backup_urls = []
    return MediaStream(
        url=item.get("base_url") or item.get("baseUrl") or item.get("url") or "",
        backup_urls=tuple(str(url) for url in backup_urls if url),
        quality=_safe_int(item.get("id")),
        codec=_safe_int(item.get("codecid")),
        mime_type=str(item.get("mime_type") or ""),
        bandwidth=_safe_int(item.get("bandwidth")),
    )


async def _prepare_stream(
    session: aiohttp.ClientSession,
    streams: list[MediaStream],
    sort_key,
) -> PreparedStream:
    last_error: Exception | None = None
    for stream in sorted(streams, key=sort_key):
        for url in _prefer_upos_url(stream.urls):
            try:
                content_length = await _content_length(session, url)
            except Exception as exc:
                last_error = exc
                continue
            if content_length > 0:
                return PreparedStream(
                    url=url,
                    content_length=content_length,
                    quality=stream.quality,
                    codec=stream.codec,
                    mime_type=stream.mime_type,
                )
    if last_error:
        raise RuntimeError(f"探测媒体流长度失败: {last_error}") from last_error
    raise RuntimeError("没有可用的媒体流地址")


async def _download_stream(
    session: aiohttp.ClientSession,
    stream: PreparedStream,
    output_path: Path,
    label: str,
    progress: ProgressCallback | None,
) -> None:
    chunk_size = get_chunk_size()
    chunks = _build_chunks(stream.content_length, chunk_size)
    await _notify(
        progress,
        f"下载{label}: {stream.content_length / 1024 / 1024:.1f} MiB, "
        f"{len(chunks)} 个分片",
    )

    if output_path.exists():
        output_path.unlink()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as file:
        file.truncate(stream.content_length)

    completed = 0
    completed_lock = asyncio.Lock()
    chunk_queue: asyncio.Queue[tuple[int, int]] = asyncio.Queue()
    for chunk in chunks:
        chunk_queue.put_nowait(chunk)

    async def download_one(start: int, end: int) -> None:
        nonlocal completed
        async with _chunk_slot():
            chunk = await _fetch_range(session, stream.url, start, end)
        await asyncio.to_thread(_write_chunk, output_path, start, chunk)
        async with completed_lock:
            completed += 1
            if completed == len(chunks) or completed == 1 or completed % 10 == 0:
                await _notify(progress, f"{label}下载进度: {completed}/{len(chunks)}")

    async def worker() -> None:
        while True:
            try:
                start, end = chunk_queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                await download_one(start, end)
            finally:
                chunk_queue.task_done()

    try:
        worker_count = min(get_chunk_concurrency(), len(chunks))
        await asyncio.gather(*[worker() for _ in range(worker_count)])
    except Exception:
        if output_path.exists():
            output_path.unlink()
        raise

    actual_size = output_path.stat().st_size
    if actual_size != stream.content_length:
        output_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"{label}下载大小不一致: {actual_size} / {stream.content_length}"
        )


async def _download_durl_media(
    session: aiohttp.ClientSession,
    streams: list[MediaStream],
    ffmpeg: str,
    temp_root: Path,
    output_path: Path,
    progress: ProgressCallback | None,
) -> None:
    await _notify(progress, "DASH 音视频流不完整，回退到单文件媒体流")
    segment_paths = []
    for index, stream in enumerate(streams, start=1):
        prepared = await _prepare_stream(session, [stream], lambda _: (0,))
        segment_path = temp_root / f"durl-{index}.mp4"
        await _download_stream(session, prepared, segment_path, f"媒体片段 {index}", progress)
        segment_paths.append(segment_path)

    if not segment_paths:
        raise RuntimeError("Bilibili durl 未返回可下载的媒体流")
    if len(segment_paths) == 1:
        shutil.move(str(segment_paths[0]), str(output_path))
        return

    await _notify(progress, "合并单文件媒体片段")
    await _concat_media(ffmpeg, segment_paths, output_path)


async def _fetch_range(
    session: aiohttp.ClientSession,
    url: str,
    start: int,
    end: int,
) -> bytes:
    expected_size = end - start + 1
    headers = {"Range": f"bytes={start}-{end}"}
    last_error: Exception | None = None
    for _ in range(get_download_attempts()):
        try:
            async with session.get(url, headers=headers) as response:
                if response.status not in {200, 206}:
                    detail = await response.text(errors="ignore")
                    raise RuntimeError(f"HTTP {response.status}: {detail[:200]}")
                data = await response.read()
                if response.status == 206 and len(data) != expected_size:
                    raise RuntimeError(f"分片大小不一致: {len(data)} / {expected_size}")
                if response.status == 200 and len(data) < expected_size:
                    raise RuntimeError(f"媒体响应过短: {len(data)} / {expected_size}")
                return data if response.status == 206 else data[start : end + 1]
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(0.5)
    raise RuntimeError(f"分片下载失败 ({start}-{end}): {last_error}") from last_error


async def _content_length(session: aiohttp.ClientSession, url: str) -> int:
    async with session.head(url, allow_redirects=True) as response:
        if response.status == 200:
            value = response.headers.get("Content-Length")
            if value and value.isdigit():
                return int(value)

    async with session.get(url, headers={"Range": "bytes=0-0"}) as response:
        if response.status == 206:
            content_range = response.headers.get("Content-Range", "")
            total = content_range.rsplit("/", 1)[-1]
            if total.isdigit():
                response.release()
                return int(total)
        if response.status == 200:
            value = response.headers.get("Content-Length")
            if value and value.isdigit():
                response.release()
                return int(value)
        detail = await response.text(errors="ignore")
        raise RuntimeError(f"HTTP {response.status}: {detail[:200]}")


async def _merge_media(ffmpeg: str, video_path: Path, audio_path: Path, output_path: Path) -> None:
    if output_path.exists():
        output_path.unlink()
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-v",
        "error",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-c",
        "copy",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-y",
        str(output_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        detail = (stderr or stdout).decode(errors="ignore").strip()
        raise RuntimeError(f"ffmpeg 合并失败: {detail or output_path.name}")
    if not output_path.is_file() or output_path.stat().st_size <= 0:
        raise RuntimeError("ffmpeg 未生成有效 mp4 文件")


async def _concat_media(ffmpeg: str, input_paths: list[Path], output_path: Path) -> None:
    if output_path.exists():
        output_path.unlink()
    concat_path = output_path.with_suffix(".concat.txt")
    concat_path.write_text(
        "\n".join(f"file '{path.as_posix()}'" for path in input_paths),
        encoding="utf-8",
    )
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-v",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_path),
        "-c",
        "copy",
        "-y",
        str(output_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    concat_path.unlink(missing_ok=True)
    if proc.returncode != 0:
        detail = (stderr or stdout).decode(errors="ignore").strip()
        raise RuntimeError(f"ffmpeg 片段合并失败: {detail or output_path.name}")
    if not output_path.is_file() or output_path.stat().st_size <= 0:
        raise RuntimeError("ffmpeg 未生成有效片段合并文件")


async def _output_lock(output_path: Path) -> asyncio.Lock:
    async with _OUTPUT_LOCKS_GUARD:
        lock = _OUTPUT_LOCKS.get(output_path)
        if lock is None:
            lock = asyncio.Lock()
            _OUTPUT_LOCKS[output_path] = lock
        return lock


class _task_slot:
    async def __aenter__(self):
        global _ACTIVE_DOWNLOAD_TASKS
        async with _TASK_LIMIT_CONDITION:
            while _ACTIVE_DOWNLOAD_TASKS >= get_task_concurrency():
                await _TASK_LIMIT_CONDITION.wait()
            _ACTIVE_DOWNLOAD_TASKS += 1

    async def __aexit__(self, exc_type, exc, tb):
        global _ACTIVE_DOWNLOAD_TASKS
        async with _TASK_LIMIT_CONDITION:
            _ACTIVE_DOWNLOAD_TASKS = max(0, _ACTIVE_DOWNLOAD_TASKS - 1)
            _TASK_LIMIT_CONDITION.notify_all()


class _chunk_slot:
    async def __aenter__(self):
        global _ACTIVE_DOWNLOAD_CHUNKS
        async with _CHUNK_LIMIT_CONDITION:
            while _ACTIVE_DOWNLOAD_CHUNKS >= get_chunk_concurrency():
                await _CHUNK_LIMIT_CONDITION.wait()
            _ACTIVE_DOWNLOAD_CHUNKS += 1

    async def __aexit__(self, exc_type, exc, tb):
        global _ACTIVE_DOWNLOAD_CHUNKS
        async with _CHUNK_LIMIT_CONDITION:
            _ACTIVE_DOWNLOAD_CHUNKS = max(0, _ACTIVE_DOWNLOAD_CHUNKS - 1)
            _CHUNK_LIMIT_CONDITION.notify_all()


def _write_chunk(path: Path, start: int, data: bytes) -> None:
    with path.open("r+b") as file:
        file.seek(start)
        file.write(data)


def _build_chunks(content_length: int, chunk_size: int) -> list[tuple[int, int]]:
    if content_length <= 0:
        raise RuntimeError("媒体流长度无效")
    chunks = []
    for start in range(0, content_length, chunk_size):
        chunks.append((start, min(start + chunk_size, content_length) - 1))
    return chunks


def _video_stream_sort_key(stream: MediaStream) -> tuple[int, int, int]:
    return (
        VIDEO_QUALITY_ORDER.get(stream.quality, len(VIDEO_QUALITY_ORDER)),
        VIDEO_CODEC_ORDER.get(stream.codec, len(VIDEO_CODEC_ORDER)),
        -stream.bandwidth,
    )


def _audio_stream_sort_key(stream: MediaStream) -> tuple[int, int]:
    return (
        AUDIO_QUALITY_ORDER.get(stream.quality, len(AUDIO_QUALITY_ORDER)),
        -stream.bandwidth,
    )


def _prefer_upos_url(urls: list[str]) -> list[str]:
    return sorted(urls, key=lambda url: 0 if url.startswith("https://upos-") else 1)


def _current_credential() -> Credential | None:
    deps = sys.modules.get("routes.deps")
    credential = getattr(deps, "credential", None) if deps is not None else None
    if credential is not None:
        return credential

    sessdata = _clean_env_value(os.getenv("BILIBILI_SESSION_TOKEN"))
    bili_jct = _clean_env_value(os.getenv("BILIBILI_BILI_JCT"))
    ac_time_value = _clean_env_value(os.getenv("BILIBILI_AC_TIME_VALUE"))
    if not sessdata:
        return None
    return Credential(sessdata=sessdata, bili_jct=bili_jct or None, ac_time_value=ac_time_value or None)


def _clean_env_value(value: str | None) -> str:
    return (value or "").strip().strip("'\"")


def _env_int(name: str, default: int, min_value: int, max_value: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except (AttributeError, TypeError, ValueError):
        return default
    return max(min_value, min(max_value, value))


def _safe_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


async def _notify(progress: ProgressCallback | None, message: str) -> None:
    if progress:
        await progress(message)
