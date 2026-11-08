"""Настоящий сетевой слой aiogram против поддельного сервера Telegram Bot API (поднимается на этом же компьютере).

Здесь проверяется то, что не видно в тестах с FakeSession: запуск бота целиком (`app.run`), настоящая отправка запросов
(сериализация кнопок и разметки), загрузка файла-сертификата и скачивание присланного фото.
"""
from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import logging
import tempfile
import time
import unittest
from pathlib import Path

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiohttp import web
from aiohttp.test_utils import TestServer

from flowerbot import app, certs, notify
from flowerbot.config import ConfigError, ROOT

from .harness import Harness
from .helpers import ADMIN, CUSTOMER, VALID_TOKEN, detach_routers, make_settings


class FakeTelegram:
    """Минимальный Bot API: отвечает на запросы бота и записывает всё, что бот прислал."""

    def __init__(self, token: str) -> None:
        self.token = token
        self.calls: list[tuple[str, dict]] = []
        self.files: list[tuple[str, str, str, bytes]] = []
        self.pending_updates: list[dict] = []
        self.descriptions = {"description": "", "short_description": ""}
        self._ids = itertools.count(500)

    async def handle(self, request: web.Request) -> web.Response:
        if request.match_info["token"] != self.token:
            return web.json_response({"ok": False, "error_code": 401, "description": "Unauthorized"}, status=401)
        method = request.match_info["method"]
        data: dict = {}
        if request.content_type == "multipart/form-data":  # запросы с файлами
            async for part in await request.multipart():
                payload = await part.read()
                if part.filename:
                    self.files.append((method, part.name, part.filename, payload))
                else:
                    data[part.name] = payload.decode()
        elif request.content_type == "application/x-www-form-urlencoded":  # обычные запросы без файлов
            data = {key: str(value) for key, value in (await request.post()).items()}
        elif request.can_read_body:
            with contextlib.suppress(ValueError):
                data = await request.json()
        self.calls.append((method, data))
        return web.json_response({"ok": True, "result": await self.result(method, data)})

    async def result(self, method: str, data: dict):
        if method == "getMe":
            return {"id": 999, "is_bot": True, "first_name": "Bot", "username": "test_bot"}
        if method == "getUpdates":
            updates, self.pending_updates = self.pending_updates, []
            if not updates:
                await asyncio.sleep(0.05)
            return updates
        if method == "getMyDescription":
            return {"description": self.descriptions["description"]}
        if method == "getMyShortDescription":
            return {"short_description": self.descriptions["short_description"]}
        if method == "setMyDescription":
            self.descriptions["description"] = data.get("description", "")
        if method == "setMyShortDescription":
            self.descriptions["short_description"] = data.get("short_description", "")
        if method == "getFile":
            return {"file_id": data["file_id"], "file_unique_id": "u", "file_size": 18, "file_path": "photos/file_1.jpg"}
        if method in ("sendMessage", "sendDocument", "sendPhoto"):
            message = {"message_id": next(self._ids), "date": int(time.time()),
                       "chat": {"id": int(data["chat_id"]), "type": "private"}}
            if "text" in data:
                message["text"] = data["text"]
            return message
        return True

    async def download(self, request: web.Request) -> web.Response:
        return web.Response(body=b"\xff\xd8real-jpeg-bytes")

    def sent_to(self, chat_id: int, method: str = "sendMessage") -> list[dict]:
        return [d for m, d in self.calls if m == method and str(d.get("chat_id")) == str(chat_id)]


