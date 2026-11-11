"""Связь с менеджером: вопрос, наличие, срочный заказ, телефон, подбор под бюджет, доставка за МКАД."""
from __future__ import annotations

import contextlib
import re
from datetime import timedelta

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, User

from .. import catalog, crm, keyboards as kb, notify, texts
from ..callbacks import Mg, Prod
from ..crm import LEAD_KINDS
from ..ctx import Ctx
from ..states import Ask, Budget, Callback
from ..ui import full_name, menu_for, msg_of, safe_edit
from ..util import clean_text, db_time, esc, phone_from_input, rub

router = Router(name="manager")
LEADS_PER_HOUR = 5  # не больше стольких обращений в час от одного клиента (защита сотрудников от спама)

PROMPTS = {
    "question": "❓ <b>Задать вопрос</b>\n\nНапишите вопрос одним сообщением — я передам его менеджеру. Ответ придёт сюда, в чат.",
    "availability": ("🔎 <b>Уточнить наличие</b>\n\nНапишите, что вас интересует: название букета или растения, цвет, "
                     "количество и на какую дату."),
    "urgent": ("⚡ <b>Срочный заказ</b>\n\nНапишите одним сообщением: что нужно, к какому времени, куда доставить "
               "и ваш телефон. Быстрее всего — позвонить: {phone}"),
    "outside": ("🚗 <b>Доставка за МКАД или в другой город</b>\n\nСтоимость рассчитает менеджер. Напишите адрес, "
                "удобную дату и время и ваш телефон — свяжемся и всё согласуем. Корзина приложится к обращению."),
}


def lead_ok(ctx: Ctx) -> str:
    return (f"✅ Передали менеджеру. Ответим здесь, в Telegram, — в рабочее время обычно в течение "
            f"{texts.duration(ctx.shop.response_minutes)}.\nСрочно? Позвоните: {esc(ctx.shop.phone)}")


def lead_limit(ctx: Ctx) -> str:
    return ("Мы уже получили ваши обращения и ответим в ближайшее время 🙏\n"
            f"Если очень срочно — позвоните: {esc(ctx.shop.phone)}")


async def create_lead(bot: Bot, ctx: Ctx, user: User, kind: str, text: str, *, phone: str = "",
                      amount: int = 0) -> int | None:
    """Сохраняет обращение и сообщает сотрудникам. None — клиент прислал слишком много обращений за час."""
    since = db_time(ctx.now() - timedelta(hours=1))
    if crm.recent_leads(ctx.db, user.id, since) >= LEADS_PER_HOUR:
        return None
    profile = crm.get_user(ctx.db, user.id)
    name = (profile["name"] if profile and profile["name"] else full_name(user))
    phone = phone or (profile["phone"] if profile else "")
    lead_id = crm.add_lead(ctx.db, user.id, kind, text, name=name, phone=phone, amount=amount)
    row = crm.get_lead(ctx.db, lead_id)
    await notify.to_staff(bot, ctx, texts.lead_card(LEAD_KINDS[kind], name, user.username or "", user.id, phone, text),
                          kb.lead(row))
    return lead_id


async def start_ask(message: Message, state: FSMContext, ctx: Ctx, kind: str, context: str = "",
                    user_id: int | None = None) -> None:
    await state.set_state(Ask.text)
    await state.update_data(kind=kind, context=context)
    markup = None
    if kind == "availability" and user_id and catalog.cart_count(ctx.db, user_id):
        from ..callbacks import Cart
        markup = kb.kb([kb.btn("🛒 Проверить мою корзину", Cart(act="check"))])
    await message.answer(PROMPTS[kind].format(phone=esc(ctx.shop.phone)) + "\n\n<i>Передумали? Нажмите любую кнопку меню.</i>",
                         reply_markup=markup)


async def cart_availability(message: Message, user: User, ctx: Ctx, bot: Bot) -> None:
    lines = catalog.cart_lines(ctx.db, user.id)
    if not lines:
        await message.answer("Корзина пуста — нечего проверять 🙂")
        return
    text = "Прошу уточнить наличие:\n" + "\n".join(f"• {l.product.name} × {l.qty}" for l in lines)
    lead_id = await create_lead(bot, ctx, user, "availability", text)
    await message.answer(lead_ok(ctx) if lead_id else lead_limit(ctx))


# ───────────────────────── меню менеджера ─────────────────────────

@router.message(F.text == texts.BTN_MANAGER)
async def open_manager(message: Message, ctx: Ctx) -> None:
    await message.answer(texts.manager_text(ctx.shop), reply_markup=kb.manager(ctx.shop))


