#!/usr/bin/env python3
"""
FastAPI 后端服务器
提供 REST API + SSE 实时进度推送
"""

import os
import asyncio
import json
import time
import tempfile
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel

from dotenv import load_dotenv
import anthropic

from bilibili_api import video, user as bili_user, search, favorite_list
from bilibili_api.video import VideoDownloadURLDataDetecter, AudioQuality
from bilibili_api.utils.network import Credential
from bilibili_api.login_v2 import QrCodeLogin, QrCodeLoginEvents

from summarize import (
    extract_bvid, get_subtitle, save_ass, save_summary,
    summarize_with_claude, get_uid_by_name, get_user_videos,
    get_favorite_videos, sanitize_filename
)
from dotenv import set_key
import base64
import aiohttp

# ---------------------------------------------------------------------------
# Path resolution (supports PyInstaller bundle)
# ---------------------------------------------------------------------------
BUNDLE_DIR = Path(os.environ.get('BILISUMMARY_BUNDLE_DIR', os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = Path(os.environ.get('BILISUMMARY_DATA_DIR', os.path.dirname(os.path.abspath(__file__))))

load_dotenv(str(DATA_DIR / '.env.local'))


credential: Optional[Credential] = None
ai_client: Optional[anthropic.AsyncAnthropic] = None


def init_credential():
    global credential
    sessdata = os.getenv('BILIBILI_SESSION_TOKEN')
    bili_jct = os.getenv('BILIBILI_BILI_JCT')
    ac_time_value = os.getenv('BILIBILI_AC_TIME_VALUE')
    if sessdata and bili_jct:
        credential = Credential(sessdata=sessdata, bili_jct=bili_jct, ac_time_value=ac_time_value or "")
        return True
    return False


def init_ai_client():
    global ai_client
    ai_client = anthropic.AsyncAnthropic(
        base_url=os.getenv('ANTHROPIC_BASE_URL'),
        api_key=os.getenv('ANTHROPIC_AUTH_TOKEN')
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_credential()
    init_ai_client()
    yield


# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------
app = FastAPI(title="Bilibili 视频总结器", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BUNDLE_DIR / "static")), name="static")

DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "GLM-4-FlashX-250414")


# ---------------------------------------------------------------------------
# Request Models
# ---------------------------------------------------------------------------
class SummarizeURLRequest(BaseModel):
    urls: list[str]
    model: str = DEFAULT_MODEL
    concurrency: int = 12


class SummarizeUserRequest(BaseModel):
    user: str  # UID or name
    count: int = 50
    model: str = DEFAULT_MODEL
    concurrency: int = 12


class SummarizeFavRequest(BaseModel):
    count: int = 20
    model: str = DEFAULT_MODEL
    concurrency: int = 12


class SummarizeBvidsRequest(BaseModel):
    bvids: list[str]
    output_subdir: str = "favorites"
    model: str = DEFAULT_MODEL
    concurrency: int = 6


# ---------------------------------------------------------------------------
# SSE Progress (event-history based, supports reconnection)
# ---------------------------------------------------------------------------
# Each task has: {"events": [...], "notify": asyncio.Event, "done": bool}
progress_tasks: dict[str, dict] = {}


def _ensure_task(task_id: str):
    if task_id not in progress_tasks:
        progress_tasks[task_id] = {
            "events": [],
            "notify": asyncio.Event(),
            "done": False,
        }


async def send_progress(task_id: str, event: str, data: dict):
    _ensure_task(task_id)
    task = progress_tasks[task_id]
    task["events"].append({"event": event, "data": data})
    if event == "done":
        task["done"] = True
        # Schedule cleanup after 5 minutes
        asyncio.get_event_loop().call_later(300, lambda: progress_tasks.pop(task_id, None))
    task["notify"].set()


async def progress_generator(task_id: str, last_id: int = -1):
    _ensure_task(task_id)
    cursor = last_id + 1  # Start from where the client left off

    while True:
        task = progress_tasks.get(task_id)
        if not task:
            break

        # Yield any events we haven't sent yet
        while cursor < len(task["events"]):
            msg = task["events"][cursor]
            yield f"id: {cursor}\nevent: {msg['event']}\ndata: {json.dumps(msg['data'], ensure_ascii=False)}\n\n"
            if msg["event"] == "done":
                return
            cursor += 1

        # If done flag is set and we've sent all events, exit
        if task["done"]:
            break

        # Wait for new events or send heartbeat
        task["notify"].clear()
        try:
            await asyncio.wait_for(task["notify"].wait(), timeout=15)
        except asyncio.TimeoutError:
            yield ": heartbeat\n\n"


