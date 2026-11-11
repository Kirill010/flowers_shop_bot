"""Оформление заказа — короткий путь.

Новому клиенту: имя (одно нажатие — берём из Telegram) → телефон (кнопка «Поделиться») → доставка или самовывоз →
адрес → когда (три быстрые кнопки) → оплата. Постоянному клиенту вместо имени и телефона — «Да, всё верно».

Всё необязательное — открытка, другой получатель, промокод, сертификат, правка времени и адреса — спрятано за одной
кнопкой «✏️ Изменить или добавить»: тем, кому это не нужно, оно не мешает. Исключение: если в корзине товар с подсказкой
(note_prompt в catalog.toml, например музыкальный букет), бот сам спросит «какая песня?».

В состоянии диалога хранится только то, что ввёл клиент. Цены, скидки и итог каждый раз считаются заново из базы
(orders.quote_for), поэтому подменить сумму через кнопку нельзя."""
from __future__ import annotations

import logging
import re
from datetime import date

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, User

from .. import billing, catalog, certs, crm, keyboards as kb, notify, orders, schedule, texts
from ..callbacks import Co
from ..ctx import Ctx
from ..orders import Draft, OrderError, PriceChanged
from ..states import Checkout
from ..ui import menu_for, msg_of, safe_edit
from ..util import clean_text, esc, format_phone, parse_user_date, phone_from_input, when_label
from ..yookassa import YooKassaError
from . import manager

log = logging.getLogger(__name__)
router = Router(name="checkout")
STALE = "Это окно устарело. Откройте корзину и начните оформление заново 🙂"
LETTERS = re.compile(r"[A-Za-zА-Яа-яЁё]")


def delivery_hint(ctx: Ctx) -> str:
    """Коротко про цену доставки — для вопроса «как получите заказ»."""
    shop = ctx.shop
    price = shop.zones[0].price if len(shop.zones) == 1 else None
    base = f"по Москве {texts.rub(price)}" if price is not None else f"от {texts.rub(min(z.price for z in shop.zones))}"
    return base + (f", от {texts.rub(shop.free_from)} — бесплатно" if shop.free_from else "")


# ───────────────────────── вход ─────────────────────────

async def begin(message: Message, user: User, state: FSMContext, ctx: Ctx) -> None:
    """Начало оформления (кнопка «✅ Оформить заказ» в корзине)."""
    if not ctx.staff_ready:
        await message.answer(f"Оформление заказов в боте ещё настраивается. Позвоните нам: {esc(ctx.shop.phone)}")
        return
    if ctx.paused:
        await message.answer("⏸ " + esc(ctx.shop.pause_message))
        return
    check = orders.quote_for(ctx.db, ctx.shop, user.id, method="pickup", zone_name="", promo_code="", cert_code="",
                             today=ctx.local_now().date())
    if check.problems:
        await message.answer("⚠️ Сначала поправьте корзину:\n" + "\n".join(f"• {esc(p)}" for p in check.problems))
        return
    await state.clear()
    await state.update_data(_on=True)
    profile = crm.get_user(ctx.db, user.id)
    if profile and profile["name"] and profile["phone"]:
        await message.answer(f"Оформляем на <b>{esc(profile['name'])}</b>, {esc(format_phone(profile['phone']))}?",
                             reply_markup=kb.co_saved())
    else:
        await ask_contact(message, state, user, first=True)


async def _alive(call: CallbackQuery, state: FSMContext) -> dict | None:
    """Данные оформления, если оно идёт. Нажатие на кнопку из старого сообщения — вежливый отказ."""
    data = await state.get_data()
    if not data.get("_on"):
        await call.answer(STALE, show_alert=True)
        return None
    return data


async def _strip(call: CallbackQuery) -> None:
    """Убирает кнопки у сообщения, на которое нажали: чтобы не нажали дважды и не запутались."""
    if message := msg_of(call):
        try:
            await message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass


async def go(message: Message, user_id: int, state: FSMContext, ctx: Ctx, step: str) -> None:
    """Следующий шаг. Если человек пришёл сюда из «✏️ Изменить» — возвращаем его к проверке заказа."""
    if (await state.get_data()).get("_back") == "summary":
        await show_summary(message, user_id, state, ctx)
    elif step == "method":
        await ask_method(message, state, ctx)
    elif step == "when":
        await ask_when(message, state, ctx)
    elif step == "after_when":
        await after_when(message, user_id, state, ctx)
    else:
        await show_summary(message, user_id, state, ctx)