@router.callback_query(Mg.filter(F.act == "menu"))
async def manager_menu(call: CallbackQuery, ctx: Ctx) -> None:
    await call.answer()
    if message := msg_of(call):
        await message.answer(texts.manager_text(ctx.shop), reply_markup=kb.manager(ctx.shop))


@router.callback_query(Mg.filter(F.act.in_({"ask", "avail", "urgent"})))
async def manager_ask(call: CallbackQuery, callback_data: Mg, state: FSMContext, ctx: Ctx) -> None:
    await call.answer()
    if message := msg_of(call):
        kind = {"ask": "question", "avail": "availability", "urgent": "urgent"}[callback_data.act]
        await start_ask(message, state, ctx, kind, user_id=call.from_user.id)


@router.callback_query(Prod.filter(F.act == "question"))
async def product_question(call: CallbackQuery, callback_data: Prod, state: FSMContext, ctx: Ctx) -> None:
    product = catalog.get_product(ctx.db, callback_data.sku)
    await call.answer()
    if message := msg_of(call):
        context = f"Вопрос по товару «{product.name}»" if product else ""
        await start_ask(message, state, ctx, "question", context)


@router.callback_query(Prod.filter(F.act == "ask"))
async def product_availability(call: CallbackQuery, callback_data: Prod, ctx: Ctx, bot: Bot) -> None:
    product = catalog.get_product(ctx.db, callback_data.sku)
    if product is None:
        await call.answer("Этого товара уже нет в каталоге.", show_alert=True)
        return
    price = "цена по запросу" if product.on_request else rub(product.price)
    lead_id = await create_lead(bot, ctx, call.from_user, "availability",
                                f"Узнать цену и наличие: «{product.name}» ({price})")
    await call.answer()
    if message := msg_of(call):
        await message.answer(lead_ok(ctx) if lead_id else lead_limit(ctx))


@router.message(Ask.text, F.text)
async def ask_received(message: Message, state: FSMContext, ctx: Ctx, bot: Bot) -> None:
    text = clean_text(message.text, 1000)
    if len(text) < 3:
        await message.answer("Напишите, пожалуйста, чуть подробнее 🙂")
        return
    data = await state.get_data()
    kind = data.get("kind", "question")
    body = f"{data['context']}\n\n{text}" if data.get("context") else text
    if kind == "outside":
        lines = catalog.cart_lines(ctx.db, message.from_user.id)
        if lines:
            body += "\n\nКорзина:\n" + "\n".join(f"• {l.product.name} × {l.qty} — {rub(l.total)}" for l in lines)
    await state.clear()
    lead_id = await create_lead(bot, ctx, message.from_user, kind, body)
    await message.answer(lead_ok(ctx) if lead_id else lead_limit(ctx), reply_markup=menu_for(ctx, message.from_user.id))


@router.message(Ask.text)
async def ask_not_text(message: Message) -> None:
    await message.answer("Я пока понимаю только текст — напишите вопрос словами 🙂")


# ───────────────────────── «это вопрос менеджеру?» (непонятное сообщение клиента) ─────────────────────────

@router.callback_query(Mg.filter(F.act == "fwd"))
async def forward_to_manager(call: CallbackQuery, state: FSMContext, ctx: Ctx, bot: Bot) -> None:
    data = await state.get_data()
    text, sources = data.get("fwd"), list(data.get("fwd_msgs") or [])
    if not text:
        await call.answer("Это сообщение уже отправлено или устарело.", show_alert=True)
        return
    await state.update_data(fwd=None, fwd_msgs=None, fwd_group=None)
    lead_id = await create_lead(bot, ctx, call.from_user, "question", text)
    if lead_id and sources:  # фото, файл или голосовое — после карточки обращения передаём и их самих
        await notify.copy_to_staff(bot, ctx, call.from_user.id, sources)
    await call.answer()
    if message := msg_of(call):
        await safe_edit(message, lead_ok(ctx) if lead_id else lead_limit(ctx), None)


