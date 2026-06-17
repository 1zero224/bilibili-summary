"""
语音识别辅助函数，支持本地 Whisper 和百炼 ASR。
"""

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Awaitable, Callable

from routes.bilibili_downloader import download_bilibili_video
from routes.bailian_asr import transcribe_bailian_media


ProgressCallback = Callable[[str], Awaitable[None]]

_LOCAL_MODEL_CACHE = {}
_LOCAL_TRANSCRIBE_SEMAPHORE = asyncio.Semaphore(1)
_MEDIA_MIN_DURATION_SECONDS = 1.0
_MEDIA_VALIDATION_DECODE_SECONDS = 8.0
_MEDIA_VALIDATION_TIMEOUT_SECONDS = 45.0
_MEDIA_DOWNLOAD_ATTEMPTS = 2
_WHISPER_LANGUAGE = "zh"
_WHISPER_INITIAL_PROMPT = (
    "以下是普通话视频的简体中文字幕。"
    "请使用简体中文、阿拉伯数字和常见技术术语。"
)
_ASR_MODE_LOCAL = "local"
_ASR_MODE_BAILIAN = "bailian"


def get_whisper_model() -> str:
    return os.getenv("WHISPER_MODEL", "whisper-tiny").strip() or "whisper-tiny"


def get_whisper_device() -> str:
    return os.getenv("WHISPER_DEVICE", "auto").strip() or "auto"


def get_whisper_compute_type() -> str:
    return os.getenv("WHISPER_COMPUTE_TYPE", "default").strip() or "default"


def get_asr_mode() -> str:
    mode = os.getenv("ASR_MODE", _ASR_MODE_LOCAL).strip().lower()
    return mode if mode in {_ASR_MODE_LOCAL, _ASR_MODE_BAILIAN} else _ASR_MODE_LOCAL


def get_asr_mode_label() -> str:
    return "百炼" if get_asr_mode() == _ASR_MODE_BAILIAN else "本地 Whisper"


def using_bailian_asr() -> bool:
    return get_asr_mode() == _ASR_MODE_BAILIAN


def get_whisper_model_dir() -> Path:
    data_dir = Path(os.environ.get("BILISUMMARY_DATA_DIR", Path(__file__).resolve().parents[1]))
    return data_dir / "models" / "whisper"


def _normalize_whisper_model(model: str) -> str:
    value = model.strip()
    if not value:
        return "tiny"
    if Path(value).exists():
        return value
    if "/" in value:
        return value
    if value.startswith("whisper-"):
        return value.removeprefix("whisper-")
    return value


async def _notify(progress: ProgressCallback | None, message: str):
    if progress:
        await progress(message)


def _positive_float(value) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed > 0 else 0.0


def _media_duration_tolerance(expected_duration: float) -> float:
    return max(5.0, min(20.0, expected_duration * 0.02))


def _trim_command_output(output: str, limit: int = 1200) -> str:
    output = output.strip()
    if len(output) <= limit:
        return output
    return output[-limit:]


def _quarantine_invalid_media(media_path: Path):
    if not media_path.exists():
        return
    invalid_path = media_path.with_suffix(media_path.suffix + ".invalid")
    if invalid_path.exists():
        invalid_path.unlink()
    media_path.replace(invalid_path)


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
        raise RuntimeError("媒体校验超时")
    return (
        proc.returncode,
        stdout.decode(errors="ignore"),
        stderr.decode(errors="ignore"),
    )


async def _probe_media(media_path: Path) -> dict:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("未找到 ffprobe，无法校验本地视频")

    returncode, stdout, stderr = await _run_media_command(
        [
            ffprobe,
            "-hide_banner",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(media_path),
        ],
        timeout=30.0,
    )
    if returncode != 0:
        detail = _trim_command_output(stderr or stdout)
        raise RuntimeError(f"ffprobe 媒体校验失败: {detail or media_path.name}")
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe 返回无效 JSON: {exc}") from exc


def _stream_duration(stream: dict, fallback: float) -> float:
    return _positive_float(stream.get("duration")) or fallback


