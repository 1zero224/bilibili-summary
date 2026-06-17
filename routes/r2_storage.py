"""
Cloudflare R2 S3 兼容上传辅助函数。
"""

import datetime as dt
import hashlib
import hmac
import mimetypes
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import quote, urlparse

import aiohttp


ProgressCallback = Callable[[str], Awaitable[None]]

DEFAULT_R2_KEY_PREFIX = "bilibili-summary/asr"
R2_REGION = "auto"
S3_SERVICE = "s3"


def _clean_env(value: str | None) -> str:
    return (value or "").strip().strip("'\"")


def get_cloudflare_r2_endpoint_url() -> str:
    endpoint = _clean_env(os.getenv("CLOUDFLARE_R2_ENDPOINT_URL"))
    if endpoint:
        return endpoint.rstrip("/")

    account_id = get_cloudflare_r2_account_id()
    if account_id:
        return f"https://{account_id}.r2.cloudflarestorage.com"
    return ""


def get_cloudflare_r2_account_id() -> str:
    return _clean_env(os.getenv("CLOUDFLARE_R2_ACCOUNT_ID"))


def get_cloudflare_r2_bucket() -> str:
    return _clean_env(os.getenv("CLOUDFLARE_R2_BUCKET"))


def get_cloudflare_r2_access_key_id() -> str:
    return _clean_env(os.getenv("CLOUDFLARE_R2_ACCESS_KEY_ID"))


def get_cloudflare_r2_secret_access_key() -> str:
    return _clean_env(os.getenv("CLOUDFLARE_R2_SECRET_ACCESS_KEY"))


def get_cloudflare_r2_public_base_url() -> str:
    return _clean_env(os.getenv("CLOUDFLARE_R2_PUBLIC_BASE_URL")).rstrip("/")


def get_cloudflare_r2_key_prefix() -> str:
    return _clean_env(os.getenv("CLOUDFLARE_R2_KEY_PREFIX")) or DEFAULT_R2_KEY_PREFIX


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def get_cloudflare_r2_delete_after_use() -> bool:
    return _env_bool("CLOUDFLARE_R2_DELETE_AFTER_USE", True)


@dataclass(frozen=True)
class CloudflareR2Config:
    endpoint_url: str
    bucket: str
    access_key_id: str
    secret_access_key: str
    public_base_url: str
    key_prefix: str = DEFAULT_R2_KEY_PREFIX

    @property
    def host(self) -> str:
        parsed = urlparse(self.endpoint_url)
        return parsed.netloc

    def validate(self):
        missing = []
        if not self.endpoint_url:
            missing.append("R2 S3 API Endpoint")
        if not self.bucket:
            missing.append("R2 Bucket")
        if not self.access_key_id:
            missing.append("R2 Access Key ID")
        if not self.secret_access_key:
            missing.append("R2 Secret Access Key")
        if not self.public_base_url:
            missing.append("R2 Public Base URL")
        if missing:
            raise RuntimeError(f"未配置 Cloudflare {', '.join(missing)}，请在设置页填写 R2 配置")

        endpoint = urlparse(self.endpoint_url)
        if endpoint.scheme != "https" or not endpoint.netloc:
            raise RuntimeError("Cloudflare R2 S3 API Endpoint 必须是 https:// 地址")
        public_base = urlparse(self.public_base_url)
        if public_base.scheme not in {"http", "https"} or not public_base.netloc:
            raise RuntimeError("Cloudflare R2 Public Base URL 必须是公网可访问的 http:// 或 https:// 地址")


@dataclass(frozen=True)
class CloudflareR2UploadResult:
    object_key: str
    public_url: str


def get_cloudflare_r2_config() -> CloudflareR2Config:
    return CloudflareR2Config(
        endpoint_url=get_cloudflare_r2_endpoint_url(),
        bucket=get_cloudflare_r2_bucket(),
        access_key_id=get_cloudflare_r2_access_key_id(),
        secret_access_key=get_cloudflare_r2_secret_access_key(),
        public_base_url=get_cloudflare_r2_public_base_url(),
        key_prefix=get_cloudflare_r2_key_prefix(),
    )


