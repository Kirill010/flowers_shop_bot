"""Каталог: разделы, карточки товаров, «Подробнее», «В корзину»."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from .. import catalog, keyboards as kb, texts
from ..callbacks import Cat, Prod
from ..catalog import CATEGORIES
from ..ctx import Ctx
from ..ui import msg_of, send_product
from ..util import rub

router = Router(name="catalog")
PAGE = 5  # сколько карточек показываем за раз


@router.message(F.text == texts.BTN_CATALOG)
@router.message(Command("catalog"))
async def open_catalog(message: Message) -> None:
    await message.answer(texts.catalog_intro(), reply_markup=kb.catalog_root())


@router.callback_query(Cat.filter())
async def open_category(call: CallbackQuery, callback_data: Cat, ctx: Ctx) -> None:
    await call.answer()
    message = msg_of(call)
    if message is None:
        return
    cat = callback_data.cat
    if cat == "root":
        await message.answer(texts.catalog_intro(), reply_markup=kb.catalog_root())
        return
    if cat not in CATEGORIES:
        return
    products = catalog.list_products(ctx.db, cat)
    start = max(0, callback_data.page) * PAGE
    chunk = products[start:start + PAGE]
    if not chunk:
        await message.answer("В этом разделе пока пусто. Загляните позже или напишите менеджеру 🌸", reply_markup=kb.catalog_root())
        return
    month = ctx.local_now().month
    if start == 0:
        await message.answer(texts.category_title(cat))
    for product in chunk:
        await send_product(message, ctx, product, texts.product_card(product, month), kb.product_card(product, month))
    shown_to = start + len(chunk)
    await message.answer(f"Показано {start + 1}–{shown_to} из {len(products)}",
                         reply_markup=kb.catalog_nav(cat, callback_data.page, shown_to, len(products)))


@router.callback_query(Prod.filter(F.act.in_({"info", "add"})))
async def product_action(call: CallbackQuery, callback_data: Prod, ctx: Ctx) -> None:
    product = catalog.get_product(ctx.db, callback_data.sku)
    if product is None or not product.active:
        await call.answer("Этого товара уже нет в каталоге.", show_alert=True)
        return
    month = ctx.local_now().month
    if callback_data.act == "info":
        await call.answer()
        if message := msg_of(call):
            await send_product(message, ctx, product, texts.product_details(product, month),
                               kb.product_details(product, month))
        return

    if reason := product.unavailable_reason(month):
        await call.answer(reason[:1].upper() + reason[1:] + ".", show_alert=True)
        return
    if error := catalog.cart_add(ctx.db, call.from_user.id, product.sku):
        await call.answer(error, show_alert=True)
        return
    lines = catalog.cart_lines(ctx.db, call.from_user.id)
    await call.answer(f"✅ Добавлено! В корзине {sum(l.qty for l in lines)} шт. на {rub(sum(l.total for l in lines))}")