# ───────────────────────── имя и телефон ─────────────────────────

async def ask_contact(message: Message, state: FSMContext, user: User, first: bool = False) -> None:
    """Самый короткий путь: одна кнопка «Поделиться номером» даёт и телефон, и имя из Telegram.
    Кто не хочет делиться — «Ввести вручную»: тогда спросим имя и номер по отдельности."""
    await state.update_data(name="", phone="")
    await state.set_state(Checkout.phone)
    hint = "\n\n<i>Передумали? Нажмите любую кнопку меню.</i>" if first else ""
    await message.answer("📞 Нажмите «Поделиться номером» внизу — этого достаточно, имя возьмём из Telegram." + hint,
                         reply_markup=kb.contact_request(manual=True))


async def ask_name(message: Message, state: FSMContext, user: User) -> None:
    await state.set_state(Checkout.name)
    shown = clean_text(user.first_name, 40)
    if shown and LETTERS.search(shown):
        await message.answer("👤 Как к вам обращаться? Нажмите своё имя или напишите другое.",
                             reply_markup=kb.co_name(shown))
    else:
        await message.answer("👤 Как вас зовут? Напишите имя.")


async def ask_phone(message: Message, state: FSMContext) -> None:
    await state.set_state(Checkout.phone)
    await message.answer("📞 Теперь телефон: нажмите «Поделиться номером» внизу или напишите номер.",
                         reply_markup=kb.contact_request())


async def next_contact_step(message: Message, state: FSMContext, ctx: Ctx, user: User) -> None:
    """Имя и телефон приходят в любом порядке и из любого источника. Как только есть оба — идём дальше;
    чего не хватает — спрашиваем."""
    data = await state.get_data()
    if not data.get("name"):
        await ask_name(message, state, user)
    elif not data.get("phone"):
        await ask_phone(message, state)
    else:
        crm.save_contact(ctx.db, user.id, name=data["name"], phone=data["phone"])
        # одно короткое подтверждение; заодно возвращаем главное меню вместо кнопки «Поделиться номером»
        await message.answer(f"✅ {esc(data['name'])}, {esc(format_phone(data['phone']))}", reply_markup=menu_for(ctx, user.id))
        await go(message, user.id, state, ctx, "method")


@router.callback_query(Co.filter(F.act == "saved"))
async def use_saved(call: CallbackQuery, state: FSMContext, ctx: Ctx) -> None:
    if await _alive(call, state) is None or (message := msg_of(call)) is None:
        return
    profile = crm.get_user(ctx.db, call.from_user.id)
    await call.answer()
    await _strip(call)
    if not profile or not profile["name"] or not profile["phone"]:
        await ask_contact(message, state, call.from_user, first=True)
        return
    await state.update_data(name=profile["name"], phone=profile["phone"])
    await go(message, call.from_user.id, state, ctx, "method")


@router.callback_query(Co.filter(F.act == "edit_contact"))
async def edit_contact(call: CallbackQuery, state: FSMContext) -> None:
    data = await _alive(call, state)
    if data is None or (message := msg_of(call)) is None:
        return
    await call.answer()
    await _strip(call)
    if data.get("slot"):  # нажали на экране проверки заказа — после телефона вернёмся туда же
        await state.update_data(_back="summary")
    await ask_contact(message, state, call.from_user)


@router.callback_query(Co.filter(F.act == "name_tg"))
async def name_from_telegram(call: CallbackQuery, state: FSMContext, ctx: Ctx) -> None:
    if await _alive(call, state) is None or (message := msg_of(call)) is None:
        return
    name = clean_text(call.from_user.first_name, 60)
    if len(name) < 2 or not LETTERS.search(name):
        await call.answer("Напишите имя сообщением, пожалуйста.", show_alert=True)
        return
    await call.answer()
    await _strip(call)
    await state.update_data(name=name)
    await next_contact_step(message, state, ctx, call.from_user)


