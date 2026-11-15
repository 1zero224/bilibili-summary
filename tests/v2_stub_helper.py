"""`routes/v2` 单测的重依赖打桩工具。

两个测试文件（`test_v2_store.py` / `test_v2_summary.py`）都要在**没装
`bilibili_api` / `anthropic` / `faster-whisper`** 的环境下独立跑起来，
所以先把 `routes.deps` 与 `routes.bilibili_downloader` 替换成最小桩模块；
在依赖齐全的环境里则保持真实模块不动（`import` 成功即跳过打桩）。

必须**在** `import routes.v2.*` **之前**调用 `install()`。
"""

from __future__ import annotations

import importlib
import sys
import tempfile
import types
from pathlib import Path

_installed = False


def _stub_deps(root: Path) -> None:
    deps = types.ModuleType("routes.deps")
    deps.DATA_DIR = root

    def summary_asset_path(summary_path: Path, asset: str) -> Path:
        if asset != "detailed_summary":
            raise ValueError(f"未知资产类型: {asset}")
        return summary_path.with_name(f"{summary_path.stem}.detailed.md")

    def save_summary_asset(summary_path: Path, asset: str, content: str) -> None:
        summary_asset_path(summary_path, asset).write_text(content or "", encoding="utf-8")

    deps.summary_asset_path = summary_asset_path
    deps.save_summary_asset = save_summary_asset
    deps.credential = None
    deps.ai_client = None
    sys.modules["routes.deps"] = deps


def _stub_downloader() -> None:
    dl = types.ModuleType("routes.bilibili_downloader")
    dl.USER_AGENT = "ua"
    dl.REFERER = "https://www.bilibili.com/"

    async def _get_playurl(*_a, **_k):
        return {}

    dl._get_playurl = _get_playurl
    dl._parse_dash_streams = lambda _p: ([], [])
    sys.modules["routes.bilibili_downloader"] = dl


def install() -> Path:
    """返回桩的 DATA_DIR（依赖齐全时返回真实 DATA_DIR）。"""
    global _installed
    root = Path(tempfile.mkdtemp(prefix="bs-v2-stub-"))
    if _installed and "routes.deps" in sys.modules:
        return getattr(sys.modules["routes.deps"], "DATA_DIR", root)

    try:
        deps = importlib.import_module("routes.deps")
        root = getattr(deps, "DATA_DIR", root)
    except Exception:  # noqa: BLE001 - 任何导入失败（缺第三方库）都走桩
        _stub_deps(root)

    try:
        importlib.import_module("routes.bilibili_downloader")
    except Exception:  # noqa: BLE001
        _stub_downloader()

    _installed = True
    return root
