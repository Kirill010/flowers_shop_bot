"""Сообщения сотрудникам и клиентам от имени бота (не в ответ на их сообщение)."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, Message

from .ctx import Ctx

log = logging.getLogger(__name__)


async def _send(bot: Bot, chat_id: int, text: str, markup: InlineKeyboardMarkup | None) -> Message | None:
    for attempt in (1, 2):
        try:
            return await bot.send_message(chat_id, text, reply_markup=markup)
        except TelegramRetryAfter as exc:
            if attempt == 2:
                break
            await asyncio.sleep(min(exc.retry_after, 5))
        except TelegramAPIError as exc:
            # клиент заблокировал бота, сотрудник ни разу не нажимал /start и т. п. — это не авария
            log.warning("Не удалось отправить сообщение в чат %s: %s", chat_id, exc)
            break
    return None


async def to_user(bot: Bot, user_id: int, text: str, markup: InlineKeyboardMarkup | None = None) -> bool:
    return await _send(bot, user_id, text, markup) is not None


async def to_staff(bot: Bot, ctx: Ctx, text: str, markup: InlineKeyboardMarkup | None = None) -> int:
    """Сообщает сотрудникам: в общий чат, если он указан, иначе каждому администратору лично.
    Возвращает, скольким получателям дошло. Если общий чат недоступен — пишем администраторам."""
    delivered = 0
    chat = ctx.settings.orders_chat_id
    if chat:
        delivered += await _send(bot, chat, text, markup) is not None
    if not delivered:
        for admin_id in ctx.settings.admin_ids:
            delivered += await _send(bot, admin_id, text, markup) is not None
    if not delivered:
        log.error("Сообщение для сотрудников никому не доставлено: %s", text[:80].replace("\n", " "))
    return delivered


async def send_certificate(bot: Bot, ctx: Ctx, cert, user_id: int) -> bool:
    """Отправляет клиенту электронный сертификат (картинка + код текстом)."""
    from . import certs
    from .util import rub
    try:
        data = certs.render_png(cert, ctx.shop)
        await bot.send_document(
            user_id, BufferedInputFile(data, filename=f"certificate-{cert.code}.png"),
            caption=(f"🎁 <b>Подарочный сертификат на {rub(cert.amount)}</b>\n"
                     f"Код: <code>{cert.code}</code>\n"
                     f"Действует до {cert.expires:%d.%m.%Y}.\n\n"
                     "Перешлите эту картинку тому, кому дарите. Расплатиться можно в магазине "
                     "(назвав код) или в боте при оформлении заказа."))
        return True
    except TelegramAPIError as exc:
        log.warning("Не удалось отправить сертификат клиенту %s: %s", user_id, exc)
        return False
