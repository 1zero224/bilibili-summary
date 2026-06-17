"""
Settings routes.
"""

import os
import asyncio
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import aiohttp
from dotenv import set_key

from routes.bailian_asr import (
    get_bailian_api_key,
    get_bailian_asr_base_url,
    get_bailian_asr_language,
    get_bailian_asr_model,
)
from routes.deps import DATA_DIR, clean_env_value, default_folder_name, init_ai_client
from routes.r2_storage import (
    DEFAULT_R2_KEY_PREFIX,
    get_cloudflare_r2_access_key_id,
    get_cloudflare_r2_account_id,
    get_cloudflare_r2_bucket,
    get_cloudflare_r2_delete_after_use,
    get_cloudflare_r2_endpoint_url,
    get_cloudflare_r2_key_prefix,
    get_cloudflare_r2_public_base_url,
    get_cloudflare_r2_secret_access_key,
)
from routes.telegram_bot import telegram_bot_service
from routes.whisper import get_asr_mode, get_whisper_compute_type, get_whisper_device, get_whisper_model

router = APIRouter(prefix="/api", tags=["settings"])

TOKEN_PLAN_DEFAULT_BASE_URL = "https://token-plan-cn.xiaomimimo.com/anthropic"

MIMO_CHAT_MODELS = [
    "mimo-v2.5-pro",
    "mimo-v2.5",
    "mimo-v2-pro",
    "mimo-v2-omni",
    "mimo-v2-flash",
]


def _mask_token(token: str) -> str:
    return token[:8] + '***' + token[-4:] if len(token) > 12 else '***'


def _normalize_anthropic_base_url(base_url: str, auth_token: str = "") -> str:
    base_url = clean_env_value(base_url).rstrip("/")
    auth_token = clean_env_value(auth_token)
    if not base_url:
        return TOKEN_PLAN_DEFAULT_BASE_URL if auth_token.startswith("tp-") else ""

    parsed = urlparse(base_url)
    if auth_token.startswith("tp-") and parsed.netloc == "api.xiaomimimo.com":
        return TOKEN_PLAN_DEFAULT_BASE_URL
    if parsed.netloc.startswith("token-plan-") and parsed.netloc.endswith(".xiaomimimo.com"):
        scheme = parsed.scheme or "https"
        return f"{scheme}://{parsed.netloc}/anthropic"
    if parsed.netloc == "api.xiaomimimo.com":
        scheme = parsed.scheme or "https"
        return f"{scheme}://{parsed.netloc}/anthropic"

    return base_url


def _known_models_for_base_url(base_url: str) -> Optional[list[dict]]:
    parsed = urlparse(clean_env_value(base_url))
    if parsed.netloc == "api.xiaomimimo.com" or (
        parsed.netloc.startswith("token-plan-") and parsed.netloc.endswith(".xiaomimimo.com")
    ):
        return [{"id": model, "owned_by": "xiaomi-mimo"} for model in MIMO_CHAT_MODELS]
    return None


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _normalize_asr_mode(mode: str) -> str:
    value = clean_env_value(mode).lower()
    return value if value in {"local", "bailian"} else "local"


