#!/usr/bin/env python3
"""
BiliSummary macOS App 入口
pywebview 原生窗口 + FastAPI 后端
"""

import threading
import webbrowser
import webview
import uvicorn
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
