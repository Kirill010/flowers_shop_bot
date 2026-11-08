"""Оформление заказа: имя → телефон → доставка или самовывоз → адрес → получатель → дата → время →
комментарий → проверка → способ оплаты.

В состоянии диалога хранится только то, что ввёл клиент. Цены, скидки и итог каждый раз считаются заново
из базы (orders.quote_for), поэтому подменить сумму через кнопку нельзя."""
from __future__ import annotations

import logging
import re
from datetime import date

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, User

from .. import billing, certs, crm, keyboards as kb, notify, orders, schedule, texts
from ..callbacks import Co
from ..ctx import Ctx
from ..orders import Draft, OrderError, PriceChanged
from ..states import Checkout
from ..ui import msg_of
from ..util import clean_text, esc, format_phone, parse_user_date, phone_from_input
from ..yookassa import YooKassaError
from . import manager

log = logging.getLogger(__name__)
router = Router(name="checkout")
STALE = "Это окно устарело. Откройте корзину и начните оформление заново 🙂"
HINT = "\n\n<i>Передумали? Нажмите любую кнопку меню.</i>"
LETTERS = re.compile(r"[A-Za-zА-Яа-яЁё]")


# ───────────────────────── вход ─────────────────────────

async def begin(message: Message, user: User, state: FSMContext, ctx: Ctx) -> None:
    """Начало оформления (кнопка «Перейти к оплате» в корзине)."""
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
                             reply_markup=kb.co_saved(profile["name"], profile["phone"]))
    else:
        await ask_name(message, state)


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


async def ask_name(message: Message, state: FSMContext) -> None:
    await state.set_state(Checkout.name)
    await message.answer("👤 Как вас зовут? Напишите имя." + HINT)


async def ask_method(message: Message, state: FSMContext, ctx: Ctx) -> None:
    await state.set_state(None)
    await message.answer("📦 Как получите заказ?", reply_markup=kb.co_method(ctx.shop))


async def ask_date(message: Message, state: FSMContext, ctx: Ctx) -> None:
    data = await state.get_data()
    local = ctx.local_now()
    days = schedule.date_choices(ctx.shop, data.get("method", "pickup"), local)
    await state.set_state(None)
    note = "\nℹ️ Заказ на другую дату — только по предоплате." if ctx.shop.prepay_future_orders else ""
    verb = "доставить" if data.get("method") == "delivery" else "забрать"
    await message.answer(f"🗓 На какую дату {verb} заказ?{note}", reply_markup=kb.co_dates(days, local.date()))


# ───────────────────────── имя и телефон ─────────────────────────

@router.callback_query(Co.filter(F.act == "saved"))
async def use_saved(call: CallbackQuery, state: FSMContext, ctx: Ctx) -> None:
    if await _alive(call, state) is None or (message := msg_of(call)) is None:
        return
    profile = crm.get_user(ctx.db, call.from_user.id)
    await call.answer()
    await _strip(call)
    if not profile or not profile["name"] or not profile["phone"]:
        await ask_name(message, state)
        return
    await state.update_data(name=profile["name"], phone=profile["phone"])
    await ask_method(message, state, ctx)


@router.callback_query(Co.filter(F.act == "edit"))
async def edit_saved(call: CallbackQuery, state: FSMContext) -> None:
    if await _alive(call, state) is None or (message := msg_of(call)) is None:
        return
    await call.answer()
    await _strip(call)
    await ask_name(message, state)


@router.message(Checkout.name, F.text)
async def got_name(message: Message, state: FSMContext) -> None:
    name = clean_text(message.text, 60)
    if len(name) < 2 or not LETTERS.search(name):
        await message.answer("Напишите, пожалуйста, имя буквами 🙂")
        return
    await state.update_data(name=name)
    await state.set_state(Checkout.phone)
    await message.answer("📞 Телефон для связи: нажмите «Поделиться номером» или напишите номер.",
                         reply_markup=kb.contact_request())


@router.message(Checkout.phone, F.contact)
async def got_contact(message: Message, state: FSMContext, ctx: Ctx) -> None:
    if message.contact.user_id != message.from_user.id:
        await message.answer("Пришлите, пожалуйста, свой номер — кнопкой «Поделиться номером» или текстом.")
        return
    await _phone_ok(message, state, ctx, message.contact.phone_number)


@router.message(Checkout.phone, F.text)
async def got_phone(message: Message, state: FSMContext, ctx: Ctx) -> None:
    await _phone_ok(message, state, ctx, message.text)


