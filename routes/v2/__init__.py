"""
`/api/v2` 与服务器历史页的聚合路由（不改动既有 `/api/*`）。

`router` 采用惰性装配：只导入 `routes.v2.store` 时不会牵连
`routes.bilibili_downloader` / `faster_whisper` 等重依赖，便于单测。
"""

from __future__ import annotations

__all__ = ["router"]


def __getattr__(name: str):
    if name != "router":
        raise AttributeError(name)

    from fastapi import APIRouter

    from routes.v2 import api as api_module
    from routes.v2 import page as page_module
    from routes.v2 import summary as summary_module

    router = APIRouter()
    router.include_router(api_module.public_router)
    router.include_router(api_module.router)
    router.include_router(summary_module.router)
    router.include_router(page_module.router)
    globals()["router"] = router
    return router
