#!/usr/bin/env python3
"""
FastAPI 后端服务器
提供 REST API + SSE 实时进度推送
"""

import os
import asyncio
import json
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel

from bilibili_api import user as bili_user

from summarize import extract_bvid, get_uid_by_name, get_user_videos, get_favorite_videos, sanitize_filename

import routes.deps as deps
from routes.deps import (
    BUNDLE_DIR, DATA_DIR,
    init_credential, init_ai_client,
    send_progress, progress_generator,
    process_single_video, run_batch, save_user_meta,
)
from routes.favorites import router as favorites_router
from routes.asr import router as asr_router
from routes.settings import router as settings_router
from routes.auth import router as auth_router


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
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

# Include route modules
app.include_router(favorites_router)
app.include_router(asr_router)
app.include_router(settings_router)
app.include_router(auth_router)


# ---------------------------------------------------------------------------
# Request Models
# ---------------------------------------------------------------------------
class SummarizeURLRequest(BaseModel):
    urls: list[str]
    model: str = ""
    concurrency: int = 12


class SummarizeUserRequest(BaseModel):
    user: str  # UID or name
    count: int = 50
    model: str = ""
    concurrency: int = 12


class SummarizeFavRequest(BaseModel):
    count: int = 20
    model: str = ""
    concurrency: int = 12


# ---------------------------------------------------------------------------
# Core API Endpoints
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index():
    return (BUNDLE_DIR / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/api/status")
async def get_status():
    return {"logged_in": deps.credential is not None, "ai_configured": deps.ai_client is not None}


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

    model = req.model or deps.DEFAULT_MODEL
    asyncio.create_task(run_batch(bvids, model, req.concurrency, "standalone", task_id))
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
            u = bili_user.User(uid=uid, credential=deps.credential)
            user_info = await u.get_user_info()
            resolved_name = user_info.get('name', username or str(uid))
        except Exception:
            resolved_name = username or str(uid)

        save_user_meta(uid, resolved_name)

        model = req.model or deps.DEFAULT_MODEL
        await send_progress(task_id, "info", {"message": f"获取 UP 主 {resolved_name} (UID:{uid}) 的最新 {req.count} 个视频..."})
        bvids = await get_user_videos(uid, req.count, deps.credential)

        if not bvids:
            await send_progress(task_id, "error", {"message": "未找到视频"})
            await send_progress(task_id, "done", {"total": 0, "success": 0, "skipped": 0, "no_subtitle": 0, "errors": 0})
            return

        await run_batch(bvids, model, req.concurrency, f"users/{uid}", task_id)

    asyncio.create_task(_run())
    return {"task_id": task_id}


@app.post("/api/summarize/favorites")
async def summarize_favorites(req: SummarizeFavRequest):
    if not deps.credential:
        return JSONResponse(status_code=401, content={"error": "未登录 Bilibili"})

    task_id = f"fav-{int(time.time()*1000)}"

    async def _run():
        model = req.model or deps.DEFAULT_MODEL
        await send_progress(task_id, "info", {"message": f"获取默认收藏夹的最新 {req.count} 个视频..."})
        bvids = await get_favorite_videos(req.count, deps.credential)

        if not bvids:
            await send_progress(task_id, "error", {"message": "未找到视频"})
            await send_progress(task_id, "done", {"total": 0, "success": 0, "skipped": 0, "no_subtitle": 0, "errors": 0})
            return

        await run_batch(bvids, model, req.concurrency, "favorites", task_id)

    asyncio.create_task(_run())
    return {"task_id": task_id}


@app.get("/api/progress/{task_id}")
async def progress_stream(task_id: str, request: Request):
    last_id = int(request.headers.get("Last-Event-ID", "-1"))
    return StreamingResponse(
        progress_generator(task_id, last_id=last_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


# ---------------------------------------------------------------------------
# Run standalone
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=18520)
