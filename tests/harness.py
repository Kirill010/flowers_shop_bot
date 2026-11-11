"""Стенд для проверки бота без интернета: поддельный Telegram.

FakeSession вместо сети записывает всё, что бот пытается отправить. Harness «пишет» боту от имени клиента
и «нажимает» кнопки так же, как настоящий Telegram: присылает обновления в диспетчер.
"""
from __future__ import annotations

import io
import itertools
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.methods import (AnswerCallbackQuery, DeleteMessage, DeleteWebhook, EditMessageCaption, EditMessageMedia,
                             EditMessageReplyMarkup, EditMessageText, GetFile, GetMe, SendDocument, SendMessage, SendPhoto,
                             TelegramMethod)
from aiogram.types import Chat, File, InlineKeyboardMarkup, Message, PhotoSize, Update, User

from flowerbot import catalog
from flowerbot.app import build_dispatcher
from flowerbot.config import ROOT
from flowerbot.ctx import Ctx
from flowerbot.db import Database
from flowerbot.middlewares import Throttle
from flowerbot.yookassa import PaymentInfo, YooKassaClient

from .helpers import ADMIN, CUSTOMER, NOW, VALID_TOKEN, detach_routers, make_settings, real_shop


class FakeSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod] = []
        self.failures: dict[type, Exception] = {}
        self._ids = itertools.count(1000)

    async def close(self) -> None:
        pass

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        yield b"\xff\xd8fake-jpeg-bytes"

    async def make_request(self, bot: Bot, method: TelegramMethod, timeout: int | None = None):
        self.calls.append(method)
        if failure := self.failures.pop(type(method), None):
            raise failure
        if isinstance(method, GetMe):
            return User(id=999, is_bot=True, first_name="Bot", username="test_bot")
        if isinstance(method, GetFile):
            return File(file_id=method.file_id, file_unique_id="u", file_path="photos/x.jpg")
        if isinstance(method, (SendMessage, SendPhoto, SendDocument, EditMessageText, EditMessageReplyMarkup)):
            chat_id = getattr(method, "chat_id", 0) or 0
            extra: dict[str, Any] = {}
            if isinstance(method, SendPhoto):
                extra["photo"] = [PhotoSize(file_id="CACHED_FILE_ID", file_unique_id="u", width=10, height=10)]
            return Message(message_id=next(self._ids), date=datetime.now(timezone.utc),
                           chat=Chat(id=int(chat_id) if str(chat_id).lstrip("-").isdigit() else 0, type="private"), **extra)
        return True


class FakeYooKassa(YooKassaClient):
    """ЮKassa без сети: помнит созданные платежи; статусом управляет тест."""

    def __init__(self) -> None:
        super().__init__("123456", "test_" + "A" * 30)
        self.created: list[dict] = []
        self.status: dict[str, str] = {}
        self.meta: dict[str, tuple[Decimal, dict]] = {}
        self.fail = False

    async def create_payment(self, *, amount, description, return_url, metadata, idempotence_key, customer_phone="",
                             items=None):
        from flowerbot.yookassa import YooKassaError
        if self.fail:
            raise YooKassaError("нет связи")
        pid = f"pay-{len(self.created) + 1:04d}-aaaaaaaaaa"
        self.created.append(dict(amount=amount, description=description, return_url=return_url, metadata=metadata,
                                 key=idempotence_key, phone=customer_phone, items=items))
        self.status[pid] = "pending"
        self.meta[pid] = (Decimal(amount), {k: str(v) for k, v in metadata.items()})
        return PaymentInfo(pid, "pending", False, Decimal(amount), f"https://pay.example/{pid}", self.meta[pid][1])

    async def get_payment(self, payment_id):
        status = self.status[payment_id]
        amount, meta = self.meta[payment_id]
        return PaymentInfo(payment_id, status, status == "succeeded", amount, None, meta)


