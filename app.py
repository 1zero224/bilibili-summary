#!/usr/bin/env python3
"""
BiliSummary macOS App 入口
pywebview 原生窗口 + FastAPI 后端
"""

import sys
import os
import threading
import webbrowser
import webview
import uvicorn

# ---------------------------------------------------------------------------
# Path resolution for PyInstaller bundle vs development
# ---------------------------------------------------------------------------
def get_bundle_dir():
    """Directory containing bundled resources (static/, config.toml)."""
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def get_data_dir():
    """Directory for user data (summary/, ass/, .env.local).
    In bundled mode: ~/Library/Application Support/BiliSummary
    In dev mode: project root (same as bundle_dir)
    """
    if getattr(sys, 'frozen', False):
        data_dir = os.path.join(
            os.path.expanduser('~'), 'Library', 'Application Support', 'BiliSummary'
        )
        os.makedirs(data_dir, exist_ok=True)
        return data_dir
    return os.path.dirname(os.path.abspath(__file__))


# Set paths as env vars so server.py and summarize.py can access them
os.environ['BILISUMMARY_BUNDLE_DIR'] = get_bundle_dir()
os.environ['BILISUMMARY_DATA_DIR'] = get_data_dir()

# Now import server (after env vars are set)
from server import app as fastapi_app


class JsApi:
    """Expose Python functions to JavaScript via pywebview."""
    def open_url(self, url: str):
        """Open URL in system default browser."""
        webbrowser.open(url)


def start_server():
    uvicorn.run(fastapi_app, host="127.0.0.1", port=18520, log_level="warning")


if __name__ == "__main__":
    # Start FastAPI in background thread
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()

    api = JsApi()

    # Create native window
    webview.create_window(
        "BiliSummary — Bilibili 视频总结器",
        url="http://127.0.0.1:18520",
        width=1100,
        height=720,
        min_size=(900, 600),
        js_api=api,
    )
    webview.start()