async def _phone_ok(message: Message, state: FSMContext, ctx: Ctx, raw: str) -> None:
    phone = phone_from_input(raw)
    if phone is None:
        await message.answer("Не получилось распознать номер. Напишите так: +7 900 123-45-67")
        return
    data = await state.get_data()
    await state.update_data(phone=phone)
    crm.save_contact(ctx.db, message.from_user.id, name=data.get("name"), phone=phone)
    await message.answer("Телефон сохранён ✅", reply_markup=kb.main_menu())  # возвращаем главное меню вместо «Поделиться»
    await ask_method(message, state, ctx)


# ───────────────────────── доставка / самовывоз ─────────────────────────

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
    await state.update_data(method=method, zone="", address="", recipient_name="", recipient_phone="")
    if method == "delivery":
        await message.answer("🚚 Куда доставить?\n" + esc(ctx.shop.area_text), reply_markup=kb.co_zones(ctx.shop))
    else:
        await ask_date(message, state, ctx)


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
    await state.set_state(Checkout.address)
    await message.answer("🏠 Напишите адрес доставки: улица, дом, подъезд, этаж, квартира." + HINT)


@router.callback_query(Co.filter(F.act == "outside"))
async def outside(call: CallbackQuery, state: FSMContext, ctx: Ctx) -> None:
    if await _alive(call, state) is None or (message := msg_of(call)) is None:
        return
    await call.answer()
    await _strip(call)
    await state.clear()
    await manager.start_ask(message, state, ctx, "outside", user_id=call.from_user.id)


@router.message(Checkout.address, F.text)
async def got_address(message: Message, state: FSMContext) -> None:
    address = clean_text(message.text, 200)
    if len(address) < 6:
        await message.answer("Напишите адрес полностью: улица, дом, квартира 🙂")
        return
    await state.update_data(address=address)
    await state.set_state(None)
    await message.answer("Кто получит заказ?", reply_markup=kb.co_recipient())


@router.callback_query(Co.filter(F.act == "rcp"))
async def recipient_choice(call: CallbackQuery, callback_data: Co, state: FSMContext, ctx: Ctx) -> None:
    if await _alive(call, state) is None or (message := msg_of(call)) is None:
        return
    await call.answer()
    await _strip(call)
    if callback_data.val == "other":
        await state.set_state(Checkout.recipient_name)
        await message.answer("🎁 Как зовут получателя?" + HINT)
    else:
        await state.update_data(recipient_name="", recipient_phone="")
        await ask_date(message, state, ctx)


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
    await ask_date(message, state, ctx)


# ───────────────────────── дата и время ─────────────────────────

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
    await message.answer(f"📅 Напишите дату в виде 14.11 (можно на любой день в течение {ctx.shop.max_days_ahead} дней)." + HINT)


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


@router.callback_query(Co.filter(F.act == "back_date"))
async def back_date(call: CallbackQuery, state: FSMContext, ctx: Ctx) -> None:
    if await _alive(call, state) is None or (message := msg_of(call)) is None:
        return
    await call.answer()
    await _strip(call)
    await ask_date(message, state, ctx)


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
    await state.set_state(Checkout.comment)
    await message.answer("💬 Комментарий к заказу: текст открытки, пожелания, название песни для музыкальной открытки, "
                         "как найти подъезд. Можно пропустить.", reply_markup=kb.co_skip("skip_comment"))


@router.message(Checkout.comment, F.text)
async def got_comment(message: Message, state: FSMContext, ctx: Ctx) -> None:
    await state.update_data(comment=clean_text(message.text, 300))
    await show_summary(message, message.from_user.id, state, ctx)


@router.callback_query(Co.filter(F.act == "skip_comment"))
async def skip_comment(call: CallbackQuery, state: FSMContext, ctx: Ctx) -> None:
    if await _alive(call, state) is None or (message := msg_of(call)) is None:
        return
    await call.answer()
    await _strip(call)
    await state.update_data(comment="")
    await show_summary(message, call.from_user.id, state, ctx)


# ───────────────────────── проверка заказа, промокод, сертификат ─────────────────────────