# ---------------------------------------------------------------------------
# No-subtitle retry logic
# ---------------------------------------------------------------------------
MAX_NOSUB_RETRIES = 3  # Max times to retry a no_subtitle video


def _retries_file(output_subdir: str) -> Path:
    return DATA_DIR / "summary" / output_subdir / "no_subtitle" / ".retries.json"


def get_retry_count(output_subdir: str, safe_title: str) -> int:
    path = _retries_file(output_subdir)
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text())
        return data.get(safe_title, 0)
    except Exception:
        return 0


def increment_retry_count(output_subdir: str, safe_title: str):
    path = _retries_file(output_subdir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except Exception:
            data = {}
    data[safe_title] = data.get(safe_title, 0) + 1
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def clear_retry_count(output_subdir: str, safe_title: str):
    path = _retries_file(output_subdir)
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text())
        data.pop(safe_title, None)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Core processing (with progress callbacks)
# ---------------------------------------------------------------------------
async def process_single_video(url: str, model: str, output_subdir: str, task_id: str):
    """Process one video and send progress events."""
    bvid = extract_bvid(url)
    if not bvid:
        await send_progress(task_id, "error", {"message": f"无法提取 BV 号: {url}"})
        return None

    try:
        v = video.Video(bvid=bvid, credential=credential)
        info = await v.get_info()
        title = info.get("title", bvid)
        duration = info.get("duration", 0)
        owner = info.get("owner", {})
        author_name = owner.get("name", "")
        author_uid = owner.get("mid", 0)
        url = f"https://www.bilibili.com/video/{bvid}"

        # Check existing (both normal and no_subtitle dirs)
        safe_title = sanitize_filename(title)
        normal_path = DATA_DIR / "summary" / output_subdir / f"{safe_title}.md"
        nosub_path = DATA_DIR / "summary" / output_subdir / "no_subtitle" / f"{safe_title}.md"

        if normal_path.exists():
            await send_progress(task_id, "skip", {
                "title": title, "bvid": bvid,
                "path": f"{output_subdir}/{safe_title}.md"
            })
            return {"title": title, "status": "skipped"}

        # For no_subtitle files: retry if under the limit
        if nosub_path.exists():
            retries = get_retry_count(output_subdir, safe_title)
            if retries >= MAX_NOSUB_RETRIES:
                await send_progress(task_id, "skip", {
                    "title": title, "bvid": bvid,
                    "path": f"{output_subdir}/no_subtitle/{safe_title}.md"
                })
                return {"title": title, "status": "skipped"}
            else:
                await send_progress(task_id, "processing", {
                    "title": title, "bvid": bvid,
                    "step": f"重试获取字幕 ({retries+1}/{MAX_NOSUB_RETRIES})"
                })

        await send_progress(task_id, "processing", {"title": title, "bvid": bvid, "step": "获取字幕"})

        subtitle_text, subtitle_raw = await get_subtitle(v)

        if subtitle_raw:
            save_ass(title, subtitle_raw, output_subdir)

        await send_progress(task_id, "processing", {"title": title, "bvid": bvid, "step": "AI 生成总结"})

        summary, duration_sec = await summarize_with_claude(subtitle_text, title, ai_client, model=model)

        final_subdir = output_subdir
        if not subtitle_text:
            final_subdir = f"{output_subdir}/no_subtitle"
            # Increment retry counter for no_subtitle
            increment_retry_count(output_subdir, safe_title)
        else:
            # If this was a retry and now we have subtitles, clean up old no_subtitle file
            if nosub_path.exists():
                nosub_path.unlink()
                clear_retry_count(output_subdir, safe_title)

        save_summary(title, bvid, url, duration, summary, final_subdir, author_name=author_name, author_uid=author_uid)

        status = "no_subtitle" if not subtitle_text else "success"
        await send_progress(task_id, "completed", {
            "title": title, "bvid": bvid,
            "duration_sec": round(duration_sec, 2),
            "status": status,
            "path": f"{final_subdir}/{safe_title}.md"
        })
        return {"title": title, "status": status, "duration_sec": round(duration_sec, 2)}

    except Exception as e:
        await send_progress(task_id, "error", {"title": bvid, "message": str(e)})
        return {"title": bvid, "status": "error", "message": str(e)}


