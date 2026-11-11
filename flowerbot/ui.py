"""Мелкие помощники для обработчиков: экраны «на месте», безопасное редактирование, фото товаров.

Главный принцип удобства: переход по кнопке меняет ТО ЖЕ сообщение, а не присылает новое. Чат не превращается в стену."""
from __future__ import annotations

import contextlib
import logging

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardMarkup, InputMediaPhoto, Message, User

from . import catalog, keyboards
from .catalog import Product
from .ctx import Ctx

log = logging.getLogger(__name__)
CAPTION_LIMIT = 1000  # у Telegram предел 1024 знака для подписи к фото


def msg_of(call: CallbackQuery) -> Message | None:
    """Сообщение под кнопкой (если оно ещё доступно: очень старые Telegram уже не отдаёт)."""
    return call.message if isinstance(call.message, Message) else None


def full_name(user: User) -> str:
    return " ".join(x for x in (user.first_name, user.last_name) if x) or "Клиент"


async def safe_edit(message: Message, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    """Меняет текст сообщения на месте. Если нельзя (это фото, сообщение старое) — присылает новое."""
    try:
        await message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc):
            return
        await message.answer(text, reply_markup=markup)


def menu_for(ctx: Ctx, user_id: int):
    """Постоянное меню внизу: у сотрудников в нём есть ещё кнопка «👑 Панель»."""
    return keyboards.main_menu(ctx.is_admin(user_id))


def photo_for(ctx: Ctx, product: Product) -> str | FSInputFile | None:
    """Чем отправлять фото: запомненным номером Telegram (быстро) или файлом из папки photos."""
    if product.photo_file_id:
        return product.photo_file_id
    if product.photo:
        folder = ctx.settings.photos_dir.resolve()
        path = (folder / product.photo).resolve()
        if path.is_file() and folder in path.parents:  # файл только из папки photos, никуда выше
            return FSInputFile(path)
    return None


def _remember(result: object, remember) -> None:
    if remember and isinstance(result, Message) and result.photo:
        remember(result.photo[-1].file_id)


async def show_screen(message: Message, text: str, markup: InlineKeyboardMarkup | None = None, *,
                      photo: str | FSInputFile | None = None, remember=None, forget=None) -> Message:
    """Показывает экран на месте нажатой кнопки.
    Текст → текст и фото → фото правятся без «мигания». Если тип не совпал (фото ↔ текст), старое сообщение
    убирается и присылается новое: Telegram не умеет превращать одно в другое."""
    caption = text if len(text) <= CAPTION_LIMIT else text[: CAPTION_LIMIT - 1] + "…"
    has_photo = bool(message.photo)
    try:
        if photo is None and not has_photo:
            await message.edit_text(text, reply_markup=markup)
            return message
        if photo is not None and has_photo:
            result = await message.edit_media(InputMediaPhoto(media=photo, caption=caption), reply_markup=markup)
            _remember(result, remember)
            return result if isinstance(result, Message) else message
    except TelegramBadRequest as exc:
        reason = str(exc).lower()
        if "message is not modified" in reason:
            return message
        if forget and ("file" in reason or "wrong" in reason):  # запомненный номер фото негоден — забываем его
            forget()
            photo = None
        log.debug("Экран не удалось поправить на месте (%s) — заменяю сообщение", exc)

    with contextlib.suppress(TelegramAPIError):  # старое (дольше 48 часов) Telegram удалить не даёт — не страшно
        await message.delete()
    if photo is not None:
        try:
            sent = await message.answer_photo(photo, caption=caption, reply_markup=markup)
            _remember(sent, remember)
            return sent
        except TelegramBadRequest as exc:
            log.warning("Фото не отправилось (%s) — показываю без фото", exc)
            if forget:
                forget()
    return await message.answer(text, reply_markup=markup)


async def show_product(message: Message, ctx: Ctx, product: Product, text: str,
                       markup: InlineKeyboardMarkup | None) -> Message:
    """Карточка товара: с фото, если оно есть; номер фото запоминается, битый — забывается."""
    return await show_screen(
        message, text, markup, photo=photo_for(ctx, product),
        remember=lambda file_id: catalog.set_photo_file_id(ctx.db, product.sku, file_id),
        forget=lambda: catalog.set_photo_file_id(ctx.db, product.sku, None))
