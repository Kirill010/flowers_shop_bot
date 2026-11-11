"""«Мои заказы»: список, статус, повторить, отменить, спросить."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import catalog, keyboards as kb, notify, orders, texts
from ..callbacks import Mo
from ..ctx import Ctx
from ..ui import msg_of, safe_edit
from ..util import esc
from . import manager
from .cart import render as render_cart

router = Router(name="my_orders")


def _list(ctx: Ctx, user_id: int) -> tuple[str, object]:
    items = orders.list_orders(ctx.db, user_id=user_id, limit=8)
    if not items:
        return "🧾 <b>Мои заказы</b>\n\nПока заказов нет. Выберите букет в каталоге — и он появится здесь 🌸", None
    return "🧾 <b>Мои заказы</b>\n\nНажмите на заказ, чтобы увидеть статус:", kb.my_orders(items)


@router.message(F.text == texts.BTN_ORDERS)
@router.message(Command("orders"))
async def show_orders(message: Message, ctx: Ctx) -> None:
    text, markup = _list(ctx, message.from_user.id)
    await message.answer(text, reply_markup=markup)


@router.callback_query(Mo.filter(F.act == "list"))
async def back_to_list(call: CallbackQuery, ctx: Ctx) -> None:
    await call.answer()
    if message := msg_of(call):
        text, markup = _list(ctx, call.from_user.id)
        await safe_edit(message, text, markup)


@router.callback_query(Mo.filter(F.act == "open"))
async def open_order(call: CallbackQuery, callback_data: Mo, ctx: Ctx) -> None:
    order = orders.get_user_order(ctx.db, callback_data.id, call.from_user.id)  # только свой заказ
    if order is None:
        await call.answer("Заказ не найден.", show_alert=True)
        return
    await call.answer()
    if message := msg_of(call):
        await safe_edit(message, texts.order_card(order, ctx.shop), kb.my_order(order))


@router.callback_query(Mo.filter(F.act == "repeat"))
async def repeat_order(call: CallbackQuery, callback_data: Mo, ctx: Ctx) -> None:
    order = orders.get_user_order(ctx.db, callback_data.id, call.from_user.id)
    if order is None:
        await call.answer("Заказ не найден.", show_alert=True)
        return
    month = ctx.local_now().month
    added, skipped = [], []
    for item in order.items:
        product = catalog.get_product(ctx.db, item.sku)
        if product is None or not product.orderable(month) or catalog.cart_add(ctx.db, call.from_user.id, item.sku, item.qty):
            skipped.append(item.name)
        else:
            added.append(f"{item.name} × {item.qty}")
    await call.answer()
    message = msg_of(call)
    if message is None:
        return
    note = ("🔁 Добавил в корзину: " + esc(", ".join(added)) if added else "Не получилось добавить товары из этого заказа.")
    if skipped:
        note += "\n⚠️ Сейчас недоступно: " + esc(", ".join(skipped))
    await message.answer(note)
    text, markup = render_cart(ctx, call.from_user.id)
    await message.answer(text, reply_markup=markup)


@router.callback_query(Mo.filter(F.act == "cancel_ask"))
async def cancel_ask(call: CallbackQuery, callback_data: Mo, ctx: Ctx) -> None:
    """Сначала спрашиваем: случайное нажатие не должно отменять заказ."""
    order = orders.get_user_order(ctx.db, callback_data.id, call.from_user.id)
    if order is None:
        await call.answer("Заказ не найден.", show_alert=True)
        return
    if order.status != "new" or order.pay_status == "paid":
        await call.answer("Этот заказ уже нельзя отменить здесь. Напишите менеджеру — поможем.", show_alert=True)
        return
    await call.answer()
    if message := msg_of(call):
        await safe_edit(message, f"Отменить заказ №{order.id}?", kb.confirm_cancel_order(order.id))


@router.callback_query(Mo.filter(F.act == "cancel"))
async def cancel_order(call: CallbackQuery, callback_data: Mo, ctx: Ctx, bot: Bot) -> None:
    order = orders.get_user_order(ctx.db, callback_data.id, call.from_user.id)
    if order is None:
        await call.answer("Заказ не найден.", show_alert=True)
        return
    if order.status != "new" or order.pay_status == "paid":
        await call.answer("Этот заказ уже нельзя отменить здесь. Напишите менеджеру — поможем.", show_alert=True)
        return
    updated = orders.set_status(ctx.db, order.id, "cancelled", by_user=call.from_user.id)
    if updated is None:
        await call.answer("Статус заказа уже изменился. Обновите список.", show_alert=True)
        return
    await call.answer("Заказ отменён")
    if message := msg_of(call):
        await safe_edit(message, texts.order_card(updated, ctx.shop), kb.my_order(updated))
    await notify.to_staff(bot, ctx, f"❌ Клиент отменил заказ №{order.id} ({esc(order.name)}).")


@router.callback_query(Mo.filter(F.act == "ask"))
async def ask_about_order(call: CallbackQuery, callback_data: Mo, state: FSMContext, ctx: Ctx) -> None:
    order = orders.get_user_order(ctx.db, callback_data.id, call.from_user.id)
    if order is None:
        await call.answer("Заказ не найден.", show_alert=True)
        return
    await call.answer()
    if message := msg_of(call):
        await manager.start_ask(message, state, ctx, "question", f"Вопрос по заказу №{order.id}")