@router.message(Checkout.name, F.text)
async def got_name(message: Message, state: FSMContext, ctx: Ctx) -> None:
    name = clean_text(message.text, 60)
    if len(name) < 2 or not LETTERS.search(name):
        await message.answer("Напишите, пожалуйста, имя буквами 🙂")
        return
    await state.update_data(name=name)
    await next_contact_step(message, state, ctx, message.from_user)


@router.message(Checkout.phone, F.contact)
async def got_contact(message: Message, state: FSMContext, ctx: Ctx) -> None:
    if message.contact.user_id != message.from_user.id:
        await message.answer("Пришлите, пожалуйста, свой номер — кнопкой «Поделиться номером» или текстом.")
        return
    phone = phone_from_input(message.contact.phone_number)
    if phone is None:
        await message.answer("Не получилось распознать номер. Напишите так: +7 900 123-45-67")
        return
    await state.update_data(phone=phone)
    # имя берём из карточки контакта (то, что человек сам указал в Telegram); если там только смайлики — спросим
    name = clean_text(f"{message.contact.first_name or ''} {message.contact.last_name or ''}", 60)
    if len(name) >= 2 and LETTERS.search(name):
        await state.update_data(name=name)
    await next_contact_step(message, state, ctx, message.from_user)


@router.message(Checkout.phone, F.text)
async def got_phone(message: Message, state: FSMContext, ctx: Ctx) -> None:
    text = clean_text(message.text, 60)
    if text == kb.BTN_MANUAL:  # «Ввести вручную»: имя и телефон — по отдельности
        await ask_name(message, state, message.from_user)
        return
    phone = phone_from_input(text)
    if phone is not None:
        await state.update_data(phone=phone)
    elif (LETTERS.search(text) and not re.search(r"\d", text) and len(text) >= 2
          and not (await state.get_data()).get("name")):  # имя ещё не знаем, а человек его и написал — тоже ок
        await state.update_data(name=text)
    else:
        await message.answer("Не получилось распознать номер. Напишите так: +7 900 123-45-67")
        return
    await next_contact_step(message, state, ctx, message.from_user)


# ───────────────────────── доставка / самовывоз ─────────────────────────

async def ask_method(message: Message, state: FSMContext, ctx: Ctx) -> None:
    await state.set_state(None)
    if not ctx.shop.delivery_enabled:  # доставки нет — вопрос лишний
        await state.update_data(method="pickup", zone="", address="")
        await ask_when(message, state, ctx)
        return
    await message.answer(f"📦 Как получите заказ?\n🛍 Самовывоз — бесплатно\n🚚 Доставка — {delivery_hint(ctx)}",
                         reply_markup=kb.co_method(ctx.shop))


async def ask_address(message: Message, state: FSMContext, ctx: Ctx) -> None:
    await state.set_state(Checkout.address)
    await message.answer("🏠 Напишите адрес доставки: улица, дом, подъезд, этаж, квартира.\n"
                         f"<i>{esc(ctx.shop.area_text)}</i>", reply_markup=kb.co_address())


@router.callback_query(Co.filter(F.act == "method"))
async def chose_method(call: CallbackQuery, callback_data: Co, state: FSMContext, ctx: Ctx) -> None:
    if await _alive(call, state) is None or (message := msg_of(call)) is None:
        return
    method = callback_data.val
    if method not in ("delivery", "pickup") or (method == "delivery" and not ctx.shop.delivery_enabled):
        await call.answer("Этот способ сейчас недоступен.", show_alert=True)
        return
    await call.answer()
    await _strip(call)
    await state.update_data(method=method, zone="", address="")
    if method == "pickup":
        await ask_when(message, state, ctx)
    elif len(ctx.shop.zones) == 1:  # зона одна — не заставляем выбирать, цена уже названа
        await state.update_data(zone=ctx.shop.zones[0].name)
        await ask_address(message, state, ctx)
    else:
        await message.answer("🚚 Куда доставить?", reply_markup=kb.co_zones(ctx.shop))


@router.callback_query(Co.filter(F.act == "back_method"))
async def back_method(call: CallbackQuery, state: FSMContext, ctx: Ctx) -> None:
    if await _alive(call, state) is None or (message := msg_of(call)) is None:
        return
    await call.answer()
    await _strip(call)
    await ask_method(message, state, ctx)