async def _notify(progress: ProgressCallback | None, message: str):
    if progress:
        await progress(message)


def _file_sha256(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _signature_key(secret_access_key: str, date_stamp: str) -> bytes:
    date_key = _sign(("AWS4" + secret_access_key).encode("utf-8"), date_stamp)
    region_key = _sign(date_key, R2_REGION)
    service_key = _sign(region_key, S3_SERVICE)
    return _sign(service_key, "aws4_request")


def _safe_object_segment(value: str, fallback: str = "media") -> str:
    segment = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    segment = segment.strip(".-_")
    return (segment or fallback)[:96]


def _clean_key_prefix(prefix: str) -> str:
    parts = [_safe_object_segment(part, "") for part in prefix.strip("/").split("/")]
    return "/".join(part for part in parts if part)


def build_r2_object_key(file_path: Path, *, key_prefix: str = "", object_name_hint: str = "", file_hash: str = "") -> str:
    suffix = file_path.suffix.lower() or ".bin"
    stem = _safe_object_segment(object_name_hint or file_path.stem)
    fingerprint = (file_hash or _file_sha256(file_path))[:16]
    filename = f"{stem}-{fingerprint}{suffix}"
    prefix = _clean_key_prefix(key_prefix)
    return f"{prefix}/{filename}" if prefix else filename


def build_r2_public_url(public_base_url: str, object_key: str) -> str:
    return f"{public_base_url.rstrip('/')}/{quote(object_key, safe='/')}"


def _quote_query_value(value: str) -> str:
    return quote(str(value), safe="-_.~")


def _canonical_query_string(params: dict[str, str]) -> str:
    return "&".join(
        f"{_quote_query_value(key)}={_quote_query_value(value)}"
        for key, value in sorted(params.items())
    )


def create_cloudflare_r2_presigned_get_url(object_key: str, expires_seconds: int = 24 * 3600) -> str:
    config = get_cloudflare_r2_config()
    config.validate()

    object_key = object_key.strip()
    if not object_key:
        raise RuntimeError("Cloudflare R2 签名 URL 生成失败: object_key 为空")

    expires_seconds = max(60, min(7 * 24 * 3600, int(expires_seconds or 0)))
    now = dt.datetime.now(dt.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    credential_scope = f"{date_stamp}/{R2_REGION}/{S3_SERVICE}/aws4_request"

    bucket = quote(config.bucket, safe="")
    key_path = quote(object_key, safe="/")
    canonical_uri = f"/{bucket}/{key_path}"
    params = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{config.access_key_id}/{credential_scope}",
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": str(expires_seconds),
        "X-Amz-SignedHeaders": "host",
    }
    canonical_query = _canonical_query_string(params)
    canonical_request = "\n".join([
        "GET",
        canonical_uri,
        canonical_query,
        f"host:{config.host}\n",
        "host",
        "UNSIGNED-PAYLOAD",
    ])
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256",
        amz_date,
        credential_scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])
    signature = hmac.new(
        _signature_key(config.secret_access_key, date_stamp),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    params["X-Amz-Signature"] = signature
    return f"{config.endpoint_url}{canonical_uri}?{_canonical_query_string(params)}"


def _content_type_for(file_path: Path, content_type: str = "") -> str:
    if content_type:
        return content_type
    guessed, _ = mimetypes.guess_type(file_path.name)
    return guessed or "application/octet-stream"


def _authorization_headers(
    config: CloudflareR2Config,
    *,
    method: str,
    canonical_uri: str,
    payload_hash: str,
    now: dt.datetime,
    content_type: str = "",
) -> dict[str, str]:
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    credential_scope = f"{date_stamp}/{R2_REGION}/{S3_SERVICE}/aws4_request"

    signed_header_values = {
        "host": config.host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    if content_type:
        signed_header_values["content-type"] = content_type

    header_names = sorted(signed_header_values)
    signed_headers = ";".join(header_names)
    canonical_headers = "".join(
        f"{name}:{signed_header_values[name]}\n"
        for name in header_names
    )
    canonical_request = "\n".join([
        method,
        canonical_uri,
        "",
        canonical_headers,
        signed_headers,
        payload_hash,
    ])
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256",
        amz_date,
        credential_scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])
    signature = hmac.new(
        _signature_key(config.secret_access_key, date_stamp),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    headers = {
        "Authorization": (
            "AWS4-HMAC-SHA256 "
            f"Credential={config.access_key_id}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, "
            f"Signature={signature}"
        ),
        "Host": config.host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    if content_type:
        headers["Content-Type"] = content_type
    return headers


async def upload_file_to_cloudflare_r2(
    file_path: Path,
    *,
    object_name_hint: str = "",
    content_type: str = "",
    progress: ProgressCallback | None = None,
) -> CloudflareR2UploadResult:
    config = get_cloudflare_r2_config()
    config.validate()

    if not file_path.is_file() or file_path.stat().st_size <= 0:
        raise RuntimeError("Cloudflare R2 上传失败: 文件不存在或为空")

    file_hash = _file_sha256(file_path)
    object_key = build_r2_object_key(
        file_path,
        key_prefix=config.key_prefix,
        object_name_hint=object_name_hint,
        file_hash=file_hash,
    )
    public_url = build_r2_public_url(config.public_base_url, object_key)
    content_type = _content_type_for(file_path, content_type)
    bucket = quote(config.bucket, safe="")
    key_path = quote(object_key, safe="/")
    canonical_uri = f"/{bucket}/{key_path}"
    upload_url = f"{config.endpoint_url}{canonical_uri}"
    headers = _authorization_headers(
        config,
        method="PUT",
        canonical_uri=canonical_uri,
        payload_hash=file_hash,
        content_type=content_type,
        now=dt.datetime.now(dt.timezone.utc),
    )
    headers["Content-Length"] = str(file_path.stat().st_size)

    await _notify(progress, "上传音频到 Cloudflare R2")
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=300)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        with file_path.open("rb") as file:
            async with session.put(upload_url, headers=headers, data=file) as resp:
                text = await resp.text()
                if resp.status < 200 or resp.status >= 300:
                    raise RuntimeError(f"Cloudflare R2 上传失败 HTTP {resp.status}: {text[:500]}")

    await _notify(progress, "Cloudflare R2 上传完成")
    return CloudflareR2UploadResult(object_key=object_key, public_url=public_url)


async def delete_file_from_cloudflare_r2(
    object_key: str,
    *,
    progress: ProgressCallback | None = None,
) -> None:
    config = get_cloudflare_r2_config()
    config.validate()

    if not object_key.strip():
        return

    bucket = quote(config.bucket, safe="")
    key_path = quote(object_key, safe="/")
    canonical_uri = f"/{bucket}/{key_path}"
    delete_url = f"{config.endpoint_url}{canonical_uri}"
    payload_hash = hashlib.sha256(b"").hexdigest()
    headers = _authorization_headers(
        config,
        method="DELETE",
        canonical_uri=canonical_uri,
        payload_hash=payload_hash,
        now=dt.datetime.now(dt.timezone.utc),
    )

    await _notify(progress, "删除 Cloudflare R2 临时音频")
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.delete(delete_url, headers=headers) as resp:
            text = await resp.text()
            if resp.status < 200 or resp.status >= 300:
                raise RuntimeError(f"Cloudflare R2 删除失败 HTTP {resp.status}: {text[:500]}")

    await _notify(progress, "Cloudflare R2 临时音频已删除")
