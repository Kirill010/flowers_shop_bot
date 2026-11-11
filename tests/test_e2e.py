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
        if method == "copyMessage":
            return {"message_id": next(self._ids)}
        return True

    async def download(self, request: web.Request) -> web.Response:
        return web.Response(body=b"\xff\xd8real-jpeg-bytes")

    def sent_to(self, chat_id: int, method: str = "sendMessage") -> list[dict]:
        return [d for m, d in self.calls if m == method and str(d.get("chat_id")) == str(chat_id)]

    def api_calls(self) -> list[tuple[str, dict]]:
        """Всё, что сделал бот, кроме постоянного опроса «есть новости?» (getUpdates)."""
        return [(m, d) for m, d in self.calls if m != "getUpdates"]


def update_with_text(uid: int, text: str, update_id: int = 1) -> dict:
    message = {"message_id": update_id, "date": int(time.time()), "chat": {"id": uid, "type": "private"},
               "from": {"id": uid, "is_bot": False, "first_name": "Иван", "username": "ivan"}, "text": text}
    if text.startswith("/"):
        message["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
    return {"update_id": update_id, "message": message}


class Client:
    """Живой «клиент»: пишет боту через поддельный Telegram и нажимает кнопки, которые бот на самом деле прислал
    (читает их из настоящих запросов sendMessage/editMessageText — так проверяется и сериализация кнопок)."""

    def __init__(self, tg: FakeTelegram, uid: int) -> None:
        self.tg, self.uid, self._ids = tg, uid, itertools.count(1)

    async def _push(self, **fields) -> None:
        before = len(self.tg.api_calls())
        self.tg.pending_updates.append({"update_id": 10_000 + next(self._ids) * 10, **fields})
        deadline = time.monotonic() + 15
        while len(self.tg.api_calls()) == before:  # ждём, пока бот ответит хоть чем-то
            if time.monotonic() > deadline:
                raise AssertionError("Бот не ответил на действие клиента за 15 секунд")
            await asyncio.sleep(0.02)
        quiet_since, seen = time.monotonic(), len(self.tg.api_calls())
        while time.monotonic() - quiet_since < 0.25:  # и ждём, пока он договорит
            await asyncio.sleep(0.05)
            if len(self.tg.api_calls()) != seen:
                quiet_since, seen = time.monotonic(), len(self.tg.api_calls())

    def _user(self) -> dict:
        return {"id": self.uid, "is_bot": False, "first_name": "Иван", "username": "ivan"}

    async def say(self, text: str) -> None:
        await self._push(message={"message_id": next(self._ids), "date": int(time.time()),
                                  "chat": {"id": self.uid, "type": "private"}, "from": self._user(), "text": text})

    async def send_photo(self, caption: str) -> None:
        await self._push(message={"message_id": next(self._ids), "date": int(time.time()),
                                  "chat": {"id": self.uid, "type": "private"}, "from": self._user(), "caption": caption,
                                  "photo": [{"file_id": "PHOTO1", "file_unique_id": "u1", "width": 100, "height": 100}]})

    async def share_contact(self, phone: str, first_name: str) -> None:
        await self._push(message={"message_id": next(self._ids), "date": int(time.time()),
                                  "chat": {"id": self.uid, "type": "private"}, "from": self._user(),
                                  "contact": {"phone_number": phone, "first_name": first_name, "user_id": self.uid}})

    def buttons(self) -> list[dict]:
        """Кнопки под сообщениями бота этому клиенту — самые новые первыми."""
        found: list[dict] = []
        for method, data in reversed(self.tg.api_calls()):
            if method in ("sendMessage", "editMessageText", "editMessageReplyMarkup", "sendPhoto", "editMessageMedia") \
                    and str(data.get("chat_id")) == str(self.uid) and data.get("reply_markup"):
                markup = json.loads(data["reply_markup"])
                found += [b for row in markup.get("inline_keyboard", []) for b in row]
        return found

    def reply_keyboard(self) -> list[dict]:
        for method, data in reversed(self.tg.api_calls()):
            if method == "sendMessage" and str(data.get("chat_id")) == str(self.uid) and data.get("reply_markup"):
                markup = json.loads(data["reply_markup"])
                if "keyboard" in markup:
                    return [b for row in markup["keyboard"] for b in row]
        return []

    async def tap(self, label: str = "", *, data_prefix: str = "", on_photo: bool = False) -> None:
        button = next((b for b in self.buttons() if b.get("callback_data") and
                       (b["text"].startswith(label) if label else b["callback_data"].startswith(data_prefix))), None)
        if button is None:
            raise AssertionError(f"Бот не присылал кнопку {label or data_prefix!r}. Есть: {[b['text'] for b in self.buttons()][:20]}")
        message = {"message_id": next(self._ids), "date": int(time.time()), "chat": {"id": self.uid, "type": "private"},
                   "from": {"id": 999, "is_bot": True, "first_name": "Bot"}}
        if on_photo:
            message["photo"] = [{"file_id": "CARD", "file_unique_id": "u", "width": 10, "height": 10}]
        else:
            message["text"] = "кнопки"
        await self._push(callback_query={"id": str(next(self._ids)), "from": self._user(), "chat_instance": "ci",
                                         "data": button["callback_data"], "message": message})


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

    @contextlib.asynccontextmanager
    async def running_bot(self, **env):
        """Бот работает, пока идёт тест: можно писать ему и нажимать кнопки по ходу дела."""
        detach_routers()
        settings = make_settings(self.root, TELEGRAM_API_URL=self.base, **env)
        task = asyncio.create_task(app.run(settings))
        try:
            deadline = time.monotonic() + 15
            while not any(m == "getUpdates" for m, _ in self.tg.calls):
                if task.done():
                    task.result()
                    raise AssertionError("Бот завершился, не начав работу")
                if time.monotonic() > deadline:
                    raise AssertionError("Бот не запустился за 15 секунд")
                await asyncio.sleep(0.05)
            yield task
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

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
        self.assertEqual(len(list((self.root / "data" / "backups").glob("shop-*.db"))), 1, "при запуске сделана копия базы")

    async def test_own_description_set_in_botfather_is_not_overwritten(self):
        self.tg.descriptions["description"] = "Описание, которое владелец написал сам"
        self.tg.pending_updates.append(update_with_text(CUSTOMER, "/start"))
        await self.run_bot_until(lambda: self.tg.sent_to(CUSTOMER))
        self.assertEqual(self.tg.descriptions["description"], "Описание, которое владелец написал сам")
        self.assertNotEqual(self.tg.descriptions["short_description"], "", "а пустое краткое описание заполнено")

    async def test_catalog_list_and_buttons_survive_real_serialization(self):
        self.tg.pending_updates.append(update_with_text(CUSTOMER, "🌸 Каталог"))
        await self.run_bot_until(lambda: self.tg.sent_to(CUSTOMER))
        markup = json.loads(self.tg.sent_to(CUSTOMER)[0]["reply_markup"])
        labels = [b["text"] for row in markup["inline_keyboard"] for b in row]
        self.assertIn("• 💐 Букеты", labels)
        self.assertIn("🌱 Растения", labels)
        self.assertTrue(all(len(b["callback_data"].encode()) <= 64 for row in markup["inline_keyboard"] for b in row),
                        "данные кнопок укладываются в лимит Telegram — 64 байта")

    async def test_every_callback_data_in_every_screen_fits_telegram_limit(self):
        """Прогоняем весь каталог, корзину, оформление и панель: ни одна кнопка не должна превышать 64 байта
        (иначе Telegram отвергнет всё сообщение)."""
        h = Harness()
        self.addCleanup(h.close)
        from flowerbot import catalog
        from flowerbot.callbacks import Prod
        await h.say(CUSTOMER, "🌸 Каталог")
        for tab in ("🌱 Растения", "💐 Букеты"):
            await h.press(CUSTOMER, tab)
        for product in catalog.list_products(h.db):
            for act in ("open", "more"):
                await h.tap(CUSTOMER, Prod(act=act, sku=product.sku).pack())
        for sku in ("roses51", "chrysanthemum", "classic"):
            await h.tap(CUSTOMER, Prod(act="add", sku=sku).pack())
        await h.say(CUSTOMER, "🛒 Корзина")
        await h.press(CUSTOMER, "✅ Оформить заказ")
        await h.contact(CUSTOMER, "79175494941")
        await h.press(CUSTOMER, "🚚 Доставка")
        await h.say(CUSTOMER, "Москва, Тверская 1, кв. 5")
        await h.press(CUSTOMER, "🗓 Другая дата")
        await h.press(CUSTOMER, "Завтра, 10.11")
        await h.press(CUSTOMER, "09:00–11:00")
        await h.press(CUSTOMER, "✏️ Изменить или добавить")
        for tab in ("🌸 Каталог", "🧾 Мои заказы", "💬 Менеджер", "🚚 Доставка и оплата", "🎁 Сертификат", "⭐ Отзывы"):
            await h.say(CUSTOMER, tab)
        await h.say(ADMIN, "👑 Панель")
        for step in ("🛒 Наличие", "⬅️ В панель", "⚙️ Ещё", "📷 Фото товаров"):
            await h.press(ADMIN, step)
        too_long = [b.callback_data for b in h.buttons() if b.callback_data and len(b.callback_data.encode()) > 64]
        self.assertEqual(too_long, [])
        self.assertGreater(len(h.buttons()), 100, "прогулка действительно прошла по многим экранам")

    async def test_wrong_token_gives_human_error(self):
        settings = make_settings(self.root, BOT_TOKEN="987654321:AAEdifferentDifferentDifferentDifferent1",
                                 TELEGRAM_API_URL=self.base)
        with self.assertRaises(ConfigError) as ctx:
            await app.run(settings)
        self.assertIn("не принял BOT_TOKEN", str(ctx.exception))
        self.assertNotIsInstance(ctx.exception, app.ConnectionProblem, "неверный ключ — это настройки, повторять бесполезно")

    async def test_unreachable_telegram_gives_human_error(self):
        settings = make_settings(self.root, TELEGRAM_API_URL="http://127.0.0.1:9")
        with self.assertRaises(ConfigError) as ctx:
            await app.run(settings)
        self.assertIn("PROXY_URL", str(ctx.exception))
        self.assertIsInstance(ctx.exception, app.ConnectionProblem, "по этому start.bat поймёт: подождать и попробовать снова")


class RealClientTests(NetworkCase):
    """Живой сценарий поверх настоящего сетевого слоя aiogram: всё, что бот отправляет, проходит настоящую сериализацию."""

    async def test_new_customer_buys_a_bouquet_by_tapping_buttons_only(self):
        async with self.running_bot():
            client = Client(self.tg, CUSTOMER)
            await client.say("🌸 Каталог")
            await client.tap("Мини ·")
            await client.tap("🛒 В корзину")
            await client.tap("🛒 Корзина ·")
            self.assertEqual(len(self.tg.sent_to(CUSTOMER)), 1,
                             "от каталога до корзины — одно сообщение, всё остальное правилось на месте")
            await client.tap("✅ Оформить заказ")
            keyboard = client.reply_keyboard()
            self.assertTrue(any(b.get("request_contact") for b in keyboard), "кнопка «Поделиться номером» дошла как положено")
            await client.share_contact("79175494941", "Мария")
            self.assertEqual({b["text"] for b in client.reply_keyboard()},
                             {"🌸 Каталог", "🛒 Корзина", "🧾 Мои заказы", "💬 Менеджер", "🚚 Доставка и оплата",
                              "🎁 Сертификат", "📍 Мы на карте", "⭐ Отзывы"}, "главное меню вернулось")
            await client.tap("🛍 Самовывоз")
            await client.tap(data_prefix="co:when:")  # первая быстрая кнопка времени — какое бы сейчас ни было время
            await client.tap("🔗 Оплатить переводом")  # подходит и на сегодня, и на другой день
            await self.wait_for(lambda: any("Новый заказ" in d.get("text", "") for d in self.tg.sent_to(ADMIN)))
        mine = [d.get("text", "") for d in self.tg.sent_to(CUSTOMER)]
        self.assertTrue(any("Заказ №1 принят" in t for t in mine), mine)
        staff = next(d for d in self.tg.sent_to(ADMIN) if "Новый заказ" in d.get("text", ""))
        self.assertIn("Мария", staff["text"])
        accept = [b for row in json.loads(staff["reply_markup"])["inline_keyboard"] for b in row if b["text"] == "✅ Принять"]
        self.assertEqual(accept[0]["callback_data"], "ad:status:1:confirmed")
        methods = [m for m, _ in self.tg.api_calls()]
        self.assertGreaterEqual(methods.count("editMessageText"), 2, "карточка и корзина — правкой на месте")
        self.assertGreaterEqual(methods.count("editMessageReplyMarkup"), 1, "«в корзину» обновило только кнопки")

    async def test_customer_photo_is_copied_to_staff_over_the_network(self):
        async with self.running_bot():
            client = Client(self.tg, CUSTOMER)
            await client.send_photo("хочу такой же")
            await client.tap("✅ Отправить менеджеру")
            await self.wait_for(lambda: any(m == "copyMessage" for m, _ in self.tg.calls))
        copy = next(d for m, d in self.tg.calls if m == "copyMessage")
        self.assertEqual((str(copy["chat_id"]), str(copy["from_chat_id"])), (str(ADMIN), str(CUSTOMER)))
        self.assertTrue(str(copy["message_id"]).isdigit())
        card = next(d for d in self.tg.sent_to(ADMIN) if "Клиент прислал фото" in d.get("text", ""))
        self.assertIn("хочу такой же", card["text"])

    async def test_photo_cards_are_edited_in_place_with_real_uploads(self):
        photos = self.root / "photos"
        photos.mkdir(exist_ok=True)
        (photos / "mini.jpg").write_bytes(b"\xff\xd8mini-photo")
        (photos / "classic.jpg").write_bytes(b"\xff\xd8classic-photo")
        async with self.running_bot():
            client = Client(self.tg, CUSTOMER)
            await client.say("🌸 Каталог")
            await client.tap("Мини ·")  # текстовый список → карточка с фото: старое сообщение убирается, фото уходит файлом
            methods = [m for m, _ in self.tg.api_calls()]
            self.assertIn("deleteMessage", methods)
            self.assertIn("sendPhoto", methods)
            self.assertEqual([f[2:] for f in self.tg.files if f[0] == "sendPhoto"], [("mini.jpg", b"\xff\xd8mini-photo")])
            await client.tap("▶", on_photo=True)  # фото → фото: то же сообщение правится, новый файл уходит вместе с запросом
            edit = next(d for m, d in self.tg.api_calls() if m == "editMessageMedia")
            media = json.loads(edit["media"])
            self.assertEqual(media["type"], "photo")
            self.assertTrue(media["media"].startswith("attach://"))
            self.assertIn("Классика", media["caption"])
            self.assertEqual([f[2:] for f in self.tg.files if f[0] == "editMessageMedia"],
                             [("classic.jpg", b"\xff\xd8classic-photo")])
            self.assertEqual(json.loads(edit["reply_markup"])["inline_keyboard"][0][0]["text"], "🛒 В корзину")


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