@router.callback_query(Co.filter(F.act == "zone"))
async def chose_zone(call: CallbackQuery, callback_data: Co, state: FSMContext, ctx: Ctx) -> None:
    if await _alive(call, state) is None or (message := msg_of(call)) is None:
        return
    zones = ctx.shop.zones
    if not callback_data.val.isdigit() or int(callback_data.val) >= len(zones):
        await call.answer("Такой зоны нет.", show_alert=True)
        return
    await call.answer()
    await _strip(call)
    await state.update_data(zone=zones[int(callback_data.val)].name)
    await ask_address(message, state, ctx)


@router.callback_query(Co.filter(F.act == "outside"))
async def outside(call: CallbackQuery, state: FSMContext, ctx: Ctx) -> None:
    if await _alive(call, state) is None or (message := msg_of(call)) is None:
        return
    await call.answer()
    await _strip(call)
    await state.clear()
    await manager.start_ask(message, state, ctx, "outside", user_id=call.from_user.id)


@router.callback_query(Co.filter(F.act == "edit_address"))
async def edit_address(call: CallbackQuery, state: FSMContext, ctx: Ctx) -> None:
    if await _alive(call, state) is None or (message := msg_of(call)) is None:
        return
    await call.answer()
    await _strip(call)
    await state.update_data(_back="summary")
    await ask_address(message, state, ctx)


@router.message(Checkout.address, F.text)
async def got_address(message: Message, state: FSMContext, ctx: Ctx) -> None:
    address = clean_text(message.text, 200)
    if len(address) < 6:
        await message.answer("Напишите адрес полностью: улица, дом, квартира 🙂")
        return
    await state.update_data(address=address)
    await state.set_state(None)
    await go(message, message.from_user.id, state, ctx, "when")


# ───────────────────────── когда: три быстрые кнопки, остальное — «Другая дата» ─────────────────────────

async def ask_when(message: Message, state: FSMContext, ctx: Ctx) -> None:
    data = await state.get_data()
    method = data.get("method", "pickup")
    local = ctx.local_now()
    labels = [s.label for s in ctx.shop.slots]
    options = schedule.nearest_options(ctx.shop, method, local)
    items = [(when_label(day, label, local.date()),
              f"{day.isoformat()}.{'a' if label == schedule.ASAP else labels.index(label)}") for day, label in options]
    await state.set_state(None)
    verb = "доставить" if method == "delivery" else "забрать"
    note = ("\nℹ️ На другой день — только по предоплате."
            if ctx.shop.prepay_future_orders and any(day > local.date() for day, _ in options) else "")
    await message.answer(f"🗓 Когда {verb} заказ?{note}", reply_markup=kb.co_when(items))


@router.callback_query(Co.filter(F.act == "when"))
async def chose_when(call: CallbackQuery, callback_data: Co, state: FSMContext, ctx: Ctx) -> None:
    data = await _alive(call, state)
    if data is None or (message := msg_of(call)) is None:
        return
    iso, _, code = callback_data.val.partition(".")
    try:
        day = date.fromisoformat(iso)
    except ValueError:
        await call.answer("Неправильная дата.", show_alert=True)
        return
    if code == "a":
        label = schedule.ASAP
    elif code.isdigit() and int(code) < len(ctx.shop.slots):
        label = ctx.shop.slots[int(code)].label
    else:
        await call.answer("Такого времени нет.", show_alert=True)
        return
    local = ctx.local_now()
    if schedule.date_problem(ctx.shop, day, local) or not schedule.slot_valid(ctx.shop, data["method"], day, local, label):
        await call.answer("Это время уже недоступно — выберите другое.", show_alert=True)
        await _strip(call)
        await ask_when(message, state, ctx)
        return
    await call.answer()
    await _strip(call)
    await state.update_data(date=day.isoformat(), slot=label)
    await go(message, call.from_user.id, state, ctx, "after_when")


@router.callback_query(Co.filter(F.act == "edit_when"))
async def edit_when(call: CallbackQuery, state: FSMContext, ctx: Ctx) -> None:
    if await _alive(call, state) is None or (message := msg_of(call)) is None:
        return
    await call.answer()
    await _strip(call)
    await state.update_data(_back="summary")
    await ask_when(message, state, ctx)