@router.get("/settings")
async def get_settings():
    """Return current API settings (token partially masked)."""
    from routes.deps import DEFAULT_MODEL, get_task_concurrency
    token = clean_env_value(os.getenv('ANTHROPIC_AUTH_TOKEN') or os.getenv('MIMO_API_KEY'))
    telegram_token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    return {
        "asr_mode": get_asr_mode(),
        "base_url": clean_env_value(os.getenv('ANTHROPIC_BASE_URL')),
        "auth_token_masked": _mask_token(token) if token else '输入 API Token',
        "default_model": DEFAULT_MODEL,
        "task_concurrency": get_task_concurrency(),
        "whisper_model": get_whisper_model(),
        "whisper_device": get_whisper_device(),
        "whisper_compute_type": get_whisper_compute_type(),
        "bailian_api_key_masked": _mask_token(get_bailian_api_key()) if get_bailian_api_key() else '输入百炼 API Key',
        "bailian_asr_base_url": get_bailian_asr_base_url(),
        "bailian_asr_model": get_bailian_asr_model(),
        "bailian_asr_language": get_bailian_asr_language(),
        "cloudflare_r2_account_id": get_cloudflare_r2_account_id(),
        "cloudflare_r2_endpoint_url": get_cloudflare_r2_endpoint_url(),
        "cloudflare_r2_bucket": get_cloudflare_r2_bucket(),
        "cloudflare_r2_access_key_id": get_cloudflare_r2_access_key_id(),
        "cloudflare_r2_secret_access_key_masked": (
            _mask_token(get_cloudflare_r2_secret_access_key())
            if get_cloudflare_r2_secret_access_key()
            else '输入 R2 Secret Access Key'
        ),
        "cloudflare_r2_public_base_url": get_cloudflare_r2_public_base_url(),
        "cloudflare_r2_key_prefix": get_cloudflare_r2_key_prefix(),
        "cloudflare_r2_delete_after_use": get_cloudflare_r2_delete_after_use(),
        "telegram_bot_enabled": _env_bool('TELEGRAM_BOT_ENABLED', False),
        "telegram_bot_token_masked": _mask_token(telegram_token) if telegram_token else '输入 Bot Token',
        "telegram_allowed_user_ids": os.getenv('TELEGRAM_ALLOWED_USER_IDS', ''),
        "telegram_output_folder": os.getenv('TELEGRAM_OUTPUT_FOLDER', default_folder_name()),
        "telegram_bot_running": telegram_bot_service.is_running,
        "telegram_bot_last_error": telegram_bot_service.last_error,
    }


class SaveSettingsRequest(BaseModel):
    asr_mode: str = ""
    base_url: str = ""
    auth_token: str = ""  # empty = don't change
    default_model: str = ""
    task_concurrency: int | None = Field(default=None, ge=1, le=20)
    whisper_model: str = ""
    whisper_device: str = ""
    whisper_compute_type: str = ""
    bailian_api_key: str = ""
    bailian_asr_base_url: str = ""
    bailian_asr_model: str = ""
    bailian_asr_language: str = ""
    cloudflare_r2_account_id: str = ""
    cloudflare_r2_endpoint_url: str = ""
    cloudflare_r2_bucket: str = ""
    cloudflare_r2_access_key_id: str = ""
    cloudflare_r2_secret_access_key: str = ""
    cloudflare_r2_public_base_url: str = ""
    cloudflare_r2_key_prefix: str = ""
    cloudflare_r2_delete_after_use: bool | None = None
    telegram_bot_enabled: bool | None = None
    telegram_bot_token: str = ""
    telegram_allowed_user_ids: str = ""
    telegram_output_folder: str = ""