class Harness:
    def __init__(self, *, admin_ids: tuple[int, ...] = (ADMIN,), yookassa: bool = False, throttle: Throttle | None = None,
                 orders_chat_id: int | None = None, now: datetime = NOW, shop=None) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        for name in ("shop.toml", "catalog.toml"):  # настоящие файлы магазина нужны для /reload
            (root / name).write_bytes((ROOT / name).read_bytes())
        env = {"ADMIN_IDS": ",".join(map(str, admin_ids))}
        if orders_chat_id:
            env["ORDERS_CHAT_ID"] = str(orders_chat_id)
        settings = make_settings(root, **env)
        self.session = FakeSession()
        self.bot = Bot(VALID_TOKEN, session=self.session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        self.now = now
        self.db = Database(":memory:", clock=lambda: self.now)
        catalog.sync_catalog(self.db, catalog.load_catalog_file(ROOT / "catalog.toml"), root / "photos")
        self.yk = FakeYooKassa() if yookassa else None
        self.ctx = Ctx(settings, self.db, shop or real_shop(), self.yk, bot_username="test_bot")
        detach_routers()
        self.dp = build_dispatcher(self.ctx, throttle=throttle or Throttle(limit=10 ** 6))
        self._update_ids = itertools.count(1)
        self._message_ids = itertools.count(1)

    def close(self) -> None:
        self.tmp.cleanup()

    # ───── что делает «Telegram» ─────
    @staticmethod
    def user(uid: int, name: str = "Иван", username: str | None = "ivan") -> dict:
        data = {"id": uid, "is_bot": False, "first_name": name}
        if username:
            data["username"] = username
        return data

    async def feed(self, **fields) -> list[TelegramMethod]:
        start = len(self.session.calls)
        update = Update.model_validate({"update_id": next(self._update_ids), **fields}, context={"bot": self.bot})
        await self.dp.feed_update(self.bot, update)
        return self.session.calls[start:]

    async def say(self, uid: int, text: str, *, name: str = "Иван", username: str | None = "ivan"):
        message: dict[str, Any] = {"message_id": next(self._message_ids), "date": int(self.now.timestamp()),
                                   "chat": {"id": uid, "type": "private"}, "from": self.user(uid, name, username), "text": text}
        if text.startswith("/"):
            message["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
        return await self.feed(message=message)

    async def contact(self, uid: int, phone: str, *, contact_user_id: int | None = None, first_name: str = "Иван",
                       last_name: str | None = None):
        """Человек нажал «Поделиться номером»: Telegram присылает карточку контакта (с именем из его профиля)."""
        card = {"phone_number": phone, "first_name": first_name,
                "user_id": uid if contact_user_id is None else contact_user_id}
        if last_name:
            card["last_name"] = last_name
        return await self.feed(message={
            "message_id": next(self._message_ids), "date": int(self.now.timestamp()), "chat": {"id": uid, "type": "private"},
            "from": self.user(uid), "contact": card})

    async def photo(self, uid: int, caption: str | None = None, *, media_group_id: str | None = None):
        """Человек прислал фото (с подписью или без; media_group_id — если это часть альбома)."""
        message = {"message_id": next(self._message_ids), "date": int(self.now.timestamp()),
                   "chat": {"id": uid, "type": "private"}, "from": self.user(uid),
                   "photo": [{"file_id": "PHOTO1", "file_unique_id": "u1", "width": 100, "height": 100}]}
        if caption is not None:
            message["caption"] = caption
        if media_group_id:
            message["media_group_id"] = media_group_id
        return await self.feed(message=message)

    async def other(self, uid: int, **content):
        """Любое другое сообщение: voice={...}, sticker={...}, document={...}."""
        return await self.feed(message={"message_id": next(self._message_ids), "date": int(self.now.timestamp()),
                                        "chat": {"id": uid, "type": "private"}, "from": self.user(uid), **content})

    async def tap(self, uid: int, data: str, *, name: str = "Иван", on_photo: bool = False):
        """Нажатие на кнопку. on_photo=True — кнопка под фото-карточкой (а не под текстовым сообщением)."""
        message = {"message_id": next(self._message_ids), "date": int(self.now.timestamp()),
                   "chat": {"id": uid, "type": "private"}, "from": {"id": 999, "is_bot": True, "first_name": "Bot"}}
        if on_photo:
            message["photo"] = [{"file_id": "CARD_PHOTO", "file_unique_id": "u", "width": 10, "height": 10}]
        else:
            message["text"] = "кнопки"
        return await self.feed(callback_query={"id": str(next(self._update_ids)), "from": self.user(uid, name),
                                               "chat_instance": "ci", "data": data, "message": message})

    # ───── что видит человек ─────
    def buttons(self, calls: list | None = None) -> list:
        """Кнопки из отправленных сообщений, новые — первыми."""
        found = []
        for call in reversed(calls if calls is not None else self.session.calls):
            markup = getattr(call, "reply_markup", None)
            if isinstance(markup, InlineKeyboardMarkup):
                for row in markup.inline_keyboard:
                    found.extend(row)
        return found

    def reply_labels(self) -> list[str]:
        for call in reversed(self.session.calls):
            markup = getattr(call, "reply_markup", None)
            if markup is not None and hasattr(markup, "keyboard"):
                return [b.text for row in markup.keyboard for b in row]
        return []

    async def press(self, uid: int, label: str, *, name: str = "Иван", prefix: bool = False, on_photo: bool = False):
        """Нажимает кнопку с такой подписью (prefix=True — достаточно начала подписи). Берёт самую свежую."""
        for button in self.buttons():
            if (button.text.startswith(label) if prefix else button.text == label) and button.callback_data:
                return await self.tap(uid, button.callback_data, name=name, on_photo=on_photo)
        have = [b.text for b in self.buttons()][:30]
        raise AssertionError(f"Кнопки {label!r} нет. Сейчас есть: {have}")

    def has_button(self, label: str, calls: list | None = None, *, prefix: bool = False) -> bool:
        return any((b.text.startswith(label) if prefix else b.text == label) for b in self.buttons(calls))

    def find(self, data_prefix: str, calls: list | None = None) -> list:
        """Кнопки, у которых данные начинаются так (например «p:open:» — все товары в списке)."""
        return [b for b in self.buttons(calls) if b.callback_data and b.callback_data.startswith(data_prefix)]

    def last_markup(self, calls: list):
        """Кнопки последнего сообщения (или правки) в этих вызовах — то, что человек видит на экране сейчас."""
        for call in reversed(calls):
            markup = getattr(call, "reply_markup", None)
            if isinstance(markup, InlineKeyboardMarkup):
                return markup
        return None

    def screen_labels(self, calls: list) -> list[str]:
        """Подписи кнопок последнего экрана, сверху вниз."""
        markup = self.last_markup(calls)
        return [b.text for row in markup.inline_keyboard for b in row] if markup else []

    @staticmethod
    def texts(calls: list) -> list[str]:
        out = []
        for call in calls:
            text = getattr(call, "text", None) or getattr(call, "caption", None)
            if isinstance(call, EditMessageMedia):
                text = getattr(call.media, "caption", None)
            if text and isinstance(call, (SendMessage, SendPhoto, SendDocument, EditMessageText, EditMessageCaption,
                                          EditMessageMedia)):
                out.append(text)
        return out

    def last_text(self, calls: list) -> str:
        texts = self.texts(calls)
        assert texts, "бот ничего не написал"
        return texts[-1]

    def sent_to(self, chat_id: int, calls: list) -> list[str]:
        return [t for c, t in ((c, getattr(c, "text", None) or getattr(c, "caption", None)) for c in calls)
                if isinstance(c, (SendMessage, SendPhoto, SendDocument)) and c.chat_id == chat_id and t]

    @staticmethod
    def alert(calls: list) -> AnswerCallbackQuery | None:
        for call in reversed(calls):
            if isinstance(call, AnswerCallbackQuery) and call.text:
                return call
        return None

    def count(self, kind: type, calls: list | None = None) -> int:
        return sum(isinstance(c, kind) for c in (calls if calls is not None else self.session.calls))