@router.callback_query(Co.filter(F.act == "back_when"))
async def back_when(call: CallbackQuery, state: FSMContext, ctx: Ctx) -> None:
    if await _alive(call, state) is None or (message := msg_of(call)) is None:
        return
    await call.answer()
    await _strip(call)
    await ask_when(message, state, ctx)


async def ask_date(message: Message, state: FSMContext, ctx: Ctx) -> None:
    data = await state.get_data()
    local = ctx.local_now()
    days = schedule.date_choices(ctx.shop, data.get("method", "pickup"), local)
    await state.set_state(None)
    await message.answer("🗓 Выберите дату:", reply_markup=kb.co_dates(days, local.date()))


@router.callback_query(Co.filter(F.act == "pick_date"))
async def pick_date(call: CallbackQuery, state: FSMContext, ctx: Ctx) -> None:
    if await _alive(call, state) is None or (message := msg_of(call)) is None:
        return
    await call.answer()
    await _strip(call)
    await ask_date(message, state, ctx)


async def _after_date(message: Message, state: FSMContext, ctx: Ctx, day: date) -> None:
    data = await state.get_data()
    local = ctx.local_now()
    if problem := schedule.date_problem(ctx.shop, day, local):
        await message.answer("⚠️ " + esc(problem))
        await ask_date(message, state, ctx)
        return
    slots = schedule.slots_for(ctx.shop, data.get("method", "pickup"), day, local)
    if not slots:
        await message.answer("На эту дату свободных окон уже нет — выберите другую.")
        await ask_date(message, state, ctx)
        return
    await state.update_data(date=day.isoformat())
    await state.set_state(None)
    labels = [s.label for s in ctx.shop.slots]
    items = [(label, "a" if label == schedule.ASAP else str(labels.index(label))) for label in slots]
    await message.answer("⏰ Выберите время:", reply_markup=kb.co_slots(items))


@router.callback_query(Co.filter(F.act == "date"))
async def chose_date(call: CallbackQuery, callback_data: Co, state: FSMContext, ctx: Ctx) -> None:
    if await _alive(call, state) is None or (message := msg_of(call)) is None:
        return
    try:
        day = date.fromisoformat(callback_data.val)
    except ValueError:
        await call.answer("Неправильная дата.", show_alert=True)
        return
    await call.answer()
    await _strip(call)
    await _after_date(message, state, ctx, day)


@router.callback_query(Co.filter(F.act == "other_date"))
async def other_date(call: CallbackQuery, state: FSMContext, ctx: Ctx) -> None:
    if await _alive(call, state) is None or (message := msg_of(call)) is None:
        return
    await call.answer()
    await _strip(call)
    await state.set_state(Checkout.custom_date)
    await message.answer(f"📅 Напишите дату в виде 14.11 (можно на любой день в течение {ctx.shop.max_days_ahead} дней).")


@router.message(Checkout.custom_date, F.text)
async def got_custom_date(message: Message, state: FSMContext, ctx: Ctx) -> None:
    local = ctx.local_now()
    day = parse_user_date(message.text, local.date())
    if day is None:
        await message.answer("Не получилось распознать дату. Напишите так: 14.11")
        return
    if problem := schedule.date_problem(ctx.shop, day, local):  # остаёмся в режиме ввода: просим другую дату
        await message.answer("⚠️ " + esc(problem) + " Напишите другую дату, например 14.11")
        return
    await _after_date(message, state, ctx, day)


@router.callback_query(Co.filter(F.act == "slot"))
async def chose_slot(call: CallbackQuery, callback_data: Co, state: FSMContext, ctx: Ctx) -> None:
    data = await _alive(call, state)
    if data is None or (message := msg_of(call)) is None:
        return
    val = callback_data.val
    if val == "a":
        label = schedule.ASAP
    elif val.isdigit() and int(val) < len(ctx.shop.slots):
        label = ctx.shop.slots[int(val)].label
    else:
        await call.answer("Такого времени нет.", show_alert=True)
        return
    day = date.fromisoformat(data["date"])
    if not schedule.slot_valid(ctx.shop, data["method"], day, ctx.local_now(), label):
        await call.answer("Это время уже недоступно — выберите другое.", show_alert=True)
        await _strip(call)
        await _after_date(message, state, ctx, day)
        return
    await call.answer()
    await _strip(call)
    await state.update_data(slot=label)
    await go(message, call.from_user.id, state, ctx, "after_when")


