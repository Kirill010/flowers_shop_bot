"""Мелкие помощники для обработчиков: безопасное редактирование сообщений, карточки с фото."""
from __future__ import annotations

import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardMarkup, Message, User

from . import catalog
from .catalog import Product
from .ctx import Ctx

log = logging.getLogger(__name__)
CAPTION_LIMIT = 1000  # у Telegram предел 1024 знака для подписи к фото


def msg_of(call: CallbackQuery) -> Message | None:
    """Сообщение под кнопкой (если оно ещё доступно: очень старые Telegram уже не отдаёт)."""
    return call.message if isinstance(call.message, Message) else None


async def safe_edit(message: Message, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    """Меняет сообщение на месте. Если нельзя (это фото, сообщение старое) — присылает новое."""
    try:
        await message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc):
            return
        await message.answer(text, reply_markup=markup)


def full_name(user: User) -> str:
    return " ".join(x for x in (user.first_name, user.last_name) if x) or "Клиент"


async def send_product(target: Message, ctx: Ctx, product: Product, text: str,
                       markup: InlineKeyboardMarkup | None) -> None:
    """Карточка товара: фото с подписью, если фото есть; иначе просто текст."""
    photo: str | FSInputFile | None = product.photo_file_id
    if photo is None and product.photo:
        folder = ctx.settings.photos_dir.resolve()
        path = (folder / product.photo).resolve()
        if path.is_file() and folder in path.parents:
            photo = FSInputFile(path)
    if photo is not None:
        fits = len(text) <= CAPTION_LIMIT
        try:
            sent = await target.answer_photo(photo, caption=text if fits else None, reply_markup=markup if fits else None)
            if isinstance(photo, FSInputFile) and sent.photo:
                catalog.set_photo_file_id(ctx.db, product.sku, sent.photo[-1].file_id)
            if not fits:
                await target.answer(text, reply_markup=markup)
            return
        except TelegramBadRequest as exc:
            log.warning("Фото товара %s не отправилось (%s) — показываю без фото", product.sku, exc)
            catalog.set_photo_file_id(ctx.db, product.sku, None)
    await target.answer(text, reply_markup=markup)