@router.post("/settings")
async def save_settings(req: SaveSettingsRequest):
    """Save API settings to .env.local and hot-reload ai_client."""
    import routes.deps as deps

    env_path = str(DATA_DIR / '.env.local')
    changed = []

    if req.asr_mode:
        value = _normalize_asr_mode(req.asr_mode)
        set_key(env_path, 'ASR_MODE', value)
        os.environ['ASR_MODE'] = value
        changed.append('asr_mode')

    existing_token = clean_env_value(os.getenv('ANTHROPIC_AUTH_TOKEN') or os.getenv('MIMO_API_KEY'))
    incoming_token = clean_env_value(req.auth_token) if req.auth_token and '***' not in req.auth_token else existing_token
    base_url = _normalize_anthropic_base_url(req.base_url, incoming_token)
    if base_url:
        set_key(env_path, 'ANTHROPIC_BASE_URL', base_url)
        os.environ['ANTHROPIC_BASE_URL'] = base_url
        changed.append('base_url')

    if req.auth_token and '***' not in req.auth_token:
        set_key(env_path, 'ANTHROPIC_AUTH_TOKEN', incoming_token)
        os.environ['ANTHROPIC_AUTH_TOKEN'] = incoming_token
        changed.append('auth_token')

    if req.default_model:
        set_key(env_path, 'DEFAULT_MODEL', req.default_model)
        os.environ['DEFAULT_MODEL'] = req.default_model
        deps.DEFAULT_MODEL = req.default_model
        changed.append('default_model')

    if req.task_concurrency is not None:
        value = max(1, min(20, int(req.task_concurrency)))
        set_key(env_path, 'TASK_CONCURRENCY', str(value))
        os.environ['TASK_CONCURRENCY'] = str(value)
        deps.set_task_concurrency(value)
        changed.append('task_concurrency')

    if req.whisper_model:
        set_key(env_path, 'WHISPER_MODEL', req.whisper_model)
        os.environ['WHISPER_MODEL'] = req.whisper_model
        changed.append('whisper_model')

    if req.whisper_device:
        set_key(env_path, 'WHISPER_DEVICE', req.whisper_device)
        os.environ['WHISPER_DEVICE'] = req.whisper_device
        changed.append('whisper_device')

    if req.whisper_compute_type:
        set_key(env_path, 'WHISPER_COMPUTE_TYPE', req.whisper_compute_type)
        os.environ['WHISPER_COMPUTE_TYPE'] = req.whisper_compute_type
        changed.append('whisper_compute_type')

    if req.bailian_api_key and '***' not in req.bailian_api_key:
        set_key(env_path, 'BAILIAN_API_KEY', clean_env_value(req.bailian_api_key))
        os.environ['BAILIAN_API_KEY'] = clean_env_value(req.bailian_api_key)
        changed.append('bailian_api_key')

    if req.bailian_asr_base_url or req.bailian_asr_base_url == "":
        value = req.bailian_asr_base_url.strip().rstrip("/") or "https://dashscope.aliyuncs.com/api/v1"
        set_key(env_path, 'BAILIAN_ASR_BASE_URL', value)
        os.environ['BAILIAN_ASR_BASE_URL'] = value
        changed.append('bailian_asr_base_url')

    if req.bailian_asr_model or req.bailian_asr_model == "":
        value = req.bailian_asr_model.strip() or "qwen3-asr-flash-filetrans"
        set_key(env_path, 'BAILIAN_ASR_MODEL', value)
        os.environ['BAILIAN_ASR_MODEL'] = value
        changed.append('bailian_asr_model')

    if req.bailian_asr_language or req.bailian_asr_language == "":
        # 保留空字符串用于关闭 language 参数。
        set_key(env_path, 'BAILIAN_ASR_LANGUAGE', req.bailian_asr_language.strip())
        os.environ['BAILIAN_ASR_LANGUAGE'] = req.bailian_asr_language.strip()
        changed.append('bailian_asr_language')

    if req.cloudflare_r2_account_id or req.cloudflare_r2_account_id == "":
        value = req.cloudflare_r2_account_id.strip()
        set_key(env_path, 'CLOUDFLARE_R2_ACCOUNT_ID', value)
        os.environ['CLOUDFLARE_R2_ACCOUNT_ID'] = value
        changed.append('cloudflare_r2_account_id')

    if req.cloudflare_r2_endpoint_url or req.cloudflare_r2_endpoint_url == "":
        value = req.cloudflare_r2_endpoint_url.strip().rstrip("/")
        set_key(env_path, 'CLOUDFLARE_R2_ENDPOINT_URL', value)
        os.environ['CLOUDFLARE_R2_ENDPOINT_URL'] = value
        changed.append('cloudflare_r2_endpoint_url')

    if req.cloudflare_r2_bucket or req.cloudflare_r2_bucket == "":
        value = req.cloudflare_r2_bucket.strip()
        set_key(env_path, 'CLOUDFLARE_R2_BUCKET', value)
        os.environ['CLOUDFLARE_R2_BUCKET'] = value
        changed.append('cloudflare_r2_bucket')

    if req.cloudflare_r2_access_key_id or req.cloudflare_r2_access_key_id == "":
        value = req.cloudflare_r2_access_key_id.strip()
        set_key(env_path, 'CLOUDFLARE_R2_ACCESS_KEY_ID', value)
        os.environ['CLOUDFLARE_R2_ACCESS_KEY_ID'] = value
        changed.append('cloudflare_r2_access_key_id')

    if req.cloudflare_r2_secret_access_key and '***' not in req.cloudflare_r2_secret_access_key:
        value = clean_env_value(req.cloudflare_r2_secret_access_key)
        set_key(env_path, 'CLOUDFLARE_R2_SECRET_ACCESS_KEY', value)
        os.environ['CLOUDFLARE_R2_SECRET_ACCESS_KEY'] = value
        changed.append('cloudflare_r2_secret_access_key')

    if req.cloudflare_r2_public_base_url or req.cloudflare_r2_public_base_url == "":
        value = req.cloudflare_r2_public_base_url.strip().rstrip("/")
        set_key(env_path, 'CLOUDFLARE_R2_PUBLIC_BASE_URL', value)
        os.environ['CLOUDFLARE_R2_PUBLIC_BASE_URL'] = value
        changed.append('cloudflare_r2_public_base_url')

    if req.cloudflare_r2_key_prefix or req.cloudflare_r2_key_prefix == "":
        value = req.cloudflare_r2_key_prefix.strip().strip("/") or DEFAULT_R2_KEY_PREFIX
        set_key(env_path, 'CLOUDFLARE_R2_KEY_PREFIX', value)
        os.environ['CLOUDFLARE_R2_KEY_PREFIX'] = value
        changed.append('cloudflare_r2_key_prefix')

    if req.cloudflare_r2_delete_after_use is not None:
        value = 'true' if req.cloudflare_r2_delete_after_use else 'false'
        set_key(env_path, 'CLOUDFLARE_R2_DELETE_AFTER_USE', value)
        os.environ['CLOUDFLARE_R2_DELETE_AFTER_USE'] = value
        changed.append('cloudflare_r2_delete_after_use')

    if req.telegram_bot_enabled is not None:
        value = 'true' if req.telegram_bot_enabled else 'false'
        set_key(env_path, 'TELEGRAM_BOT_ENABLED', value)
        os.environ['TELEGRAM_BOT_ENABLED'] = value
        changed.append('telegram_bot_enabled')

    if req.telegram_bot_token and '***' not in req.telegram_bot_token:
        set_key(env_path, 'TELEGRAM_BOT_TOKEN', req.telegram_bot_token)
        os.environ['TELEGRAM_BOT_TOKEN'] = req.telegram_bot_token
        changed.append('telegram_bot_token')

    allowed_user_ids = req.telegram_allowed_user_ids.strip()
    set_key(env_path, 'TELEGRAM_ALLOWED_USER_IDS', allowed_user_ids)
    os.environ['TELEGRAM_ALLOWED_USER_IDS'] = allowed_user_ids
    changed.append('telegram_allowed_user_ids')

    output_folder = req.telegram_output_folder.strip() or default_folder_name()
    set_key(env_path, 'TELEGRAM_OUTPUT_FOLDER', output_folder)
    os.environ['TELEGRAM_OUTPUT_FOLDER'] = output_folder
    changed.append('telegram_output_folder')

    # Hot-reload AI client
    init_ai_client()
    await telegram_bot_service.reload_from_env()

    return {
        "success": True,
        "changed": changed,
        "telegram_bot_running": telegram_bot_service.is_running,
        "telegram_bot_last_error": telegram_bot_service.last_error,
    }


@router.get("/models")
async def list_models():
    """Fetch available models from the API provider's /v1/models endpoint."""
    from routes.deps import DEFAULT_MODEL
    base_url = clean_env_value(os.getenv('ANTHROPIC_BASE_URL'))
    token = clean_env_value(os.getenv('ANTHROPIC_AUTH_TOKEN') or os.getenv('MIMO_API_KEY'))

    known_models = _known_models_for_base_url(base_url)
    if known_models is not None:
        return {"models": known_models, "current": DEFAULT_MODEL}

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
                for item in data.get('data', []):
                    models.append({
                        "id": item.get('id', ''),
                        "owned_by": item.get('owned_by', ''),
                    })
                models.sort(key=lambda item: item['id'])
                return {"models": models, "current": DEFAULT_MODEL}
    except asyncio.TimeoutError:
        return JSONResponse(status_code=504, content={"error": "请求超时"})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})