# ───────────────────────── открытка / комментарий: только если нужно или попросили ─────────────────────────

async def after_when(message: Message, user_id: int, state: FSMContext, ctx: Ctx) -> None:
    """Комментарий спрашиваем лишь тогда, когда он нужен для товара (например, «какая песня?»). Иначе — сразу проверка."""
    data = await state.get_data()
    if not data.get("comment") and not data.get("_note_done"):
        prompt = next((l.product.note_prompt for l in catalog.cart_lines(ctx.db, user_id) if l.product.note_prompt), "")
        if prompt:
            await state.update_data(_note_done=True)
            await state.set_state(Checkout.comment)
            await message.answer("💌 " + esc(prompt), reply_markup=kb.co_skip("skip_note"))
            return
    await show_summary(message, user_id, state, ctx)


@router.callback_query(Co.filter(F.act == "note"))
async def ask_note(call: CallbackQuery, state: FSMContext) -> None:
    if await _alive(call, state) is None or (message := msg_of(call)) is None:
        return
    await call.answer()
    await _strip(call)
    await state.set_state(Checkout.comment)
    await message.answer("💌 Напишите текст открытки или комментарий к заказу — например, как найти подъезд.",
                         reply_markup=kb.co_skip("skip_note"))


@router.message(Checkout.comment, F.text)
async def got_comment(message: Message, state: FSMContext, ctx: Ctx) -> None:
    await state.update_data(comment=clean_text(message.text, 300))
    await show_summary(message, message.from_user.id, state, ctx)


@router.callback_query(Co.filter(F.act == "skip_note"))
async def skip_note(call: CallbackQuery, state: FSMContext, ctx: Ctx) -> None:
    if await _alive(call, state) is None or (message := msg_of(call)) is None:
        return
    await call.answer()
    await _strip(call)
    await show_summary(message, call.from_user.id, state, ctx)


# ───────────────────────── другой получатель ─────────────────────────

@router.callback_query(Co.filter(F.act == "rcp"))
async def recipient_start(call: CallbackQuery, state: FSMContext) -> None:
    if await _alive(call, state) is None or (message := msg_of(call)) is None:
        return
    await call.answer()
    await _strip(call)
    await state.set_state(Checkout.recipient_name)
    await message.answer("🎁 Как зовут получателя?", reply_markup=kb.kb([kb.btn("Получатель — я", Co(act="rcp_clear"))]))


@router.callback_query(Co.filter(F.act == "rcp_clear"))
async def recipient_clear(call: CallbackQuery, state: FSMContext, ctx: Ctx) -> None:
    if await _alive(call, state) is None or (message := msg_of(call)) is None:
        return
    await call.answer()
    await _strip(call)
    await state.update_data(recipient_name="", recipient_phone="")
    await show_summary(message, call.from_user.id, state, ctx)


@router.message(Checkout.recipient_name, F.text)
async def got_recipient_name(message: Message, state: FSMContext) -> None:
    name = clean_text(message.text, 60)
    if len(name) < 2 or not LETTERS.search(name):
        await message.answer("Напишите, пожалуйста, имя буквами 🙂")
        return
    await state.update_data(recipient_name=name)
    await state.set_state(Checkout.recipient_phone)
    await message.answer("📞 Телефон получателя (курьер позвонит, чтобы согласовать приезд):")


@router.message(Checkout.recipient_phone, F.text)
async def got_recipient_phone(message: Message, state: FSMContext, ctx: Ctx) -> None:
    phone = phone_from_input(message.text)
    if phone is None:
        await message.answer("Не получилось распознать номер. Напишите так: +7 900 123-45-67")
        return
    await state.update_data(recipient_phone=phone)
    await show_summary(message, message.from_user.id, state, ctx)


# ───────────────────────── проверка заказа ─────────────────────────

def _quote(ctx: Ctx, user_id: int, data: dict):
    return orders.quote_for(ctx.db, ctx.shop, user_id, method=data["method"], zone_name=data.get("zone", ""),
                            promo_code=data.get("promo", ""), cert_code=data.get("cert", ""),
                            today=ctx.local_now().date())