async def run_batch(bvids: list[str], model: str, concurrency: int, output_subdir: str, task_id: str):
    sem = asyncio.Semaphore(concurrency)
    results = []

    await send_progress(task_id, "start", {
        "total": len(bvids), "concurrency": concurrency, "model": model
    })

    async def bounded(bvid):
        async with sem:
            url = f"https://www.bilibili.com/video/{bvid}"
            try:
                r = await process_single_video(url, model, output_subdir, task_id)
                results.append(r)
            except Exception as e:
                await send_progress(task_id, "error", {"title": bvid, "message": str(e)})
                results.append({"title": bvid, "status": "error", "message": str(e)})

    try:
        await asyncio.gather(*[bounded(bv) for bv in bvids])
    except Exception as e:
        await send_progress(task_id, "error", {"title": "", "message": f"批处理异常: {e}"})

    success = sum(1 for r in results if r and r.get("status") == "success")
    skipped = sum(1 for r in results if r and r.get("status") == "skipped")
    no_sub = sum(1 for r in results if r and r.get("status") == "no_subtitle")
    errors = sum(1 for r in results if r and r.get("status") == "error")

    await send_progress(task_id, "done", {
        "total": len(bvids), "success": success, "skipped": skipped,
        "no_subtitle": no_sub, "errors": errors
    })
    return results


