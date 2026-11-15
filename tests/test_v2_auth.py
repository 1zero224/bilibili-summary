"""`routes.v2.auth` 的令牌逻辑单测（重点：非 ASCII 令牌不能抛异常变成 500）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import v2_stub_helper  # noqa: E402

v2_stub_helper.install()  # 必须在 import routes.v2.* 之前

from routes.v2 import auth  # noqa: E402


@pytest.fixture()
def token_file(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "_CACHED", None, raising=False)
    monkeypatch.setenv("BILISUMMARY_API_TOKEN", "")
    path = tmp_path / ".api_token"
    monkeypatch.setattr(auth, "token_path", lambda: path)
    return path


def test_首次自动生成_32字节令牌(token_file):
    token = auth.get_api_token()
    assert len(token) == 64
    assert token_file.is_file()
    # 再读一次要稳定
    auth._CACHED = None
    assert auth.get_api_token() == token


def test_env_可覆盖令牌(token_file, monkeypatch):
    monkeypatch.setenv("BILISUMMARY_API_TOKEN", "fixed-token-for-test")
    auth._CACHED = None
    assert auth.get_api_token() == "fixed-token-for-test"


def test_正确令牌通过_错误令牌拒绝(token_file, monkeypatch):
    monkeypatch.setenv("BILISUMMARY_API_TOKEN", "abc123")
    auth._CACHED = None
    assert auth.token_ok("abc123") is True
    assert auth.token_ok("abc124") is False
    assert auth.token_ok("") is False


def test_非_ascii令牌不抛异常(token_file, monkeypatch):
    """曾经的 bug：compare_digest 遇到 '…' 抛 TypeError → 接口 500。"""
    monkeypatch.setenv("BILISUMMARY_API_TOKEN", "abc123")
    auth._CACHED = None
    assert auth.token_ok("not-a-token…x") is False  # 不抛就通过


def test_失败文案带长度提示(token_file, monkeypatch):
    monkeypatch.setenv("BILISUMMARY_API_TOKEN", "a" * 64)
    auth._CACHED = None
    assert "缺少 X-BS-Token" in auth.token_mismatch_detail("")
    detail = auth.token_mismatch_detail("not-a-token…x")
    assert "收到 13 字符" in detail
    assert "应为 64 字符" in detail
