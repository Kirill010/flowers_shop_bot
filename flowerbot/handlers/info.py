"""Доставка и оплата, самовывоз, «Мы на карте»."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from .. import keyboards as kb, texts
from ..callbacks import Info
from ..ctx import Ctx
from ..ui import msg_of, safe_edit

router = Router(name="info")


@router.message(F.text == texts.BTN_DELIVERY)
async def open_info(message: Message) -> None:
    await message.answer(texts.info_menu(), reply_markup=kb.info_menu())


@router.callback_query(Info.filter())
async def info_page(call: CallbackQuery, callback_data: Info, ctx: Ctx) -> None:
    await call.answer()
    message = msg_of(call)
    if message is None:
        return
    shop = ctx.shop
    pages = {
        "delivery": (texts.delivery_text(shop), kb.info_back()),
        "payment": (texts.payment_text(shop, ctx.online_enabled), kb.info_back()),
        "pickup": (texts.pickup_text(shop), kb.info_back()),
    }
    text, markup = pages.get(callback_data.act, (texts.info_menu(), kb.info_menu()))
    await safe_edit(message, text, markup)


@router.message(F.text == texts.BTN_MAP)
async def show_map(message: Message, ctx: Ctx) -> None:
    await message.answer(texts.map_text(ctx.shop), reply_markup=kb.map_buttons(ctx.shop))
