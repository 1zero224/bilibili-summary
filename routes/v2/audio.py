"""
纯音频获取（ASR 专用）。

两条路径：
1) 插件在页面上下文拿到 DASH 音频直链 → 后端只负责下载（不需要服务端登录态）
2) 直链缺失/失效 → 后端用自身 credential 走 playurl 解析（需要已登录）

复用 ``routes.bilibili_downloader`` 的 UA/Referer 与 playurl 解析，不新写反爬逻辑。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import aiohttp

from routes.bilibili_downloader import (
    REFERER,
    USER_AGENT,
    _get_playurl,
    _parse_dash_streams,
)

DEFAULT_TIMEOUT = 600
MIN_VALID_BYTES = 2048


def _headers(extra: dict | None = None) -> dict:
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": REFERER,
        "Accept": "*/*",
    }
    if extra:
        headers.update({k: v for k, v in extra.items() if v})
    return headers


async def download_from_url(url: str, out_path: Path, referer: str = REFERER) -> Path:
    """流式下载到 ``out_path``（原子替换），失败抛 RuntimeError。"""
    if not url:
        raise RuntimeError("音频直链为空")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    staging = out_path.with_suffix(out_path.suffix + ".part")
    timeout = aiohttp.ClientTimeout(total=None, sock_read=120)
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=_headers({"Referer": referer})) as session:
            async with session.get(url) as resp:
                if resp.status >= 400:
                    raise RuntimeError(f"音频下载失败: HTTP {resp.status}")
                with staging.open("wb") as fh:
                    async for chunk in resp.content.iter_chunked(256 * 1024):
                        if chunk:
                            fh.write(chunk)
    except Exception as exc:  # noqa: BLE001
        if staging.exists():
            staging.unlink(missing_ok=True)
        raise RuntimeError(f"音频下载失败: {exc}") from exc

    size = staging.stat().st_size if staging.exists() else 0
    if size < MIN_VALID_BYTES:
        staging.unlink(missing_ok=True)
        raise RuntimeError(f"音频文件过小({size}B)，可能被风控拦截")
    staging.replace(out_path)
    return out_path


def pick_audio_url(playurl: dict) -> str:
    """从 playurl 结果里挑码率最高的音频流。"""
    try:
        _, audios = _parse_dash_streams(playurl)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"解析 playurl 失败: {exc}") from exc

    usable = [s for s in audios if s.urls]
    if usable:
        usable.sort(key=lambda s: s.bandwidth, reverse=True)
        return usable[0].urls[0]

    durl = playurl.get("durl") or []
    if isinstance(durl, list) and durl:
        first = durl[0] or {}
        candidate = first.get("base_url") or first.get("baseUrl") or first.get("url") or ""
        if candidate:
            return str(candidate)
    raise RuntimeError("playurl 未返回可用音频流")


async def resolve_audio_url(bvid: str, page_index: int = 0) -> str:
    playurl = await _get_playurl(bvid, page_index)
    return pick_audio_url(playurl)


async def download_audio_for_video(bvid: str, page_index: int, out_path: Path) -> Path:
    """服务端自行解析并下载音频（需要 credential）。"""
    started = time.time()
    last_error: Exception | None = None
    for attempt in range(1, 3):
        try:
            url = await resolve_audio_url(bvid, page_index)
            path = await download_from_url(url, out_path)
            if path.exists():
                return path
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            await asyncio.sleep(0.8 * attempt)
    raise RuntimeError(f"服务端下载音频失败（{time.time() - started:.0f}s）: {last_error}")


async def prepare_audio(
    *,
    bvid: str,
    page_index: int,
    audio_url: str,
    out_path: Path,
) -> tuple[Path, str]:
    """
    准备音频文件，返回 ``(path, via)``。
    ``via`` ∈ ``plugin-url`` / ``server-playurl``，用于进度提示与排障。
    """
    if audio_url:
        try:
            return await download_from_url(audio_url, out_path), "plugin-url"
        except RuntimeError:
            # 直链过期或被风控 → 回退服务端解析
            pass
    return await download_audio_for_video(bvid, page_index, out_path), "server-playurl"


async def download_full_media(bvid: str, page_index: int, out_path: Path, duration: int = 0) -> Path:
    """
    最后一档兜底：走既有「整段视频下载」管线（实测在境外服务器可用），
    拿到的 mp4 直接喂给 ``transcribe_media``（bailian/mimo 内部会 ffmpeg 抽音，
    local 模式由 av 直接读音轨）。
    """
    from routes.whisper import download_bilibili_media  # 延迟导入

    target = bvid if page_index <= 0 else f"https://www.bilibili.com/video/{bvid}?p={page_index + 1}"
    return await download_bilibili_media(target, out_path, expected_duration=duration)