def update_with_text(uid: int, text: str, update_id: int = 1) -> dict:
    message = {"message_id": update_id, "date": int(time.time()), "chat": {"id": uid, "type": "private"},
               "from": {"id": uid, "is_bot": False, "first_name": "Иван", "username": "ivan"}, "text": text}
    if text.startswith("/"):
        message["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
    return {"update_id": update_id, "message": message}


class NetworkCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        logging.getLogger("aiogram").setLevel(logging.CRITICAL)  # при остановке сервера aiogram шумит про обрыв связи — это ожидаемо
        self.tg = FakeTelegram(VALID_TOKEN)
        web_app = web.Application()
        web_app.router.add_post("/bot{token}/{method}", self.tg.handle)
        web_app.router.add_get("/file/bot{token}/{path:.*}", self.tg.download)
        self.server = TestServer(web_app)
        await self.server.start_server()
        self.base = str(self.server.make_url("")).rstrip("/")
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for name in ("shop.toml", "catalog.toml"):
            (self.root / name).write_bytes((ROOT / name).read_bytes())

    async def asyncTearDown(self):
        await self.server.close()
        self.tmp.cleanup()

    async def wait_for(self, condition, timeout: float = 15.0):
        deadline = time.monotonic() + timeout
        while not condition():
            if time.monotonic() > deadline:
                raise AssertionError("Не дождались ответа бота за %.0f сек" % timeout)
            await asyncio.sleep(0.05)

    async def run_bot_until(self, condition, timeout: float = 15.0, **env) -> None:
        """Запускает бота целиком (как start.bat) и ждёт нужного результата. Если бот упал — показывает настоящую ошибку."""
        detach_routers()
        settings = make_settings(self.root, TELEGRAM_API_URL=self.base, **env)
        task = asyncio.create_task(app.run(settings))
        try:
            deadline = time.monotonic() + timeout
            while not condition():
                if task.done():
                    task.result()  # если бот завершился с ошибкой — она будет поднята здесь
                    raise AssertionError("Бот завершился, не дождавшись нужного результата")
                if time.monotonic() > deadline:
                    raise AssertionError(f"Не дождались ответа бота за {timeout:.0f} сек")
                await asyncio.sleep(0.05)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


class StartupTests(NetworkCase):
    async def test_bot_starts_polls_and_greets_a_customer(self):
        self.tg.pending_updates.append(update_with_text(CUSTOMER, "/start"))
        await self.run_bot_until(lambda: self.tg.sent_to(CUSTOMER) and self.tg.sent_to(ADMIN))
        methods = [m for m, _ in self.tg.calls]
        for needed in ("getMe", "deleteWebhook", "setMyCommands", "setMyDescription", "setMyShortDescription", "getUpdates"):
            self.assertIn(needed, methods, needed)
        self.assertLess(methods.index("getMe"), methods.index("getUpdates"))
        commands = json.loads(next(d for m, d in self.tg.calls if m == "setMyCommands")["commands"])
        self.assertEqual({c["command"] for c in commands}, {"menu", "cart", "orders", "help"})

        greeting = self.tg.sent_to(CUSTOMER)[0]
        self.assertIn("MuzFlowers", greeting["text"])
        self.assertEqual(greeting["parse_mode"], "HTML")
        keyboard = json.loads(greeting["reply_markup"])["keyboard"]
        self.assertEqual([len(row) for row in keyboard], [2, 2, 2, 2], "главное меню: 8 кнопок в 4 ряда")
        self.assertIn("Бот запущен", self.tg.sent_to(ADMIN)[0]["text"], "сотрудники узнают, что бот включился")

    async def test_own_description_set_in_botfather_is_not_overwritten(self):
        self.tg.descriptions["description"] = "Описание, которое владелец написал сам"
        self.tg.pending_updates.append(update_with_text(CUSTOMER, "/start"))
        await self.run_bot_until(lambda: self.tg.sent_to(CUSTOMER))
        self.assertEqual(self.tg.descriptions["description"], "Описание, которое владелец написал сам")
        self.assertNotEqual(self.tg.descriptions["short_description"], "", "а пустое краткое описание заполнено")

    async def test_catalog_card_and_buttons_survive_real_serialization(self):
        self.tg.pending_updates.append(update_with_text(CUSTOMER, "🌸 Каталог"))
        await self.run_bot_until(lambda: self.tg.sent_to(CUSTOMER))
        markup = json.loads(self.tg.sent_to(CUSTOMER)[0]["reply_markup"])
        labels = [b["text"] for row in markup["inline_keyboard"] for b in row]
        self.assertIn("💐 Букеты", labels)
        self.assertTrue(all(len(b["callback_data"].encode()) <= 64 for row in markup["inline_keyboard"] for b in row),
                        "данные кнопок укладываются в лимит Telegram — 64 байта")

    async def test_every_callback_data_in_every_screen_fits_telegram_limit(self):
        """Прогоняем весь каталог и корзину: ни одна кнопка не должна превышать 64 байта (иначе Telegram отвергнет сообщение)."""
        h = Harness()
        self.addCleanup(h.close)
        from flowerbot.callbacks import Prod
        await h.say(CUSTOMER, "🌸 Каталог")
        for category in ("💐 Букеты", "🌱 Горшечные растения"):
            await h.press(CUSTOMER, category)
        for sku in ("classic", "musical", "orchid"):
            await h.tap(CUSTOMER, Prod(act="info", sku=sku).pack())
        await h.tap(CUSTOMER, Prod(act="add", sku="roses51").pack())
        await h.say(CUSTOMER, "🛒 Корзина")
        await h.say(ADMIN, "/admin")
        too_long = [b.callback_data for b in h.buttons() if b.callback_data and len(b.callback_data.encode()) > 64]
        self.assertEqual(too_long, [])

    async def test_wrong_token_gives_human_error(self):
        settings = make_settings(self.root, BOT_TOKEN="987654321:AAEdifferentDifferentDifferentDifferent1",
                                 TELEGRAM_API_URL=self.base)
        with self.assertRaises(ConfigError) as ctx:
            await app.run(settings)
        self.assertIn("не принял BOT_TOKEN", str(ctx.exception))

    async def test_unreachable_telegram_gives_human_error(self):
        settings = make_settings(self.root, TELEGRAM_API_URL="http://127.0.0.1:9")
        with self.assertRaises(ConfigError) as ctx:
            await app.run(settings)
        self.assertIn("PROXY_URL", str(ctx.exception))


class FilesTests(NetworkCase):
    async def test_certificate_is_uploaded_as_a_file(self):
        h = Harness()
        self.addCleanup(h.close)
        bot = Bot(VALID_TOKEN, session=AiohttpSession(api=TelegramAPIServer.from_base(self.base)),
                  default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        try:
            cert = certs.issue(h.db, h.ctx.shop, 3000, buyer_id=CUSTOMER)
            sent = await notify.send_certificate(bot, h.ctx, cert, CUSTOMER)
        finally:
            await bot.session.close()
        self.assertTrue(sent)
        method, data = self.tg.calls[-1]
        self.assertEqual(method, "sendDocument")
        self.assertIn(cert.code, data["caption"])
        ((_, field, filename, payload),) = self.tg.files
        self.assertEqual(data["document"], f"attach://{field}", "файл привязан к полю document")
        self.assertEqual(filename, f"certificate-{cert.code}.png")
        self.assertTrue(payload.startswith(b"\x89PNG"), "ушла настоящая картинка")

    async def test_admin_photo_is_downloaded_and_saved(self):
        update = update_with_text(ADMIN, "", 7)
        update["message"].pop("text")
        update["message"].pop("entities", None)
        update["message"]["caption"] = "/photo classic"
        update["message"]["photo"] = [{"file_id": "PHOTO1", "file_unique_id": "u1", "width": 100, "height": 100}]
        self.tg.pending_updates.append(update)
        await self.run_bot_until(lambda: any("сохранено" in d.get("text", "") for d in self.tg.sent_to(ADMIN)))
        saved = self.root / "photos" / "classic.jpg"
        self.assertTrue(saved.is_file())
        self.assertEqual(saved.read_bytes(), b"\xff\xd8real-jpeg-bytes")


if __name__ == "__main__":
    unittest.main()