def _validate_media_probe(probe: dict, expected_duration: int | float = 0) -> float:
    streams = probe.get("streams") or []
    format_info = probe.get("format") or {}
    format_duration = _positive_float(format_info.get("duration"))
    video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio_stream = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)

    if not video_stream:
        raise RuntimeError("本地视频校验失败: 缺少视频轨")
    if not audio_stream:
        raise RuntimeError("本地视频校验失败: 缺少音频轨，无法转录")
    if format_duration < _MEDIA_MIN_DURATION_SECONDS:
        raise RuntimeError("本地视频校验失败: 容器时长无效")

    video_duration = _stream_duration(video_stream, format_duration)
    audio_duration = _stream_duration(audio_stream, format_duration)
    reference_duration = _positive_float(expected_duration) or max(format_duration, video_duration, audio_duration)
    tolerance = _media_duration_tolerance(reference_duration)

    if video_duration < _MEDIA_MIN_DURATION_SECONDS:
        raise RuntimeError("本地视频校验失败: 视频轨时长无效")
    if audio_duration < _MEDIA_MIN_DURATION_SECONDS:
        raise RuntimeError("本地视频校验失败: 音频轨时长无效")
    if video_duration + tolerance < reference_duration:
        raise RuntimeError(
            "本地视频校验失败: "
            f"视频轨过短 ({video_duration:.1f}s / 预期 {reference_duration:.1f}s)"
        )
    if audio_duration + tolerance < reference_duration:
        raise RuntimeError(
            "本地视频校验失败: "
            f"音频轨过短 ({audio_duration:.1f}s / 预期 {reference_duration:.1f}s)"
        )
    if abs(video_duration - audio_duration) > tolerance:
        raise RuntimeError(
            "本地视频校验失败: "
            f"音视频时长不一致 (video={video_duration:.1f}s, audio={audio_duration:.1f}s)"
        )

    return min(reference_duration, format_duration, video_duration)


async def _validate_video_tail_decode(media_path: Path, duration: float):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg，无法校验本地视频")

    decode_seconds = min(_MEDIA_VALIDATION_DECODE_SECONDS, max(1.0, duration))
    start = max(0.0, duration - decode_seconds)
    returncode, stdout, stderr = await _run_media_command(
        [
            ffmpeg,
            "-hide_banner",
            "-v",
            "error",
            "-xerror",
            "-ss",
            f"{start:.3f}",
            "-i",
            str(media_path),
            "-t",
            f"{decode_seconds:.3f}",
            "-map",
            "0:v:0",
            "-an",
            "-sn",
            "-dn",
            "-f",
            "null",
            "-",
        ],
        timeout=_MEDIA_VALIDATION_TIMEOUT_SECONDS,
    )
    detail = _trim_command_output("\n".join(part for part in (stderr, stdout) if part.strip()))
    if returncode != 0 or detail:
        raise RuntimeError(f"本地视频校验失败: 视频尾部无法正常解码: {detail or media_path.name}")


async def validate_local_media(
    media_path: Path,
    expected_duration: int | float = 0,
    progress: ProgressCallback | None = None,
) -> Path:
    if not media_path.is_file() or media_path.stat().st_size <= 0:
        raise RuntimeError("本地视频校验失败: 文件不存在或为空")

    await _notify(progress, "校验本地视频文件")
    probe = await _probe_media(media_path)
    tail_duration = _validate_media_probe(probe, expected_duration)
    await _validate_video_tail_decode(media_path, tail_duration)
    return media_path


