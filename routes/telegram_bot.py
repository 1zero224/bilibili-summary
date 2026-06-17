"""
Telegram Bot integration.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import dataclass
from typing import Any

import aiohttp

import routes.deps as deps
from summarize import extract_bvid


BOT_API_BASE = "https://api.telegram.org/bot"
MAX_BVIDS_PER_MESSAGE = 20
EDIT_INTERVAL_SEC = 2.0
URL_RE = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)
BVID_RE = re.compile(r"BV[0-9A-Za-z]+")
TRUTHY_VALUES = {"1", "true", "yes", "on", "enabled"}


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in TRUTHY_VALUES


def _parse_allowed_user_ids(value: str) -> set[int]:
    ids: set[int] = set()
    for part in re.split(r"[\s,;]+", value or ""):
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            continue
    return ids


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


@dataclass(frozen=True)
class TelegramBotConfig:
    enabled: bool
    token: str
    allowed_user_ids: set[int]
    output_folder: str

    @classmethod
    def from_env(cls) -> "TelegramBotConfig":
        return cls(
            enabled=_env_bool("TELEGRAM_BOT_ENABLED", False),
            token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
            allowed_user_ids=_parse_allowed_user_ids(os.getenv("TELEGRAM_ALLOWED_USER_IDS", "")),
            output_folder=os.getenv("TELEGRAM_OUTPUT_FOLDER", deps.default_folder_name()).strip(),
        )


@dataclass
class TelegramProgressState:
    task_id: str
    total: int
    completed: int = 0
    success: int = 0
    skipped: int = 0
    no_subtitle: int = 0
    errors: int = 0
    current: str = ""
    error_lines: list[str] | None = None
    done: bool = False

    def apply(self, event: str, data: dict[str, Any]):
        if event == "start":
            self.total = int(data.get("total", self.total) or self.total)
            self.current = "任务已开始"
        elif event == "processing":
            title = data.get("title") or data.get("bvid") or ""
            step = data.get("step") or data.get("message") or ""
            self.current = f"{title} - {step}".strip(" -")
        elif event == "completed":
            self.completed += 1
            if data.get("status") == "no_subtitle":
                self.no_subtitle += 1
            else:
                self.success += 1
            self.current = f"完成: {data.get('title') or data.get('bvid') or ''}".strip()
        elif event == "skip":
            self.completed += 1
            self.skipped += 1
            self.current = f"跳过: {data.get('title') or data.get('bvid') or ''}".strip()
        elif event == "error":
            self.completed += 1
            self.errors += 1
            title = data.get("title") or data.get("bvid") or "视频"
            message = data.get("message") or "处理失败"
            line = f"{title}: {message}"
            if self.error_lines is None:
                self.error_lines = []
            self.error_lines.append(line[:180])
            self.error_lines = self.error_lines[-5:]
            self.current = f"失败: {title}"
        elif event == "done":
            self.done = True
            self.total = int(data.get("total", self.total) or self.total)
            self.success = int(data.get("success", self.success) or 0)
            self.skipped = int(data.get("skipped", self.skipped) or 0)
            self.no_subtitle = int(data.get("no_subtitle", self.no_subtitle) or 0)
            self.errors = int(data.get("errors", self.errors) or 0)
            self.completed = self.success + self.skipped + self.no_subtitle + self.errors
            self.current = "任务完成"

    def render(self) -> str:
        status = "已完成" if self.done else "处理中"
        percent = 0 if self.total <= 0 else min(100, round(self.completed / self.total * 100))
        lines = [
            f"BiliSummary 任务{status}",
            f"任务 ID: {self.task_id}",
            f"进度: {self.completed}/{self.total} ({percent}%)",
            f"成功: {self.success} | 跳过: {self.skipped} | 无字幕: {self.no_subtitle} | 失败: {self.errors}",
        ]
        if self.current:
            lines.append(f"当前: {self.current}")
        if self.error_lines:
            lines.append("")
            lines.append("最近错误:")
            lines.extend(f"- {line}" for line in self.error_lines)
        return "\n".join(lines)[:3900]


class TelegramBotService:
    def __init__(self):
        self._config = TelegramBotConfig.from_env()
        self._session: aiohttp.ClientSession | None = None
        self._poll_task: asyncio.Task | None = None
        self._summary_tasks: set[asyncio.Task] = set()
        self._offset = 0
        self._last_error = ""
        self._lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        return bool(self._poll_task and not self._poll_task.done())

    @property
    def config(self) -> TelegramBotConfig:
        return self._config

    @property
    def last_error(self) -> str:
        return self._last_error

    async def reload_from_env(self):
        async with self._lock:
            config = TelegramBotConfig.from_env()
            await self._stop_locked(cancel_summary_tasks=False)
            self._config = config
            self._last_error = ""
            if config.enabled and not config.token:
                self._last_error = "已启用 Telegram Bot，但未配置 Bot Token"
                return
            if config.enabled and config.token:
                if not self._session or self._session.closed:
                    self._session = aiohttp.ClientSession(
                        timeout=aiohttp.ClientTimeout(total=40, connect=10)
                    )
                try:
                    await self._api("getMe", {}, token=config.token)
                except Exception as exc:
                    self._last_error = f"Telegram Bot 启动失败: {exc}"
                    if self._session and not self._session.closed and not self._summary_tasks:
                        await self._session.close()
                        self._session = None
                    return
                self._poll_task = asyncio.create_task(self._poll_updates(), name="telegram-bot-poll")

    async def shutdown(self):
        async with self._lock:
            await self._stop_locked(cancel_summary_tasks=True)

    async def _stop_locked(self, cancel_summary_tasks: bool):
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        self._poll_task = None

        if cancel_summary_tasks:
            for task in list(self._summary_tasks):
                task.cancel()
            if self._summary_tasks:
                await asyncio.gather(*self._summary_tasks, return_exceptions=True)
            self._summary_tasks.clear()

        if self._session and not self._session.closed and (cancel_summary_tasks or not self._summary_tasks):
            await self._session.close()
            self._session = None

    async def _api(self, method: str, payload: dict[str, Any], token: str | None = None) -> dict[str, Any]:
        if not self._session:
            raise RuntimeError("Telegram Bot 未启动")
        url = f"{BOT_API_BASE}{token or self._config.token}/{method}"
        async with self._session.post(url, json=payload) as response:
            data = await response.json(content_type=None)
            if not data.get("ok"):
                description = data.get("description") or f"HTTP {response.status}"
                raise RuntimeError(description)
            return data

    async def _poll_updates(self):
        while True:
            try:
                data = await self._api("getUpdates", {
                    "offset": self._offset,
                    "timeout": 25,
                    "allowed_updates": ["message"],
                })
                self._last_error = ""
                for update in data.get("result", []):
                    self._offset = max(self._offset, int(update.get("update_id", 0)) + 1)
                    await self._handle_update(update)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = f"Telegram Bot 轮询失败: {exc}"
                print(self._last_error)
                await asyncio.sleep(5)

    async def _handle_update(self, update: dict[str, Any]):
        message = update.get("message") or {}
        text = message.get("text") or message.get("caption") or ""
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        chat_id = chat.get("id")
        user_id = sender.get("id")
        if not chat_id or sender.get("is_bot"):
            return

        if self._config.allowed_user_ids and user_id not in self._config.allowed_user_ids:
            await self._send_message(
                chat_id,
                f"当前 Telegram 用户 ID: {user_id}\n未在允许列表中，请在设置中加入该 ID 后再发送视频链接。",
                reply_to_message_id=message.get("message_id"),
            )
            return

        stripped = text.strip()
        if stripped in {"/start", "/help", "help", "帮助"}:
            await self._send_guide(chat_id, message.get("message_id"), user_id)
            return

        bvids = await self._extract_bvids(stripped)
        if not bvids:
            await self._send_guide(chat_id, message.get("message_id"), user_id)
            return

        bvids = bvids[:MAX_BVIDS_PER_MESSAGE]
        task_id = f"telegram-{int(time.time() * 1000)}"
        model = deps.DEFAULT_MODEL
        try:
            output_subdir, folder_name = deps.resolve_folder_output_subdir(self._config.output_folder)
        except ValueError:
            output_subdir = deps.default_folder_output_subdir()
            folder_name = deps.default_folder_name()

        deps.register_task(
            task_id,
            "telegram",
            "Telegram 视频总结",
            total=len(bvids),
            meta={
                "chat_id": chat_id,
                "user_id": user_id,
                "folder": folder_name,
                "bvids": bvids,
            },
        )

        sent = await self._send_message(
            chat_id,
            f"已创建 BiliSummary 任务\n任务 ID: {task_id}\n视频数量: {len(bvids)}\n状态: 排队中",
            reply_to_message_id=message.get("message_id"),
        )
        reply_message_id = sent.get("result", {}).get("message_id")
        if not reply_message_id:
            return

        task = asyncio.create_task(
            self._run_summary_task(
                task_id,
                bvids,
                model,
                output_subdir,
                chat_id,
                reply_message_id,
                self._config.token,
            ),
            name=f"telegram-summary-{task_id}",
        )
        self._summary_tasks.add(task)
        task.add_done_callback(self._summary_tasks.discard)

    async def _extract_bvids(self, text: str) -> list[str]:
        bvids = BVID_RE.findall(text)
        urls = URL_RE.findall(text)
        for url in urls[:MAX_BVIDS_PER_MESSAGE]:
            try:
                bvids.append(extract_bvid(url))
                continue
            except ValueError:
                pass
            if self._is_b23_url(url):
                resolved = await self._resolve_redirect_url(url)
                if resolved:
                    try:
                        bvids.append(extract_bvid(resolved))
                    except ValueError:
                        pass
        return _dedupe(bvids)

    def _is_b23_url(self, url: str) -> bool:
        return re.match(r"https?://(?:b23\.tv|bili2233\.cn)/", url, re.IGNORECASE) is not None

    async def _resolve_redirect_url(self, url: str) -> str:
        if not self._session:
            return ""
        try:
            async with self._session.get(url, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=10)) as response:
                return str(response.url)
        except Exception:
            return ""

    async def _run_summary_task(
        self,
        task_id: str,
        bvids: list[str],
        model: str,
        output_subdir: str,
        chat_id: int,
        message_id: int,
        token: str,
    ):
        queue = deps.subscribe_progress(task_id)
        batch_task = asyncio.create_task(
            deps.run_batch(
                bvids,
                model,
                deps.get_task_concurrency(),
                output_subdir,
                task_id,
                modules=deps.DEFAULT_GENERATION_MODULES,
            )
        )
        state = TelegramProgressState(task_id=task_id, total=len(bvids))
        last_text = ""
        last_edit_at = 0.0

        try:
            while True:
                item = await queue.get()
                event = item.get("event")
                data = item.get("data") or {}
                state.apply(event, data)
                text = state.render()
                now = time.monotonic()
                should_edit = event in {"done", "error", "completed", "skip"} or now - last_edit_at >= EDIT_INTERVAL_SEC
                if text != last_text and should_edit:
                    await self._edit_message(chat_id, message_id, text, token)
                    last_text = text
                    last_edit_at = now
                if event == "done":
                    break
        except asyncio.CancelledError:
            batch_task.cancel()
            raise
        finally:
            deps.unsubscribe_progress(task_id, queue)
            await asyncio.gather(batch_task, return_exceptions=True)

    async def _send_guide(self, chat_id: int, reply_to_message_id: int | None, user_id: int | None):
        guide = "\n".join([
            "BiliSummary 使用指南",
            "",
            "发送一条或多条包含 BV 号的 Bilibili 视频链接，每行一个或直接粘贴在同一条消息中。",
            "",
            "示例:",
            "https://www.bilibili.com/video/BV1xx411c7mD",
            "https://www.bilibili.com/video/BV1yy411c7mD",
            "",
            f"当前 Telegram 用户 ID: {user_id}",
        ])
        await self._send_message(chat_id, guide, reply_to_message_id=reply_to_message_id)

    async def _send_message(
        self,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id
            payload["allow_sending_without_reply"] = True
        return await self._api("sendMessage", payload)

    async def _edit_message(self, chat_id: int, message_id: int, text: str, token: str | None = None):
        try:
            await self._api("editMessageText", {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "disable_web_page_preview": True,
            }, token=token)
        except Exception as exc:
            if "message is not modified" not in str(exc).lower():
                print(f"Telegram Bot 更新消息失败: {exc}")


telegram_bot_service = TelegramBotService()