async def show_summary(message: Message, user_id: int, state: FSMContext, ctx: Ctx) -> None:
    await state.update_data(_back="")
    data = await state.get_data()
    local = ctx.local_now()
    day = date.fromisoformat(data["date"])
    q = _quote(ctx, user_id, data)
    if q.problems:
        await state.clear()
        await message.answer("⚠️ Корзина изменилась:\n" + "\n".join(f"• {esc(p)}" for p in q.problems)
                             + "\n\nОткройте корзину и поправьте её.", reply_markup=menu_for(ctx, user_id))
        return
    if q.promo_error:
        await state.update_data(promo="")
    if q.cert_error:
        await state.update_data(cert="")
    methods = orders.allowed_pay_methods(ctx.shop, ctx.online_enabled, day, local.date(), q.to_pay)
    await state.update_data(quote_total=q.total)
    await state.set_state(None)
    await message.answer(texts.checkout_summary(ctx.shop, data, q, day, local.date()), reply_markup=kb.co_summary(methods))


@router.callback_query(Co.filter(F.act == "edit"))
async def open_edit_menu(call: CallbackQuery, state: FSMContext, ctx: Ctx) -> None:
    """«✏️ Изменить или добавить»: то же сообщение, но кнопки — что можно поменять. Основной экран остаётся чистым."""
    data = await _alive(call, state)
    if data is None or (message := msg_of(call)) is None:
        return
    await call.answer()
    q = _quote(ctx, call.from_user.id, data)
    local = ctx.local_now()
    await safe_edit(message, texts.checkout_summary(ctx.shop, data, q, date.fromisoformat(data["date"]), local.date()),
                    kb.co_edit(data.get("method") == "delivery", bool(q.promo), bool(q.cert)))


@router.callback_query(Co.filter(F.act == "summary"))
async def back_to_summary(call: CallbackQuery, state: FSMContext, ctx: Ctx) -> None:
    data = await _alive(call, state)
    if data is None or (message := msg_of(call)) is None:
        return
    await call.answer()
    local = ctx.local_now()
    day = date.fromisoformat(data["date"])
    q = _quote(ctx, call.from_user.id, data)
    methods = orders.allowed_pay_methods(ctx.shop, ctx.online_enabled, day, local.date(), q.to_pay)
    await safe_edit(message, texts.checkout_summary(ctx.shop, data, q, day, local.date()), kb.co_summary(methods))


@router.callback_query(Co.filter(F.act == "code"))
async def ask_code(call: CallbackQuery, state: FSMContext) -> None:
    if await _alive(call, state) is None or (message := msg_of(call)) is None:
        return
    await call.answer()
    await _strip(call)
    await state.set_state(Checkout.code)
    await message.answer("🏷 Введите промокод или код подарочного сертификата (вида ABCD-EFGH-JKLM):")


@router.message(Checkout.code, F.text)
async def got_code(message: Message, state: FSMContext, ctx: Ctx) -> None:
    """Одно поле на два случая: код вида ABCD-EFGH-JKLM — сертификат, всё остальное — промокод."""
    text = clean_text(message.text, 60)
    uid = message.from_user.id
    data = await state.get_data()
    problem = ""
    if certs.normalize_code(text):
        cert, error = certs.check_code(ctx.db, uid, text)  # здесь же защита от подбора кодов
        if cert is None or error:
            problem = error or "Сертификат не найден."
        else:
            await state.update_data(cert=cert.code)
    elif not ctx.shop.promos:
        problem = "Это не похоже на код сертификата (он вида ABCD-EFGH-JKLM), а промокодов сейчас нет."
    else:
        trial = orders.quote_for(ctx.db, ctx.shop, uid, method=data["method"], zone_name=data.get("zone", ""),
                                 promo_code=text, cert_code=data.get("cert", ""), today=ctx.local_now().date())
        if trial.promo is None:
            problem = trial.promo_error or "Такого промокода нет."
        else:
            await state.update_data(promo=trial.promo.code)
    if problem:
        await message.answer("⚠️ " + esc(problem))
    await show_summary(message, uid, state, ctx)


