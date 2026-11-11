"""Каталог: список товаров → карточка. Всё происходит в одном сообщении, чат не превращается в стену карточек.

Путь клиента: «🌸 Каталог» → нажал на товар → «🛒 В корзину». Листать соседние товары — ◀ ▶ прямо в карточке."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from .. import catalog, keyboards as kb, texts
from ..callbacks import Cat, Prod
from ..catalog import CATEGORIES, Product
from ..ctx import Ctx
from ..ui import msg_of, show_product, show_screen
from ..util import rub

router = Router(name="catalog")
DEFAULT_CAT = "bouquet"


def cart_info(ctx: Ctx, user_id: int) -> tuple[int, int]:
    lines = catalog.cart_lines(ctx.db, user_id)
    return sum(line.qty for line in lines), sum(line.total for line in lines)


def list_screen(ctx: Ctx, user_id: int, cat: str, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    items = catalog.list_products(ctx.db, cat)
    pages = max(1, -(-len(items) // kb.LIST_PAGE))
    page = min(max(page, 0), pages - 1)
    count, total = cart_info(ctx, user_id)
    return texts.list_text(cat, items), kb.catalog_list(cat, items, page, ctx.local_now().month, count, total)


def card_screen(ctx: Ctx, user_id: int, product: Product, expanded: bool) -> tuple[str, InlineKeyboardMarkup]:
    month = ctx.local_now().month
    around = catalog.neighbors(ctx.db, product.sku)
    prev_p, next_p, index, total = around if around else (product, product, 1, 1)
    count, cart_total = cart_info(ctx, user_id)
    text = texts.product_details(product, month) if expanded else texts.product_card(product, month)
    return text, kb.card(product, month, expanded=expanded, prev_sku=prev_p.sku, next_sku=next_p.sku, index=index,
                         total=total, cart_count=count, cart_total=cart_total)


def is_expanded(message: Message) -> bool:
    """Раскрыта ли сейчас карточка («Подробнее»): по тому, есть ли в ней кнопка «Свернуть»."""
    markup = message.reply_markup
    return bool(markup and any(b.text.startswith("📖 Свернуть") for row in markup.inline_keyboard for b in row))


@router.message(F.text == texts.BTN_CATALOG)
@router.message(Command("catalog"))
async def open_catalog(message: Message, ctx: Ctx) -> None:
    text, markup = list_screen(ctx, message.from_user.id, DEFAULT_CAT)
    await message.answer(text, reply_markup=markup)


@router.callback_query(Cat.filter())
async def open_category(call: CallbackQuery, callback_data: Cat, ctx: Ctx) -> None:
    await call.answer()
    message = msg_of(call)
    if message is None:
        return
    cat = callback_data.cat if callback_data.cat in CATEGORIES else DEFAULT_CAT  # старые кнопки («root») тоже работают
    text, markup = list_screen(ctx, call.from_user.id, cat, callback_data.page)
    if callback_data.new:
        await message.answer(text, reply_markup=markup)
    else:
        await show_screen(message, text, markup)


@router.callback_query(Prod.filter(F.act.in_({"open", "more", "info", "add"})))
async def product_action(call: CallbackQuery, callback_data: Prod, ctx: Ctx) -> None:
    product = catalog.get_product(ctx.db, callback_data.sku)
    if product is None or not product.active:
        await call.answer("Этого товара уже нет в каталоге.", show_alert=True)
        return
    message = msg_of(call)
    month = ctx.local_now().month

    if callback_data.act == "add":
        if reason := product.unavailable_reason(month):
            await call.answer(reason[:1].upper() + reason[1:] + ".", show_alert=True)
            return
        if error := catalog.cart_add(ctx.db, call.from_user.id, product.sku):
            await call.answer(error, show_alert=True)
            return
        count, total = cart_info(ctx, call.from_user.id)
        await call.answer(f"✅ Добавлено! В корзине {count} шт. на {rub(total)}")
        if message is not None:  # обновляем только кнопки: появилась или изменилась «🛒 Корзина · …»
            _, markup = card_screen(ctx, call.from_user.id, product, is_expanded(message))
            try:
                await message.edit_reply_markup(reply_markup=markup)
            except TelegramBadRequest:
                pass
        return

    await call.answer()
    if message is None:
        return
    text, markup = card_screen(ctx, call.from_user.id, product, expanded=callback_data.act in ("more", "info"))
    await show_product(message, ctx, product, text, markup)
