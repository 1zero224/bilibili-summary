#!/usr/bin/env python3
"""
FastAPI 后端服务器
提供 REST API + SSE 实时进度推送
"""

import os
import asyncio
import json
import time
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel

from dotenv import load_dotenv
import anthropic

from bilibili_api import video, user as bili_user, search
from bilibili_api.utils.network import Credential
from bilibili_api.login_v2 import QrCodeLogin, QrCodeLoginEvents

from summarize import (
    extract_bvid, get_subtitle, save_ass, save_summary,
    summarize_with_claude, get_uid_by_name, get_user_videos,
    get_favorite_videos, sanitize_filename
)
from dotenv import set_key
import base64

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
load_dotenv('.env.local')

progress_queues: dict[str, asyncio.Queue] = {}
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
app.mount("/static", StaticFiles(directory="static"), name="static")


# ---------------------------------------------------------------------------
# Request Models
# ---------------------------------------------------------------------------
class SummarizeURLRequest(BaseModel):
    urls: list[str]
    model: str = "GLM-4-FlashX-250414"
    concurrency: int = 12


class SummarizeUserRequest(BaseModel):
    user: str  # UID or name
    count: int = 50
    model: str = "GLM-4-FlashX-250414"
    concurrency: int = 12


class SummarizeFavRequest(BaseModel):
    count: int = 20
    model: str = "GLM-4-FlashX-250414"
    concurrency: int = 12


# ---------------------------------------------------------------------------
# SSE Progress
# ---------------------------------------------------------------------------
async def send_progress(task_id: str, event: str, data: dict):
    if task_id in progress_queues:
        await progress_queues[task_id].put({"event": event, "data": data})


async def progress_generator(task_id: str):
    queue = progress_queues.setdefault(task_id, asyncio.Queue())
    try:
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=15)
            except asyncio.TimeoutError:
                # Send heartbeat to keep SSE connection alive
                yield ": heartbeat\n\n"
                continue
            yield f"event: {msg['event']}\ndata: {json.dumps(msg['data'], ensure_ascii=False)}\n\n"
            if msg["event"] == "done":
                break
    except Exception:
        yield f"event: done\ndata: {json.dumps({'message': 'connection_error'})}\n\n"
    finally:
        progress_queues.pop(task_id, None)


# ---------------------------------------------------------------------------
# No-subtitle retry logic
# ---------------------------------------------------------------------------
MAX_NOSUB_RETRIES = 3  # Max times to retry a no_subtitle video


def _retries_file(output_subdir: str) -> Path:
    return Path("summary") / output_subdir / "no_subtitle" / ".retries.json"


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
        url = f"https://www.bilibili.com/video/{bvid}"

        # Check existing (both normal and no_subtitle dirs)
        safe_title = sanitize_filename(title)
        normal_path = Path("summary") / output_subdir / f"{safe_title}.md"
        nosub_path = Path("summary") / output_subdir / "no_subtitle" / f"{safe_title}.md"

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

        save_summary(title, bvid, url, duration, summary, final_subdir)

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
    user_dir = Path("summary") / "users" / str(uid)
    user_dir.mkdir(parents=True, exist_ok=True)
    meta_file = user_dir / ".meta.json"
    meta_file.write_text(json.dumps({"uid": uid, "name": name}, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index():
    return (Path("static") / "index.html").read_text(encoding="utf-8")


@app.get("/api/status")
async def get_status():
    return {"logged_in": credential is not None, "ai_configured": ai_client is not None}


@app.get("/api/summaries")
async def list_summaries():
    """List all generated summaries, structured by category."""
    summary_root = Path("summary")
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
            categories.append({"type": "standalone", "label": "独立视频", "icon": "🔗", "count": len(items), "items": items})

    # 2) Favorites
    fav_dir = summary_root / "favorites"
    if fav_dir.exists():
        items = []
        for md in sorted(fav_dir.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
            rel = md.relative_to(summary_root)
            items.append({"name": md.stem, "path": str(rel), "no_subtitle": "no_subtitle" in str(rel)})
        if items:
            categories.append({"type": "favorites", "label": "收藏夹", "icon": "⭐", "count": len(items), "items": items})

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
            categories.append({"type": "users", "label": "UP 主", "icon": "👤", "count": total, "groups": user_groups})

    return {"categories": categories}


@app.get("/api/summary/{path:path}")
async def read_summary(path: str):
    filepath = Path("summary") / path
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


@app.get("/api/progress/{task_id}")
async def progress_stream(task_id: str):
    return StreamingResponse(
        progress_generator(task_id),
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
                env_path = str(Path('.env.local'))
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
    env_path = Path('.env.local')
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