async def download_bilibili_media(
    target: str,
    output_path: Path,
    expected_duration: int | float = 0,
    progress: ProgressCallback | None = None,
) -> Path:
    if output_path.is_file() and output_path.stat().st_size > 0:
        try:
            await validate_local_media(output_path, expected_duration, progress)
            await _notify(progress, "复用本地视频文件")
            return output_path
        except RuntimeError as exc:
            await _notify(progress, f"本地视频校验失败，重新下载: {exc}")
            _quarantine_invalid_media(output_path)

    if not shutil.which("ffmpeg"):
        raise RuntimeError("未找到 ffmpeg，无法保存本地视频")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bilisummary-download-") as temp_root:
        temp_root_path = Path(temp_root)

        last_error: RuntimeError | None = None
        for attempt in range(1, _MEDIA_DOWNLOAD_ATTEMPTS + 1):
            if attempt > 1:
                await _notify(progress, f"重新下载本地视频 ({attempt}/{_MEDIA_DOWNLOAD_ATTEMPTS})")
            else:
                await _notify(progress, "下载 Bilibili 本地视频")

            staging_path = output_path.with_suffix(output_path.suffix + ".download")
            try:
                attempt_dir = temp_root_path / f"attempt-{attempt}"
                shutil.rmtree(attempt_dir, ignore_errors=True)
                staging_path = await download_bilibili_video(target, output_path, attempt_dir, progress)
                await validate_local_media(staging_path, expected_duration, progress)
                staging_path.replace(output_path)
                return output_path
            except RuntimeError as exc:
                last_error = exc
                if staging_path.exists():
                    staging_path.unlink()
                if attempt >= _MEDIA_DOWNLOAD_ATTEMPTS:
                    raise
                await _notify(progress, f"本地视频下载校验失败: {exc}")

        raise last_error or RuntimeError("本地视频下载失败")


def _get_local_whisper_model(model_name: str, device: str, compute_type: str):
    from faster_whisper import WhisperModel

    normalized_model = _normalize_whisper_model(model_name)
    cache_key = (normalized_model, device, compute_type)
    if cache_key not in _LOCAL_MODEL_CACHE:
        model_dir = get_whisper_model_dir()
        model_dir.mkdir(parents=True, exist_ok=True)
        _LOCAL_MODEL_CACHE[cache_key] = WhisperModel(
            normalized_model,
            device=device,
            compute_type=compute_type,
            download_root=str(model_dir),
        )
    return _LOCAL_MODEL_CACHE[cache_key]


def _transcribe_with_local_whisper(
    media_path: Path,
    model_name: str,
    device: str,
    compute_type: str,
    notify_sync,
) -> tuple[str, list[dict]]:
    model = _get_local_whisper_model(model_name, device, compute_type)
    notify_sync(f"本地 Whisper 模型已加载 ({model_name}, device={device}, compute={compute_type})")
    segments_iter, info = model.transcribe(
        str(media_path),
        language=_WHISPER_LANGUAGE,
        initial_prompt=_WHISPER_INITIAL_PROMPT,
        vad_filter=True,
        beam_size=5,
    )
    if info.language:
        notify_sync(f"识别语言: {info.language} ({info.language_probability:.2f})")

    subtitle_segments = []
    for idx, segment in enumerate(segments_iter, start=1):
        content = segment.text.strip()
        if not content:
            continue
        subtitle_segments.append({
            "from": float(segment.start),
            "to": float(segment.end),
            "content": content,
        })
        if idx == 1 or idx % 10 == 0:
            notify_sync(f"本地转录进度: {int(segment.end)} 秒")

    transcript = "\n".join(item["content"] for item in subtitle_segments).strip()
    if not transcript:
        raise RuntimeError("本地 Whisper 返回空文本")

    return transcript, subtitle_segments


async def transcribe_local_media(
    media_path: Path,
    duration: int = 0,
    progress: ProgressCallback | None = None,
) -> tuple[str, list[dict]]:
    model_name = get_whisper_model()
    device = get_whisper_device()
    compute_type = get_whisper_compute_type()
    await _notify(progress, "读取本地视频音频")
    await _notify(progress, f"本地 Whisper 转录中 ({model_name})")

    loop = asyncio.get_running_loop()

    def notify_sync(message: str):
        if progress:
            asyncio.run_coroutine_threadsafe(progress(message), loop)

    async with _LOCAL_TRANSCRIBE_SEMAPHORE:
        return await asyncio.to_thread(
            _transcribe_with_local_whisper,
            media_path,
            model_name,
            device,
            compute_type,
            notify_sync,
        )


async def transcribe_media(
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
    if using_bailian_asr():
        await _notify(progress, "百炼字幕识别模式已启用")
        return await transcribe_bailian_media(
            media_path,
            duration=duration,
            progress=progress,
            source_url=source_url,
            bvid=bvid,
            title=title,
            output_subdir=output_subdir,
            media_rel_path=media_rel_path,
        )

    return await transcribe_local_media(
        media_path,
        duration=duration,
        progress=progress,
    )