@router.callback_query(Mg.filter(F.act == "dismiss"))
async def dismiss_forward(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(fwd=None, fwd_msgs=None, fwd_group=None)
    await call.answer()
    if message := msg_of(call):
        with contextlib.suppress(TelegramAPIError):
            await message.delete()


# ───────────────────────── «оставить номер телефона» ─────────────────────────

@router.callback_query(Mg.filter(F.act == "phone"))
async def phone_start(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    if message := msg_of(call):
        await state.set_state(Callback.phone)
        await message.answer("📞 Нажмите «Поделиться номером» или напишите номер сообщением — менеджер перезвонит.",
                             reply_markup=kb.contact_request())


@router.message(Callback.phone, F.contact)
async def phone_contact(message: Message, state: FSMContext, ctx: Ctx, bot: Bot) -> None:
    if message.contact.user_id != message.from_user.id:
        await message.answer("Пришлите, пожалуйста, свой номер — кнопкой «Поделиться номером» или текстом.")
        return
    await _phone_done(message, state, ctx, bot, message.contact.phone_number)


@router.message(Callback.phone, F.text)
async def phone_text(message: Message, state: FSMContext, ctx: Ctx, bot: Bot) -> None:
    await _phone_done(message, state, ctx, bot, message.text)


async def _phone_done(message: Message, state: FSMContext, ctx: Ctx, bot: Bot, raw: str) -> None:
    phone = phone_from_input(raw)
    if phone is None:
        await message.answer("Не получилось распознать номер. Напишите так: +7 900 123-45-67")
        return
    crm.save_contact(ctx.db, message.from_user.id, phone=phone)
    await state.clear()
    lead_id = await create_lead(bot, ctx, message.from_user, "callback", "Просит перезвонить.", phone=phone)
    await message.answer(
        (f"✅ Спасибо! Перезвоним в рабочее время — обычно в течение {texts.duration(ctx.shop.response_minutes)}."
         if lead_id else lead_limit(ctx)), reply_markup=menu_for(ctx, message.from_user.id))


# ───────────────────────── подбор под бюджет ─────────────────────────

@router.callback_query(Mg.filter(F.act == "budget"))
async def budget_start(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    if message := msg_of(call):
        await state.set_state(Budget.amount)
        await message.answer("💰 <b>Подбор букета под бюджет</b>\n\nФлористы подберут вариант именно для вас. "
                             "Напишите, на какую сумму рассчитываете (в рублях):\n\n<i>Передумали? Нажмите любую кнопку меню.</i>")


@router.message(Budget.amount, F.text)
async def budget_amount(message: Message, state: FSMContext, ctx: Ctx) -> None:
    digits = re.sub(r"\D", "", message.text)
    amount = int(digits) if digits and len(digits) <= 7 else 0
    if not 500 <= amount <= 1_000_000:
        await message.answer("Напишите сумму числом, от 500 ₽. Например: 3000")
        return
    await state.update_data(budget=amount)
    profile = crm.get_user(ctx.db, message.from_user.id)
    if profile and profile["phone"]:
        await state.set_state(Budget.wishes)
        await _ask_wishes(message)
    else:
        await state.set_state(Budget.phone)
        await message.answer("📞 Оставьте телефон — менеджер свяжется и предложит варианты.", reply_markup=kb.contact_request())


@router.message(Budget.phone, F.contact | F.text)
async def budget_phone(message: Message, state: FSMContext, ctx: Ctx) -> None:
    if message.contact and message.contact.user_id != message.from_user.id:
        await message.answer("Пришлите, пожалуйста, свой номер.")
        return
    phone = phone_from_input(message.contact.phone_number if message.contact else message.text)
    if phone is None:
        await message.answer("Не получилось распознать номер. Напишите так: +7 900 123-45-67")
        return
    crm.save_contact(ctx.db, message.from_user.id, phone=phone)
    await state.set_state(Budget.wishes)
    await message.answer("Спасибо! 🌸", reply_markup=menu_for(ctx, message.from_user.id))
    await _ask_wishes(message)


async def _ask_wishes(message: Message) -> None:
    await message.answer("🎨 Расскажите о пожеланиях: любимые цветы, цвета, повод, кому дарите. Можно коротко.")


@router.message(Budget.wishes, F.text)
async def budget_wishes(message: Message, state: FSMContext, ctx: Ctx, bot: Bot) -> None:
    data = await state.get_data()
    await state.clear()
    text = f"Бюджет: {rub(data['budget'])}\nПожелания: {clean_text(message.text, 700)}"
    lead_id = await create_lead(bot, ctx, message.from_user, "budget", text)
    await message.answer(lead_ok(ctx) if lead_id else lead_limit(ctx), reply_markup=menu_for(ctx, message.from_user.id))


@router.message(Budget.amount)
@router.message(Budget.phone)
@router.message(Budget.wishes)
async def budget_not_text(message: Message) -> None:
    await message.answer("Напишите ответ текстом 🙂")