def save_user_meta(uid: int, name: str):
    """Save .meta.json in user summary directory for display name resolution."""
    user_dir = DATA_DIR / "summary" / "users" / str(uid)
    user_dir.mkdir(parents=True, exist_ok=True)
    meta_file = user_dir / ".meta.json"
    meta_file.write_text(json.dumps({"uid": uid, "name": name}, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index():
    return (BUNDLE_DIR / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/api/status")
async def get_status():
    return {"logged_in": credential is not None, "ai_configured": ai_client is not None}


@app.get("/api/summaries")
async def list_summaries():
    """List all generated summaries, structured by category."""
    summary_root = DATA_DIR / "summary"
    if not summary_root.exists():
        return {"categories": []}

    categories = []

    # 1) Standalone
    standalone_dir = summary_root / "standalone"
    if standalone_dir.exists():
        items = []
        for md in sorted(standalone_dir.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
            rel = md.relative_to(summary_root)
            items.append({"name": md.stem, "path": str(rel), "no_subtitle": "no_subtitle" in str(rel)})
        if items:
            categories.append({"type": "standalone", "label": "独立视频", "icon": "link", "count": len(items), "items": items})

    # 2) Favorites
    fav_dir = summary_root / "favorites"
    if fav_dir.exists():
        items = []
        for md in sorted(fav_dir.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
            rel = md.relative_to(summary_root)
            items.append({"name": md.stem, "path": str(rel), "no_subtitle": "no_subtitle" in str(rel)})
        if items:
            categories.append({"type": "favorites", "label": "收藏夹", "icon": "star", "count": len(items), "items": items})

    # 3) Users — each UID is a sub-group with display name
    users_dir = summary_root / "users"
    if users_dir.exists():
        user_groups = []
        for uid_folder in sorted(users_dir.iterdir()):
            if not uid_folder.is_dir():
                continue
            uid = uid_folder.name
            # Read display name from .meta.json
            meta_file = uid_folder / ".meta.json"
            display_name = uid
            if meta_file.exists():
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                    display_name = meta.get("name", uid)
                except Exception:
                    pass

            items = []
            for md in sorted(uid_folder.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
                rel = md.relative_to(summary_root)
                items.append({"name": md.stem, "path": str(rel), "no_subtitle": "no_subtitle" in str(rel)})
            if items:
                user_groups.append({"uid": uid, "display_name": display_name, "count": len(items), "items": items})

        if user_groups:
            total = sum(g["count"] for g in user_groups)
            categories.append({"type": "users", "label": "UP 主", "icon": "users", "count": total, "groups": user_groups})

    return {"categories": categories}


@app.get("/api/summary/{path:path}")
async def read_summary(path: str):
    filepath = DATA_DIR / "summary" / path
    if not filepath.exists():
        return JSONResponse(status_code=404, content={"error": "Not found"})
    return {"content": filepath.read_text(encoding="utf-8"), "path": path}


@app.post("/api/summarize/url")
async def summarize_urls(req: SummarizeURLRequest):
    task_id = f"url-{int(time.time()*1000)}"
    bvids = [extract_bvid(u) for u in req.urls]
    bvids = [b for b in bvids if b]
    if not bvids:
        return JSONResponse(status_code=400, content={"error": "无法解析任何 BV 号"})

    asyncio.create_task(run_batch(bvids, req.model, req.concurrency, "standalone", task_id))
    return {"task_id": task_id, "total": len(bvids)}


@app.post("/api/summarize/user")
async def summarize_user(req: SummarizeUserRequest):
    task_id = f"user-{int(time.time()*1000)}"

    async def _run():
        username = None
        if req.user.isdigit():
            uid = int(req.user)
        else:
            username = req.user
            uid = await get_uid_by_name(req.user)
            if not uid:
                await send_progress(task_id, "error", {"message": f"未找到 UP 主: {req.user}"})
                await send_progress(task_id, "done", {"total": 0, "success": 0, "skipped": 0, "no_subtitle": 0, "errors": 1})
                return

        # Fetch user info and save metadata
        try:
            u = bili_user.User(uid=uid, credential=credential)
            user_info = await u.get_user_info()
            resolved_name = user_info.get('name', username or str(uid))
        except Exception:
            resolved_name = username or str(uid)

        save_user_meta(uid, resolved_name)

        await send_progress(task_id, "info", {"message": f"获取 UP 主 {resolved_name} (UID:{uid}) 的最新 {req.count} 个视频..."})
        bvids = await get_user_videos(uid, req.count, credential)

        if not bvids:
            await send_progress(task_id, "error", {"message": "未找到视频"})
            await send_progress(task_id, "done", {"total": 0, "success": 0, "skipped": 0, "no_subtitle": 0, "errors": 0})
            return

        await run_batch(bvids, req.model, req.concurrency, f"users/{uid}", task_id)

    asyncio.create_task(_run())
    return {"task_id": task_id}


@app.post("/api/summarize/favorites")
async def summarize_favorites(req: SummarizeFavRequest):
    if not credential:
        return JSONResponse(status_code=401, content={"error": "未登录 Bilibili"})

    task_id = f"fav-{int(time.time()*1000)}"

    async def _run():
        await send_progress(task_id, "info", {"message": f"获取默认收藏夹的最新 {req.count} 个视频..."})
        bvids = await get_favorite_videos(req.count, credential)

        if not bvids:
            await send_progress(task_id, "error", {"message": "未找到视频"})
            await send_progress(task_id, "done", {"total": 0, "success": 0, "skipped": 0, "no_subtitle": 0, "errors": 0})
            return

        await run_batch(bvids, req.model, req.concurrency, "favorites", task_id)

    asyncio.create_task(_run())
    return {"task_id": task_id}


# ---------------------------------------------------------------------------
# Favorites Browser APIs
# ---------------------------------------------------------------------------
@app.get("/api/favorites/list")
async def list_favorites():
    """Return all favorite folders for the logged-in user."""
    if not credential:
        return JSONResponse(status_code=401, content={"error": "未登录 Bilibili"})

    try:
        me = await bili_user.get_self_info(credential)
        my_uid = me['mid']
        fav_data = await favorite_list.get_video_favorite_list(uid=my_uid, credential=credential)

        folders = []
        for f in fav_data.get('list', []):
            folders.append({
                "id": f['id'],
                "title": f['title'],
                "count": f.get('media_count', 0),
                "is_default": f.get('attr', 1) == 0,
            })
        return {"folders": folders}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/favorites/{fav_id}/videos")
async def list_favorite_videos(fav_id: int, page: int = 1):
    """Return videos in a favorite folder with cover images and summary status."""
    if not credential:
        return JSONResponse(status_code=401, content={"error": "未登录 Bilibili"})

    try:
        content = await favorite_list.get_video_favorite_list_content(
            media_id=fav_id, page=page, credential=credential
        )

        videos = []
        for m in content.get('medias', []) or []:
            bvid = m.get('bvid', '')
            title = m.get('title', '')
            safe_title = sanitize_filename(title)

            # Check if summary exists
            normal_path = DATA_DIR / "summary" / "favorites" / f"{safe_title}.md"
            nosub_path = DATA_DIR / "summary" / "favorites" / "no_subtitle" / f"{safe_title}.md"
            has_summary = normal_path.exists()
            has_nosub = nosub_path.exists()
            summary_path = None
            if has_summary:
                summary_path = f"favorites/{safe_title}.md"
            elif has_nosub:
                summary_path = f"favorites/no_subtitle/{safe_title}.md"

            videos.append({
                "bvid": bvid,
                "title": title,
                "cover": m.get('cover', ''),
                "duration": m.get('duration', 0),
                "upper": m.get('upper', {}).get('name', ''),
                "upper_mid": m.get('upper', {}).get('mid', 0),
                "play_count": m.get('cnt_info', {}).get('play', 0),
                "has_summary": has_summary or has_nosub,
                "summary_status": 'done' if has_summary else ('no_subtitle' if has_nosub else 'none'),
                "summary_path": summary_path,
            })

        has_more = content.get('has_more', False)
        return {"videos": videos, "has_more": has_more, "page": page}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/favorites/summarize")
async def summarize_favorite_bvids(req: SummarizeBvidsRequest):
    """Summarize specific BVIDs from favorites (auto-trigger from browse)."""
    if not credential:
        return JSONResponse(status_code=401, content={"error": "未登录 Bilibili"})
    if not req.bvids:
        return {"task_id": None, "message": "无需总结"}

    task_id = f"fav-auto-{int(time.time()*1000)}"

    async def _run():
        await run_batch(req.bvids, req.model, req.concurrency, req.output_subdir, task_id)

    asyncio.create_task(_run())
    return {"task_id": task_id}


@app.delete("/api/favorites/{fav_id}/video/{bvid}")
async def unfavorite_video(fav_id: int, bvid: str):
    """Remove a video from a favorite folder."""
    if not credential:
        return JSONResponse(status_code=401, content={"error": "未登录 Bilibili"})

    try:
        v = video.Video(bvid=bvid, credential=credential)
        info = await v.get_info()
        aid = info.get("aid")
        if not aid:
            return JSONResponse(status_code=400, content={"error": "无法获取视频 AID"})

        await favorite_list.delete_video_favorite_list_content(
            media_id=fav_id, aids=[aid], credential=credential
        )
        return {"success": True, "bvid": bvid}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/retry/{bvid}")
async def retry_summarize(bvid: str, output_subdir: str = "favorites"):
    """Force re-summarize a single video by deleting existing no_subtitle file."""
    if not credential:
        return JSONResponse(status_code=401, content={"error": "未登录 Bilibili"})

    try:
        # Get video info to find the file
        v = video.Video(bvid=bvid, credential=credential)
        info = await v.get_info()
        title = info.get("title", bvid)
        safe_title = sanitize_filename(title)

        # Delete existing no_subtitle file if present
        nosub_path = DATA_DIR / "summary" / output_subdir / "no_subtitle" / f"{safe_title}.md"
        if nosub_path.exists():
            nosub_path.unlink()

        # Reset retry count
        clear_retry_count(output_subdir, safe_title)

        # Run summarization as a task
        task_id = f"retry-{bvid}-{int(time.time()*1000)}"

        async def _run():
            await process_single_video(bvid, DEFAULT_MODEL, output_subdir, task_id)
            await send_progress(task_id, "done", {"total": 1})

        asyncio.create_task(_run())
        return {"task_id": task_id, "title": title}

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ---------------------------------------------------------------------------
# ASR-based Summarization (for videos without subtitles)
# ---------------------------------------------------------------------------
@app.post("/api/asr-summarize/{bvid}")
async def asr_summarize(bvid: str, output_subdir: str = "favorites"):
    """Download audio → GLM-ASR transcription → LLM summary via SSE."""
    if not credential:
        return JSONResponse(status_code=401, content={"error": "未登录 Bilibili"})
    if not ai_client:
        return JSONResponse(status_code=400, content={"error": "未配置 AI API"})

    async def event_stream():
        try:
            # Step 1: Get video info
            yield f"data: {json.dumps({'step': 'info', 'message': '获取视频信息...'})}\n\n"
            v = video.Video(bvid=bvid, credential=credential)
            info = await v.get_info()
            title = info.get("title", bvid)
            duration = info.get("duration", 0)
            owner = info.get("owner", {})
            author_name = owner.get("name", "")
            author_uid = owner.get("mid", 0)
            safe_title = sanitize_filename(title)
            url = f"https://www.bilibili.com/video/{bvid}"

            # Step 2: Get audio download URL (use lowest quality to minimize size)
            yield f"data: {json.dumps({'step': 'audio_url', 'message': '获取音频流地址...'})}\n\n"
            download_data = await v.get_download_url(page_index=0)
            detector = VideoDownloadURLDataDetecter(download_data)
            streams = detector.detect_best_streams(
                audio_max_quality=AudioQuality._64K,
                no_dolby_audio=True,
                no_hires=True,
            )

            audio_stream = None
            for s in streams:
                if hasattr(s, 'audio_quality'):
                    audio_stream = s
                    break

            if not audio_stream:
                yield f"data: {json.dumps({'step': 'error', 'message': '无法获取音频流'})}\n\n"
                return

            # Step 3: Download audio
            yield f"data: {json.dumps({'step': 'download', 'message': '下载音频中...'})}\n\n"
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Referer": "https://www.bilibili.com",
            }
            audio_data = b""
            async with aiohttp.ClientSession() as session:
                async with session.get(audio_stream.url, headers=headers) as resp:
                    if resp.status != 200:
                        yield f"data: {json.dumps({'step': 'error', 'message': f'音频下载失败: HTTP {resp.status}'})}\n\n"
                        return
                    audio_data = await resp.read()

            audio_size_mb = len(audio_data) / (1024 * 1024)
            yield f"data: {json.dumps({'step': 'downloaded', 'message': f'音频下载完成 ({audio_size_mb:.1f} MB)'})}\n\n"

            # Step 4: Send to GLM-ASR for transcription
            yield f"data: {json.dumps({'step': 'asr', 'message': '语音识别中 (GLM-ASR)...'})}\n\n"

            asr_endpoint = "https://open.bigmodel.cn/api/paas/v4/audio/transcriptions"
            api_key = os.getenv('ANTHROPIC_AUTH_TOKEN', '')

            # Write audio to temp file for conversion
            m4s_path = tempfile.mktemp(suffix=".m4s")
            with open(m4s_path, 'wb') as f:
                f.write(audio_data)

            # Convert m4s to 25-second mp3 segments using PyAV (ASR limit: 30s per file)
            SEGMENT_DURATION = 25  # seconds, stay under 30s limit
            yield f"data: {json.dumps({'step': 'asr', 'message': '切分音频并转换格式...'})}\n\n"

            chunk_paths = []
            try:
                import av as pyav
                input_container = pyav.open(m4s_path)
                audio_stream_info = input_container.streams.audio[0]
                total_duration = float(audio_stream_info.duration * audio_stream_info.time_base) if audio_stream_info.duration else duration
                num_segments = max(1, int(total_duration / SEGMENT_DURATION) + (1 if total_duration % SEGMENT_DURATION > 0.5 else 0))

                print(f"[ASR] Audio duration: {total_duration:.1f}s, splitting into {num_segments} segments of {SEGMENT_DURATION}s")

                # Decode all audio frames first
                all_frames = []
                for frame in input_container.decode(audio=0):
                    all_frames.append(frame)
                input_container.close()

                if not all_frames:
                    yield f"data: {json.dumps({'step': 'error', 'message': '音频解码失败: 无帧数据'})}\n\n"
                    return

                sample_rate = all_frames[0].sample_rate
                samples_per_segment = SEGMENT_DURATION * sample_rate

                # Calculate total samples
                total_samples = sum(f.samples for f in all_frames)
                num_segments = max(1, (total_samples + samples_per_segment - 1) // samples_per_segment)

                # Split frames into segments by sample count
                current_segment = 0
                current_samples = 0
                segment_frames = []

                def write_segment(frames, seg_idx):
                    seg_path = tempfile.mktemp(suffix=f"_seg{seg_idx}.mp3")
                    out = pyav.open(seg_path, 'w', format='mp3')
                    out_stream = out.add_stream('mp3', rate=16000)
                    out_stream.bit_rate = 64000
                    out_stream.layout = 'mono'
                    resampler = pyav.AudioResampler(format='s16', layout='mono', rate=16000)
                    for fr in frames:
                        fr.pts = None
                        for resampled in resampler.resample(fr):
                            for pkt in out_stream.encode(resampled):
                                out.mux(pkt)
                    for pkt in out_stream.encode():
                        out.mux(pkt)
                    out.close()
                    return seg_path

                for frame in all_frames:
                    segment_frames.append(frame)
                    current_samples += frame.samples
                    if current_samples >= samples_per_segment:
                        seg_path = write_segment(segment_frames, current_segment)
                        chunk_paths.append(seg_path)
                        current_segment += 1
                        current_samples = 0
                        segment_frames = []

                # Write remaining frames
                if segment_frames:
                    seg_path = write_segment(segment_frames, current_segment)
                    chunk_paths.append(seg_path)

            except Exception as conv_err:
                yield f"data: {json.dumps({'step': 'error', 'message': f'音频转换失败: {conv_err}'})}\n\n"
                return
            finally:
                if os.path.exists(m4s_path):
                    os.unlink(m4s_path)

            num_chunks = len(chunk_paths)
            yield f"data: {json.dumps({'step': 'asr', 'message': f'共 {num_chunks} 段，开始语音识别...'})}\n\n"

            # Transcribe each segment
            transcripts = []
            try:
                for i, chunk_path in enumerate(chunk_paths):
                    chunk_size_kb = os.path.getsize(chunk_path) / 1024
                    yield f"data: {json.dumps({'step': 'asr', 'message': f'转录中 ({i+1}/{num_chunks}) [{chunk_size_kb:.0f}KB]...'})}\n\n"

                    form = aiohttp.FormData()
                    form.add_field('model', 'glm-asr-2512')
                    form.add_field('stream', 'false')
                    form.add_field('file', open(chunk_path, 'rb'), filename=f'seg{i}.mp3', content_type='audio/mpeg')

                    async with aiohttp.ClientSession() as session:
                        async with session.post(
                            asr_endpoint,
                            data=form,
                            headers={"Authorization": f"Bearer {api_key}"},
                            timeout=aiohttp.ClientTimeout(total=120),
                        ) as resp:
                            resp_text = await resp.text()
                            print(f"[ASR seg {i}] status={resp.status}, response={resp_text[:300]}")
                            if resp.status != 200:
                                yield f"data: {json.dumps({'step': 'error', 'message': f'ASR 第{i+1}段失败: {resp_text[:200]}'})}\n\n"
                                return
                            chunk_result = json.loads(resp_text)
                            text = chunk_result.get('text', '')
                            if text:
                                transcripts.append(text)
            finally:
                for p in chunk_paths:
                    if os.path.exists(p):
                        os.unlink(p)

            transcript = ' '.join(transcripts)
            if not transcript:
                yield f"data: {json.dumps({'step': 'error', 'message': 'ASR 返回空文本'})}\n\n"
                return

            transcript_len = len(transcript)
            yield f"data: {json.dumps({'step': 'transcribed', 'message': f'转录完成 ({transcript_len} 字)'})}\n\n"

            # Step 5: LLM Summarization
            yield f"data: {json.dumps({'step': 'summarize', 'message': '生成总结中...'})}\n\n"
            summary_text, llm_time = await summarize_with_claude(
                subtitle=transcript, title=title, client=ai_client, model=DEFAULT_MODEL
            )

            # Step 6: Save result
            # Delete old no_subtitle file if exists
            nosub_path = DATA_DIR / "summary" / output_subdir / "no_subtitle" / f"{safe_title}.md"
            if nosub_path.exists():
                nosub_path.unlink()

            save_summary(
                title=title, bvid=bvid, url=url, duration=duration,
                summary=summary_text, output_subdir=output_subdir,
                author_name=author_name, author_uid=author_uid,
            )

            new_path = f"{output_subdir}/{safe_title}.md"
            yield f"data: {json.dumps({'step': 'done', 'message': '总结完成!', 'path': new_path, 'llm_time': round(llm_time, 1)})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'step': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Settings & Model Selection
# ---------------------------------------------------------------------------
@app.get("/api/settings")
async def get_settings():
    """Return current API settings (token partially masked)."""
    token = os.getenv('ANTHROPIC_AUTH_TOKEN', '')
    masked = token[:8] + '***' + token[-4:] if len(token) > 12 else '***'
    return {
        "base_url": os.getenv('ANTHROPIC_BASE_URL', ''),
        "auth_token_masked": masked,
        "default_model": DEFAULT_MODEL,
    }


class SaveSettingsRequest(BaseModel):
    base_url: str = ""
    auth_token: str = ""  # empty = don't change
    default_model: str = ""


@app.post("/api/settings")
async def save_settings(req: SaveSettingsRequest):
    """Save API settings to .env.local and hot-reload ai_client."""
    global DEFAULT_MODEL
    env_path = str(DATA_DIR / '.env.local')
    changed = []

    if req.base_url:
        set_key(env_path, 'ANTHROPIC_BASE_URL', req.base_url)
        os.environ['ANTHROPIC_BASE_URL'] = req.base_url
        changed.append('base_url')

    if req.auth_token and '***' not in req.auth_token:
        set_key(env_path, 'ANTHROPIC_AUTH_TOKEN', req.auth_token)
        os.environ['ANTHROPIC_AUTH_TOKEN'] = req.auth_token
        changed.append('auth_token')

    if req.default_model:
        set_key(env_path, 'DEFAULT_MODEL', req.default_model)
        os.environ['DEFAULT_MODEL'] = req.default_model
        DEFAULT_MODEL = req.default_model
        changed.append('default_model')

    # Hot-reload AI client
    init_ai_client()

    return {"success": True, "changed": changed}


@app.get("/api/models")
async def list_models():
    """Fetch available models from the API provider's /v1/models endpoint."""
    base_url = os.getenv('ANTHROPIC_BASE_URL', '')
    token = os.getenv('ANTHROPIC_AUTH_TOKEN', '')

    if not base_url or not token:
        return JSONResponse(status_code=400, content={"error": "API 未配置"})

    # Build models URL: strip trailing /v1 or /v1/ if present, then add /v1/models
    models_url = base_url.rstrip('/')
    if models_url.endswith('/v1'):
        models_url = models_url[:-3]
    models_url = models_url.rstrip('/') + '/v1/models'

    try:
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bearer {token}"}
            async with session.get(models_url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    return JSONResponse(status_code=resp.status, content={"error": f"API 返回 {resp.status}: {text[:200]}"})
                data = await resp.json()
                models = []
                for m in data.get('data', []):
                    models.append({
                        "id": m.get('id', ''),
                        "owned_by": m.get('owned_by', ''),
                    })
                # Sort: text models first
                models.sort(key=lambda x: x['id'])
                return {"models": models, "current": DEFAULT_MODEL}
    except asyncio.TimeoutError:
        return JSONResponse(status_code=504, content={"error": "请求超时"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/progress/{task_id}")
async def progress_stream(task_id: str, request: Request):
    last_id = int(request.headers.get("Last-Event-ID", "-1"))
    return StreamingResponse(
        progress_generator(task_id, last_id=last_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


# ---------------------------------------------------------------------------
# QR Login / Logout
# ---------------------------------------------------------------------------
@app.get("/api/login/qr")
async def qr_login_stream():
    """SSE stream: generates QR code, polls login state, saves credential."""
    async def _gen():
        global credential
        login = QrCodeLogin()
        await login.generate_qrcode()

        # Get QR code as base64 PNG
        pic = login.get_qrcode_picture()
        img_bytes = pic.content
        b64 = base64.b64encode(img_bytes).decode('utf-8')
        yield f"event: qrcode\ndata: {json.dumps({'image': b64})}\n\n"

        # Poll login state
        while True:
            try:
                state = await login.check_state()
            except Exception as e:
                yield f"event: error\ndata: {json.dumps({'message': str(e)})}\n\n"
                break

            if state == QrCodeLoginEvents.DONE:
                cred = login.get_credential()
                # Save to .env.local
                env_path = str(DATA_DIR / '.env.local')
                set_key(env_path, 'BILIBILI_SESSION_TOKEN', cred.sessdata)
                set_key(env_path, 'BILIBILI_BILI_JCT', cred.bili_jct)
                if cred.ac_time_value:
                    set_key(env_path, 'BILIBILI_AC_TIME_VALUE', cred.ac_time_value)
                # Update global credential
                credential = Credential(
                    sessdata=cred.sessdata,
                    bili_jct=cred.bili_jct,
                    ac_time_value=cred.ac_time_value or ""
                )
                yield f"event: done\ndata: {json.dumps({'message': '登录成功'})}\n\n"
                break
            elif state == QrCodeLoginEvents.TIMEOUT:
                yield f"event: timeout\ndata: {json.dumps({'message': '二维码已过期'})}\n\n"
                break
            elif state == QrCodeLoginEvents.CONF:
                yield f"event: scanned\ndata: {json.dumps({'message': '已扫码，请在手机上确认'})}\n\n"

            await asyncio.sleep(2)

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@app.post("/api/logout")
async def logout():
    """Clear credential and remove from .env.local."""
    global credential
    credential = None
    env_path = DATA_DIR / '.env.local'
    if env_path.exists():
        set_key(str(env_path), 'BILIBILI_SESSION_TOKEN', '')
        set_key(str(env_path), 'BILIBILI_BILI_JCT', '')
        set_key(str(env_path), 'BILIBILI_AC_TIME_VALUE', '')
    return {"message": "已注销"}


# ---------------------------------------------------------------------------
# Run standalone
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=18520)