@router.callback_query(Co.filter(F.act.in_({"drop_promo", "drop_cert"})))
async def drop_code(call: CallbackQuery, callback_data: Co, state: FSMContext, ctx: Ctx) -> None:
    if await _alive(call, state) is None or (message := msg_of(call)) is None:
        return
    await call.answer()
    await _strip(call)
    await state.update_data(**({"promo": ""} if callback_data.act == "drop_promo" else {"cert": ""}))
    await show_summary(message, call.from_user.id, state, ctx)


@router.callback_query(Co.filter(F.act == "cancel"))
async def cancel(call: CallbackQuery, state: FSMContext, ctx: Ctx) -> None:
    await call.answer()
    await _strip(call)
    await state.clear()
    if message := msg_of(call):
        await message.answer("Оформление отменено. Корзина сохранена — можно вернуться к ней в любой момент 🌸",
                             reply_markup=menu_for(ctx, call.from_user.id))


# ───────────────────────── оформление заказа ─────────────────────────

@router.callback_query(Co.filter(F.act == "pay"))
async def pay(call: CallbackQuery, callback_data: Co, state: FSMContext, ctx: Ctx, bot: Bot) -> None:
    data = await _alive(call, state)
    if data is None or (message := msg_of(call)) is None:
        return
    user = call.from_user
    if not all(data.get(key) for key in ("name", "phone", "method", "date", "slot")):  # например, посреди смены телефона
        await call.answer(STALE, show_alert=True)
        return
    try:
        draft = Draft(user_id=user.id, name=data["name"], phone=data["phone"], method=data["method"],
                      zone=data.get("zone", ""), address=data.get("address", ""),
                      recipient_name=data.get("recipient_name", ""), recipient_phone=data.get("recipient_phone", ""),
                      date=date.fromisoformat(data["date"]), slot=data["slot"], comment=data.get("comment", ""),
                      pay_method=callback_data.val, promo=data.get("promo", ""), cert_code=data.get("cert", ""))
    except KeyError:
        await call.answer(STALE, show_alert=True)
        return

    try:
        order_id = orders.place_order(ctx.db, ctx.shop, draft, now_utc=ctx.now(), online_enabled=ctx.online_enabled,
                                      expected_total=data.get("quote_total"))
    except PriceChanged as exc:
        await call.answer("Цена изменилась — проверьте новую сумму.", show_alert=True)
        await _strip(call)
        await state.update_data(quote_total=exc.new_total)
        await show_summary(message, user.id, state, ctx)
        return
    except OrderError as exc:
        if str(exc).startswith("Корзина пуста"):
            await call.answer("Этот заказ уже оформлен ✅ Статус — в разделе «Мои заказы».", show_alert=True)
            await _strip(call)
            await state.clear()
            return
        await call.answer()
        await message.answer("⚠️ " + esc(str(exc)))
        return

    await call.answer()
    await _strip(call)
    await state.clear()
    order = orders.get_order(ctx.db, order_id)
    assert order is not None

    markup, online_failed = None, False
    if order.pay_method == "online" and order.to_pay > 0:
        try:
            info = await billing.pay_for_order(ctx, order)
            markup = kb.pay_link(info.confirmation_url or "", info.id)
        except (YooKassaError, ValueError) as exc:
            online_failed = True
            log.error("Не удалось создать платёж для заказа %s: %s", order_id, exc)
    await message.answer(texts.order_created(order, ctx.shop, online_failed), reply_markup=markup)

    staff_text = "🆕 <b>Новый заказ</b>\n" + texts.order_card(order, ctx.shop, staff=True, username=user.username or "")
    if online_failed:
        staff_text += "\n\n⚠️ Ссылку на онлайн-оплату создать не удалось — отправьте клиенту ссылку вручную."
    await notify.to_staff(bot, ctx, staff_text, kb.order_staff(order))


# ───────────────────────── не текст там, где ждём текст ─────────────────────────

@router.message(Checkout.name)
@router.message(Checkout.phone)
@router.message(Checkout.address)
@router.message(Checkout.recipient_name)
@router.message(Checkout.recipient_phone)
@router.message(Checkout.custom_date)
@router.message(Checkout.comment)
@router.message(Checkout.code)
async def need_text(message: Message) -> None:
    await message.answer("Ответьте, пожалуйста, текстом 🙂")
