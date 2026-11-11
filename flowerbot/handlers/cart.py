"""Корзина."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from .. import catalog, keyboards as kb, texts
from ..callbacks import Cart
from ..ctx import Ctx
from ..ui import msg_of, safe_edit, show_screen
from . import checkout, manager

router = Router(name="cart")


def render(ctx: Ctx, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    lines = catalog.cart_lines(ctx.db, user_id)
    return texts.cart_text(lines, ctx.shop, ctx.local_now().month), kb.cart(lines)


@router.message(F.text == texts.BTN_CART)
@router.message(Command("cart"))
async def show_cart(message: Message, ctx: Ctx) -> None:
    text, markup = render(ctx, message.from_user.id)
    await message.answer(text, reply_markup=markup)


@router.callback_query(Cart.filter())
async def cart_action(call: CallbackQuery, callback_data: Cart, ctx: Ctx, state: FSMContext, bot: Bot) -> None:
    uid, act, sku = call.from_user.id, callback_data.act, callback_data.sku
    message = msg_of(call)
    if message is None or act == "noop":
        await call.answer()
        return
    if act == "show":  # из каталога: корзина открывается на месте списка/карточки
        await call.answer()
        text, markup = render(ctx, uid)
        await show_screen(message, text, markup)
        return
    if act == "pay":
        await call.answer()
        await checkout.begin(message, call.from_user, state, ctx)
        return
    if act == "check":
        await call.answer()
        await manager.cart_availability(message, call.from_user, ctx, bot)
        return

    if act == "inc":
        if error := catalog.cart_add(ctx.db, uid, sku):
            await call.answer(error, show_alert=True)
            return
    elif act == "dec":
        catalog.cart_change(ctx.db, uid, sku, -1)
    elif act == "del":
        catalog.cart_remove(ctx.db, uid, sku)
    elif act == "clear":
        catalog.cart_clear(ctx.db, uid)
    else:
        await call.answer()
        return
    await call.answer()
    text, markup = render(ctx, uid)
    await safe_edit(message, text, markup)
