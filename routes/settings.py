"""
Settings & Model Selection routes.
"""

import os
import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import aiohttp
from dotenv import set_key

from routes.deps import DATA_DIR, init_ai_client

router = APIRouter(prefix="/api", tags=["settings"])


@router.get("/settings")
async def get_settings():
    """Return current API settings (token partially masked)."""
    from routes.deps import DEFAULT_MODEL
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


@router.post("/settings")
async def save_settings(req: SaveSettingsRequest):
    """Save API settings to .env.local and hot-reload ai_client."""
    import routes.deps as deps

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
        deps.DEFAULT_MODEL = req.default_model
        changed.append('default_model')

    # Hot-reload AI client
    init_ai_client()

    return {"success": True, "changed": changed}


@router.get("/models")
async def list_models():
    """Fetch available models from the API provider's /v1/models endpoint."""
    from routes.deps import DEFAULT_MODEL
    base_url = os.getenv('ANTHROPIC_BASE_URL', '')
    token = os.getenv('ANTHROPIC_AUTH_TOKEN', '')

    if not base_url or not token:
        return JSONResponse(status_code=400, content={"error": "API 未配置"})

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
                models.sort(key=lambda x: x['id'])
                return {"models": models, "current": DEFAULT_MODEL}
    except asyncio.TimeoutError:
        return JSONResponse(status_code=504, content={"error": "请求超时"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
