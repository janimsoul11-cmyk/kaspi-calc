"""Локальные заглушки внешних сервисов для сквозного теста.

Один aiohttp-сервер изображает сразу три API:
- /bot{token}/{method}, /file/bot{token}/{path} — Telegram Bot API;
- /stt/audio/transcriptions — распознавание речи (формат OpenAI/Groq);
- /anthropic/v1/messages — Claude с заранее прописанным поведением.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from aiohttp import web

BOT_USER = {"id": 999, "is_bot": True, "first_name": "Ассистент", "username": "test_assistant_bot"}


class FakeServices:
    def __init__(self, timezone: str = "Asia/Almaty"):
        self.tz = ZoneInfo(timezone)
        self.updates: list[dict] = []
        self.next_update_id = 1
        self.next_message_id = 1000
        self.calls: list[dict] = []          # все вызовы Bot API: {"method", "params", "files"}
        self.files: dict[str, bytes] = {}    # file_id -> содержимое
        self.stt_requests: list[dict] = []
        self.claude_requests: list[dict] = []
        self.tts_requests: list[dict] = []
        self.transcript = "Напомни мне проверить тест"
        self._new_update = asyncio.Event()
        self._runner: web.AppRunner | None = None
        self.base = ""

    # ---------- жизненный цикл ----------
    async def start(self) -> str:
        app = web.Application(client_max_size=50 * 1024 * 1024)
        app.router.add_post("/bot{token}/{method}", self._telegram)
        app.router.add_get("/file/bot{token}/{path:.*}", self._telegram_file)
        app.router.add_post("/stt/audio/transcriptions", self._stt)
        app.router.add_post("/anthropic/v1/messages", self._claude)
        app.router.add_post("/openai/audio/speech", self._openai_speech)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.base = f"http://127.0.0.1:{port}"
        return self.base

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    # ---------- помощники для теста ----------
    def push(self, update: dict) -> None:
        update["update_id"] = self.next_update_id
        self.next_update_id += 1
        self.updates.append(update)
        self._new_update.set()

    def message(self, from_id: int = 42, text: str | None = None, **extra) -> dict:
        self.next_message_id += 1
        msg = {
            "message_id": self.next_message_id,
            "date": int(time.time()),
            "chat": {"id": from_id, "type": "private", "first_name": "Тест"},
            "from": {"id": from_id, "is_bot": False, "first_name": "Тест"},
            **extra,
        }
        if text is not None:
            msg["text"] = text
            if text.startswith("/"):
                msg["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
        return {"message": msg}

    @staticmethod
    def file_of(call: dict, param: str) -> dict:
        """aiogram передаёт файл как param='attach://<поле>' + multipart-поле с этим именем."""
        ref = call["params"].get(param, "")
        return call["files"][ref[len("attach://"):]] if ref.startswith("attach://") else call["files"][param]

    def sent(self, method: str, chat_id: int | None = None) -> list[dict]:
        return [c for c in self.calls if c["method"] == method
                and (chat_id is None or str(c["params"].get("chat_id")) == str(chat_id))]

    async def wait_for(self, predicate, timeout: float = 15.0, what: str = "условие"):
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            result = predicate()
            if result:
                return result
            await asyncio.sleep(0.05)
        raise AssertionError(f"Не дождались: {what}. Вызовы Bot API: {[c['method'] for c in self.calls]}")

    # ---------- Telegram ----------
    def _message_result(self, chat_id, **fields) -> dict:
        self.next_message_id += 1
        return {"message_id": self.next_message_id, "date": int(time.time()),
                "chat": {"id": int(chat_id), "type": "private"}, "from": BOT_USER, **fields}

    async def _telegram(self, request: web.Request) -> web.Response:
        method = request.match_info["method"]
        params, files = {}, {}
        if request.content_type in ("multipart/form-data", "application/x-www-form-urlencoded"):
            for key, value in (await request.post()).items():
                if hasattr(value, "file"):
                    files[key] = {"filename": value.filename, "data": value.file.read()}
                else:
                    params[key] = value
        elif request.can_read_body:
            params = await request.json()

        if method == "getUpdates":
            offset = int(params.get("offset") or 0)
            self.updates = [u for u in self.updates if u["update_id"] >= offset]
            if not self.updates:
                self._new_update.clear()
                try:
                    await asyncio.wait_for(self._new_update.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
            return web.json_response({"ok": True, "result": list(self.updates)})

        self.calls.append({"method": method, "params": params, "files": files})
        if method == "getMe":
            result = BOT_USER
        elif method == "getFile":
            file_id = params["file_id"]
            result = {"file_id": file_id, "file_unique_id": f"u{file_id}",
                      "file_size": len(self.files.get(file_id, b"")), "file_path": f"files/{file_id}"}
        elif method == "sendMessage":
            result = self._message_result(params["chat_id"], text=params.get("text", ""))
        elif method in ("sendVoice", "sendDocument", "editMessageReplyMarkup"):
            result = self._message_result(params.get("chat_id", 42))
        else:
            result = True
        return web.json_response({"ok": True, "result": result})

    async def _telegram_file(self, request: web.Request) -> web.Response:
        file_id = request.match_info["path"].rsplit("/", 1)[-1]
        if file_id not in self.files:
            return web.Response(status=404)
        return web.Response(body=self.files[file_id])

    # ---------- STT ----------
    async def _stt(self, request: web.Request) -> web.Response:
        record: dict = {"auth": request.headers.get("Authorization")}
        for key, value in (await request.post()).items():
            if hasattr(value, "file"):
                record[key] = {"filename": value.filename, "size": len(value.file.read())}
            else:
                record[key] = value
        self.stt_requests.append(record)
        return web.json_response({"text": self.transcript})

    # ---------- OpenAI TTS ----------
    async def _openai_speech(self, request: web.Request) -> web.Response:
        self.tts_requests.append({"auth": request.headers.get("Authorization"), "body": await request.json()})
        return web.Response(body=b"ID3-fake-openai-mp3", content_type="audio/mpeg")

    # ---------- Claude ----------
    async def _claude(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.claude_requests.append({"headers": dict(request.headers), "body": body})
        return web.json_response(self._claude_reply(body))

    def _claude_reply(self, body: dict) -> dict:
        last = body["messages"][-1]["content"]
        blocks = last if isinstance(last, list) else [{"type": "text", "text": last}]
        results = [b for b in blocks if b.get("type") == "tool_result"]
        if results:
            return _message([_text("✅ " + "; ".join(str(r["content"]) for r in results))], "end_turn")

        user_text = [b["text"] for b in blocks if b.get("type") == "text"][-1]
        if m := re.match(r"\[Ночная обработка: итог дня (\d{4}-\d{2}-\d{2})\]", user_text):
            return _tool("save_summary", {"period": "day", "period_key": m.group(1),
                                          "content": "Итог дня: проверяли бота."})
        if user_text.startswith("[Автозапуск") or user_text.startswith("[Запрос"):
            return _message([_text("<b>План дня</b>\n• Главная задача: тест")], "end_turn")
        lowered = user_text.lower()
        if "напомни" in lowered:
            at = datetime.now(self.tz).strftime("%Y-%m-%d %H:%M")
            return _tool("add_reminder", {"text": "Проверить тест", "at": at})
        if "задач" in lowered:
            return _tool("add_task", {"title": "Протестировать бота", "area": "work", "priority": 1})
        return _message([_text(f"Принял: {user_text[:60]}")], "end_turn")


def _text(text: str) -> dict:
    return {"type": "text", "text": text}


def _tool(name: str, tool_input: dict) -> dict:
    return _message([
        {"type": "thinking", "thinking": "", "signature": "fake-signature"},
        {"type": "tool_use", "id": f"toolu_{name}_{int(time.time() * 1000)}", "name": name, "input": tool_input},
    ], "tool_use")


def _message(content: list[dict], stop_reason: str) -> dict:
    return {
        "id": f"msg_{int(time.time() * 1000)}", "type": "message", "role": "assistant",
        "model": "claude-opus-5-5", "content": content, "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 2000, "output_tokens": 150, "cache_read_input_tokens": 1500,
                  "cache_creation_input_tokens": 0},
    }


def dump(fake: FakeServices) -> str:
    """Для отладки упавшего теста."""
    return json.dumps([{"method": c["method"], "params": c["params"]} for c in fake.calls],
                      ensure_ascii=False, indent=1)[:5000]
