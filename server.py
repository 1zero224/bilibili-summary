#!/usr/bin/env python3
"""
FastAPI 后端：`/api/v2`（插件 / ASR / 总结 / 历史）+ `/history` 页面 + Telegram Bot。

旧 Web UI（URL / UP 主 / 收藏夹 / 浏览 / 设置）已移除：
运行时配置统一由 `.env.local` 或环境变量提供，修改后重启容器生效。
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

import routes.deps as deps
from routes.deps import init_ai_client, init_credential, load_task_logs
from routes.telegram_bot import telegram_bot_service
from routes.v2 import router as v2_router
from routes.v2.auth import get_api_token, request_token, token_ok


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_credential()
    init_ai_client()
    load_task_logs()
    token = get_api_token()
    if AUTH_ENFORCE:
        print(f"🔐 API 令牌鉴权已启用，令牌文件: {os.path.join(deps.DATA_DIR, '.api_token')}")
    else:
        print("🔓 API 令牌鉴权未启用（BILISUMMARY_AUTH=0）：仅建议在本机 127.0.0.1 使用。")
    print(f"🔑 X-BS-Token: {token[:6]}…{token[-4:]}")
    await telegram_bot_service.reload_from_env()
    try:
        yield
    finally:
        await telegram_bot_service.shutdown()


# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------
app = FastAPI(title="Bilibili 视频总结器", lifespan=lifespan)
app.include_router(v2_router)


# ---------------------------------------------------------------------------
# 令牌鉴权 + CORS
# ---------------------------------------------------------------------------
AUTH_ENFORCE = os.getenv("BILISUMMARY_AUTH", "0").strip().lower() in {"1", "true", "yes", "on"}
# /api/status 只回 {logged_in, ai_configured}，保持公开以便 Docker HEALTHCHECK 与探活
AUTH_PUBLIC_PATHS = {"/", "/history", "/api/status", "/api/v2/health"}


def _maybe_set_token_cookie(request: Request, response) -> None:
    """用 `?token=` 首次访问时为 /history 页面种 Cookie，之后同源页面无需再带参数。"""
    candidate = request.query_params.get("token")
    if not candidate or not token_ok(candidate):
        return
    try:
        response.set_cookie(
            "bs_token",
            candidate,
            max_age=30 * 24 * 3600,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
        )
    except Exception:
        pass


@app.middleware("http")
async def token_guard(request: Request, call_next):
    """BILISUMMARY_AUTH=1 时，`/api/*` 一律要求令牌（插件用 X-BS-Token，页面用 Cookie）。"""
    if not AUTH_ENFORCE:
        return await call_next(request)

    path = request.url.path
    if request.method == "OPTIONS" or path in AUTH_PUBLIC_PATHS or not path.startswith("/api/"):
        response = await call_next(request)
        _maybe_set_token_cookie(request, response)
        return response

    if token_ok(request_token(request)):
        response = await call_next(request)
        _maybe_set_token_cookie(request, response)
        return response

    try:
        from routes.v2.auth import token_mismatch_detail

        detail = token_mismatch_detail(request_token(request))
    except Exception:  # noqa: BLE001
        detail = "缺少或错误的 X-BS-Token"
    return JSONResponse(status_code=401, content={"error": "unauthorized", "detail": detail})


app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(chrome-extension|extension)://.*$",
    allow_origins=["http://127.0.0.1:18520", "http://localhost:18520"],
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=False,
    max_age=600,
)


# ---------------------------------------------------------------------------
# 入口与探活
# ---------------------------------------------------------------------------
@app.get("/")
async def index():
    """旧 Web UI 已删除，首页直接跳到服务器历史面板。"""
    return RedirectResponse(url="/history", status_code=307)


@app.get("/api/status")
async def get_status():
    return {"logged_in": deps.credential is not None, "ai_configured": deps.ai_client is not None}


# ---------------------------------------------------------------------------
# 独立运行
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "127.0.0.1")
    try:
        port = int(os.getenv("PORT", "18520"))
    except ValueError:
        port = 18520

    uvicorn.run(app, host=host, port=port)
