"""Отзывы: читать, оставить в боте, перейти на Яндекс Карты."""
from __future__ import annotations

import html

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import crm, keyboards as kb, notify, orders, texts
from ..callbacks import Adm, Mo, Rv
from ..ctx import Ctx
from ..states import ReviewFlow
from ..ui import full_name, msg_of
from ..util import clean_text, esc

router = Router(name="reviews")
PAGE = 5


async def intro(message: Message, ctx: Ctx) -> None:
    await message.answer("⭐ <b>Отзывы</b>\n\nНам важно ваше мнение: оно помогает стать лучше. "
                         "Прочитайте, что пишут другие, или поделитесь впечатлением.",
                         reply_markup=kb.reviews_menu(ctx.shop))


@router.message(F.text == texts.BTN_REVIEWS)
async def open_reviews(message: Message, ctx: Ctx) -> None:
    await intro(message, ctx)


@router.callback_query(Rv.filter(F.act == "read"))
async def read(call: CallbackQuery, callback_data: Rv, ctx: Ctx) -> None:
    await call.answer()
    message = msg_of(call)
    if message is None:
        return
    page = max(0, callback_data.val)
    rows = crm.list_reviews(ctx.db, PAGE, page * PAGE)
    if not rows:
        url = html.escape(ctx.shop.reviews_url, quote=True)  # кавычки в ссылке не должны ломать разметку
        extra = f"\nВсе отзывы о нас — на <a href=\"{url}\">Яндекс Картах</a>." if ctx.shop.reviews_url else ""
        await message.answer("Здесь пока нет отзывов — будьте первым(ой)! 🌸" + extra, reply_markup=kb.reviews_menu(ctx.shop))
        return
    out = []
    for r in rows:
        first_name = esc(r["name"].split()[0]) if r["name"] else "Клиент"  # фамилию не показываем
        out.append(f"{'⭐' * r['rating']}\n<i>«{esc(r['text'])}»</i>\n— {first_name}, {r['created_at'][:10]}")
    has_more = crm.count_reviews(ctx.db) > (page + 1) * PAGE
    await message.answer("\n\n".join(out), reply_markup=kb.reviews_page(page, has_more))


@router.callback_query(Rv.filter(F.act == "write"))
async def write(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    if message := msg_of(call):
        await state.clear()
        await message.answer("Как оцените нас? 👇", reply_markup=kb.rating())


@router.callback_query(Rv.filter(F.act == "rate"))
async def rate(call: CallbackQuery, callback_data: Rv, state: FSMContext) -> None:
    if not 1 <= callback_data.val <= 5:
        await call.answer()
        return
    await call.answer()
    if message := msg_of(call):
        data = await state.get_data()
        await state.set_state(ReviewFlow.text)
        await state.update_data(rating=callback_data.val, order_id=data.get("order_id"))
        await message.answer(f"{'⭐' * callback_data.val}\n\nНапишите пару слов: что понравилось, что можно улучшить?"
                             "\n\n<i>Передумали? Нажмите любую кнопку меню.</i>")


@router.message(ReviewFlow.text, F.text)
async def got_text(message: Message, state: FSMContext, ctx: Ctx, bot: Bot) -> None:
    text = clean_text(message.text, 1000)
    if len(text) < 5:
        await message.answer("Напишите, пожалуйста, хотя бы пару слов 🙂")
        return
    data = await state.get_data()
    await state.clear()
    rating, order_id = data.get("rating", 5), data.get("order_id")
    name = full_name(message.from_user)
    crm.add_review(ctx.db, message.from_user.id, name, text, rating, order_id)
    markup = kb.kb([kb.url_btn("⭐ Отзыв на Яндекс Картах", ctx.shop.reviews_url)]) if ctx.shop.reviews_url else None
    await message.answer("❤️ Спасибо за отзыв! Он поможет нам стать лучше.\n"
                         + ("Если захотите, оставьте его и на Яндекс Картах — это очень помогает магазину." if markup else ""),
                         reply_markup=markup)
    flag = "⚠️ <b>Низкая оценка — свяжитесь с клиентом</b>\n" if rating <= 3 else ""
    where = f" (заказ №{order_id})" if order_id else ""
    username = message.from_user.username or ""
    await notify.to_staff(bot, ctx, f"{flag}📝 <b>Новый отзыв{where}</b> — {'⭐' * rating}\n"
                                    f"👤 {esc(name)} — <a href=\"tg://user?id={message.from_user.id}\">"
                                    f"{'@' + esc(username) if username else 'профиль'}</a>\n\n{esc(text)}",
                          kb.kb([kb.btn("↩️ Ответить", Adm(act="msg", id=message.from_user.id))]))


@router.message(ReviewFlow.text)
async def need_text(message: Message) -> None:
    await message.answer("Напишите отзыв текстом 🙂")


@router.callback_query(Mo.filter(F.act == "review"))
async def review_order(call: CallbackQuery, callback_data: Mo, state: FSMContext, ctx: Ctx) -> None:
    """Оценить выполненный заказ (только свой)."""
    order = orders.get_user_order(ctx.db, callback_data.id, call.from_user.id)
    if order is None or order.status != "done":
        await call.answer("Оценить можно только выполненный заказ.", show_alert=True)
        return
    await call.answer()
    if message := msg_of(call):
        await state.clear()
        await state.update_data(order_id=order.id)
        await message.answer(f"Как оцените заказ №{order.id}? 👇", reply_markup=kb.rating())