async def show_summary(message: Message, user_id: int, state: FSMContext, ctx: Ctx) -> None:
    data = await state.get_data()
    local = ctx.local_now()
    day = date.fromisoformat(data["date"])
    q = orders.quote_for(ctx.db, ctx.shop, user_id, method=data["method"], zone_name=data.get("zone", ""),
                         promo_code=data.get("promo", ""), cert_code=data.get("cert", ""), today=local.date())
    if q.problems:
        await state.clear()
        await message.answer("⚠️ Корзина изменилась:\n" + "\n".join(f"• {esc(p)}" for p in q.problems)
                             + "\n\nОткройте корзину и поправьте её.", reply_markup=kb.main_menu())
        return
    if q.promo_error:
        await state.update_data(promo="")
    if q.cert_error:
        await state.update_data(cert="")
    methods = orders.allowed_pay_methods(ctx.shop, ctx.online_enabled, day, local.date(), q.to_pay)
    await state.update_data(quote_total=q.total)
    await state.set_state(None)
    await message.answer(texts.checkout_summary(ctx.shop, data, q, day, local.date()),
                         reply_markup=kb.co_summary(methods, has_promo=bool(q.promo), has_cert=bool(q.cert)))


@router.callback_query(Co.filter(F.act.in_({"promo", "cert"})))
async def ask_code(call: CallbackQuery, callback_data: Co, state: FSMContext) -> None:
    if await _alive(call, state) is None or (message := msg_of(call)) is None:
        return
    await call.answer()
    await _strip(call)
    if callback_data.act == "promo":
        await state.set_state(Checkout.promo)
        await message.answer("🏷 Введите промокод:" + HINT)
    else:
        await state.set_state(Checkout.cert)
        await message.answer("🎁 Введите код подарочного сертификата (вида ABCD-EFGH-JKLM):" + HINT)


@router.callback_query(Co.filter(F.act.in_({"promo_off", "cert_off"})))
async def drop_code(call: CallbackQuery, callback_data: Co, state: FSMContext, ctx: Ctx) -> None:
    if await _alive(call, state) is None or (message := msg_of(call)) is None:
        return
    await call.answer()
    await _strip(call)
    await state.update_data(**({"promo": ""} if callback_data.act == "promo_off" else {"cert": ""}))
    await show_summary(message, call.from_user.id, state, ctx)


@router.message(Checkout.promo, F.text)
async def got_promo(message: Message, state: FSMContext, ctx: Ctx) -> None:
    code = clean_text(message.text, 40)
    data = await state.get_data()
    trial = orders.quote_for(ctx.db, ctx.shop, message.from_user.id, method=data["method"], zone_name=data.get("zone", ""),
                             promo_code=code, cert_code=data.get("cert", ""), today=ctx.local_now().date())
    if trial.promo is None:
        await message.answer("⚠️ " + esc(trial.promo_error or "Промокод не подошёл."))
    else:
        await state.update_data(promo=trial.promo.code)
    await show_summary(message, message.from_user.id, state, ctx)


@router.message(Checkout.cert, F.text)
async def got_cert(message: Message, state: FSMContext, ctx: Ctx) -> None:
    cert, error = certs.check_code(ctx.db, message.from_user.id, message.text)
    if cert is None or error:
        await message.answer("⚠️ " + esc(error or "Сертификат не найден."))
    else:
        await state.update_data(cert=cert.code)
    await show_summary(message, message.from_user.id, state, ctx)


@router.callback_query(Co.filter(F.act == "restart"))
async def restart(call: CallbackQuery, state: FSMContext, ctx: Ctx) -> None:
    if await _alive(call, state) is None or (message := msg_of(call)) is None:
        return
    await call.answer()
    await _strip(call)
    await begin(message, call.from_user, state, ctx)


@router.callback_query(Co.filter(F.act == "cancel"))
async def cancel(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _strip(call)
    await state.clear()
    if message := msg_of(call):
        await message.answer("Оформление отменено. Корзина сохранена — можно вернуться к ней в любой момент 🌸",
                             reply_markup=kb.main_menu())


# ───────────────────────── оформление заказа ─────────────────────────

@router.callback_query(Co.filter(F.act == "pay"))
async def pay(call: CallbackQuery, callback_data: Co, state: FSMContext, ctx: Ctx, bot: Bot) -> None:
    data = await _alive(call, state)
    if data is None or (message := msg_of(call)) is None:
        return
    user = call.from_user
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
@router.message(Checkout.promo)
@router.message(Checkout.cert)
async def need_text(message: Message) -> None:
    await message.answer("Ответьте, пожалуйста, текстом 🙂")
