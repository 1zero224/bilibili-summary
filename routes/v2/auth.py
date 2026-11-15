"""
/api/v2 的访问令牌与鉴权依赖。

设计：
- 令牌落 ``DATA_DIR/.api_token``（0600），首次启动自动生成 32 字节随机串。
- 插件请求带 ``X-BS-Token`` 头；Web UI 可用 ``?token=`` 一次性换取 Cookie（见 server.py 中间件）。
- 比较用 ``secrets.compare_digest``，避免时序侧信道。
"""

from __future__ import annotations

import os
import secrets

from fastapi import HTTPException, Request

from routes.deps import DATA_DIR

TOKEN_FILENAME = ".api_token"
_CACHED: str | None = None


def token_path():
    return DATA_DIR / TOKEN_FILENAME


def get_api_token(force_reload: bool = False) -> str:
    """读取或生成访问令牌。"""
    global _CACHED
    if _CACHED and not force_reload:
        return _CACHED

    path = token_path()
    if path.is_file():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            _CACHED = value
            return value

    value = os.getenv("BILISUMMARY_API_TOKEN", "").strip() or secrets.token_hex(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    _CACHED = value
    return value


def request_token(request: Request) -> str:
    """从 header / query / cookie 里取令牌。"""
    header = request.headers.get("x-bs-token") or request.headers.get("X-BS-Token")
    if header:
        return header.strip()
    query = request.query_params.get("token")
    if query:
        return query.strip()
    cookie = request.cookies.get("bs_token")
    return (cookie or "").strip()


def token_ok(candidate: str) -> bool:
    """常量时间比较；先转 bytes，避免 candidate 含非 ASCII 时 compare_digest 抛 TypeError（会变成 500）。"""
    if not candidate:
        return False
    try:
        return secrets.compare_digest(candidate.encode("utf-8"), get_api_token().encode("utf-8"))
    except Exception:  # noqa: BLE001
        return False


def token_mismatch_detail(candidate: str) -> str:
    """比对失败时给出可自查的信息（不泄露令牌本身）。"""
    expected_len = len(get_api_token())
    got_len = len(candidate or "")
    if not got_len:
        return f"缺少 X-BS-Token（服务器令牌 {expected_len} 字符）"
    return f"令牌不匹配（收到 {got_len} 字符，应为 {expected_len} 字符）"


async def require_token(request: Request) -> None:
    """FastAPI 依赖：校验 ``X-BS-Token``。"""
    candidate = request_token(request)
    if not token_ok(candidate):
        raise HTTPException(status_code=401, detail=token_mismatch_detail(candidate))
