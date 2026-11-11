"""Подарочные сертификаты: выбрать сумму, оплатить, получить электронную версию, проверить баланс."""
from __future__ import annotations

import logging
import re
from datetime import timedelta

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, User

from .. import billing, certs, crm, keyboards as kb, orders, texts
from ..callbacks import Ce
from ..ctx import Ctx
from ..states import CertFlow
from ..ui import menu_for, msg_of
from ..util import db_time, esc, phone_from_input, rub
from ..yookassa import YooKassaError
from . import manager

log = logging.getLogger(__name__)
router = Router(name="gifts")
PAYMENTS_PER_HOUR = 5


def menu_text(ctx: Ctx) -> str:
    shop = ctx.shop
    how = ("Оплатите картой или через СБП — электронный сертификат придёт сюда сразу после оплаты."
           if ctx.online_enabled else
           "Выберите сумму — менеджер пришлёт реквизиты для оплаты, а электронный сертификат придёт сюда после оплаты.")
    return ("🎁 <b>Подарочный сертификат</b>\n\nПодарите возможность выбрать самому 🌸\n\n"
            f"• {how}\n• Его можно переслать получателю — это картинка с кодом.\n"
            f"• Действует {shop.cert_valid_days} дней, на любые букеты и растения.\n"
            "• Им можно оплатить заказ в боте или в магазине — полностью или частично.\n\n"
            "Выберите сумму:")


@router.message(F.text == texts.BTN_CERT)
async def open_menu(message: Message, ctx: Ctx) -> None:
    await message.answer(menu_text(ctx), reply_markup=kb.certs_menu(ctx.shop))


@router.callback_query(Ce.filter(F.act == "menu"))
async def menu(call: CallbackQuery, ctx: Ctx) -> None:
    await call.answer()
    if message := msg_of(call):
        await message.answer(menu_text(ctx), reply_markup=kb.certs_menu(ctx.shop))


@router.callback_query(Ce.filter(F.act == "buy"))
async def buy(call: CallbackQuery, callback_data: Ce, state: FSMContext, ctx: Ctx, bot: Bot) -> None:
    # сумму берём только из настроек магазина, а не из того, что пришло с кнопки
    if callback_data.val not in ctx.shop.cert_amounts:
        await call.answer("Такой суммы нет. Выберите из списка.", show_alert=True)
        return
    await call.answer()
    if message := msg_of(call):
        await begin_purchase(message, call.from_user, callback_data.val, ctx, bot, state)


@router.callback_query(Ce.filter(F.act == "other"))
async def other_amount(call: CallbackQuery, state: FSMContext, ctx: Ctx) -> None:
    await call.answer()
    if message := msg_of(call):
        await state.set_state(CertFlow.amount)
        await message.answer(f"✏️ Напишите сумму сертификата числом — от {rub(ctx.shop.cert_min)} до {rub(ctx.shop.cert_max)}."
                             "\n\n<i>Передумали? Нажмите любую кнопку меню.</i>")


@router.message(CertFlow.amount, F.text)
async def got_amount(message: Message, state: FSMContext, ctx: Ctx, bot: Bot) -> None:
    digits = re.sub(r"\D", "", message.text)
    amount = int(digits) if digits and len(digits) <= 7 else 0
    if not ctx.shop.cert_min <= amount <= ctx.shop.cert_max:
        await message.answer(f"Сумма должна быть от {rub(ctx.shop.cert_min)} до {rub(ctx.shop.cert_max)}. Напишите число.")
        return
    await state.clear()
    await begin_purchase(message, message.from_user, amount, ctx, bot, state)


async def begin_purchase(message: Message, user: User, amount: int, ctx: Ctx, bot: Bot, state: FSMContext) -> None:
    if not ctx.online_enabled:
        lead_id = await manager.create_lead(
            bot, ctx, user, "certificate", f"Хочу купить подарочный сертификат на {rub(amount)}. "
                                           "Нужны реквизиты или ссылка для оплаты.", amount=amount)
        await message.answer(manager.lead_ok(ctx) if lead_id else manager.lead_limit(ctx),
                             reply_markup=menu_for(ctx, user.id))
        return
    profile = crm.get_user(ctx.db, user.id)
    phone = profile["phone"] if profile else ""
    if not phone and ctx.yookassa and ctx.yookassa.receipt:
        await state.set_state(CertFlow.phone)
        await state.update_data(amount=amount)
        await message.answer("📞 Для чека об оплате нужен ваш телефон: нажмите «Поделиться номером» или напишите номер.",
                             reply_markup=kb.contact_request())
        return
    await create_payment(message, user, amount, phone, ctx)


@router.message(CertFlow.phone, F.contact)
async def got_contact(message: Message, state: FSMContext, ctx: Ctx) -> None:
    if message.contact.user_id != message.from_user.id:
        await message.answer("Пришлите, пожалуйста, свой номер — кнопкой «Поделиться номером» или текстом.")
        return
    await _got_phone(message, state, ctx, message.contact.phone_number)


@router.message(CertFlow.phone, F.text)
async def got_phone(message: Message, state: FSMContext, ctx: Ctx) -> None:
    await _got_phone(message, state, ctx, message.text)


async def _got_phone(message: Message, state: FSMContext, ctx: Ctx, raw: str) -> None:
    phone = phone_from_input(raw)
    if phone is None:
        await message.answer("Не получилось распознать номер. Напишите так: +7 900 123-45-67")
        return
    data = await state.get_data()
    await state.clear()
    crm.save_contact(ctx.db, message.from_user.id, phone=phone)
    await message.answer("Телефон сохранён ✅", reply_markup=menu_for(ctx, message.from_user.id))
    await create_payment(message, message.from_user, int(data.get("amount", 0)), phone, ctx)


async def create_payment(message: Message, user: User, amount: int, phone: str, ctx: Ctx) -> None:
    since = db_time(ctx.now() - timedelta(hours=1))
    recent = orders.count_recent_payments(ctx.db, user.id, "cert", since)
    if recent >= PAYMENTS_PER_HOUR:
        await message.answer("Вы уже создали несколько платежей подряд. Оплатите один из них или напишите менеджеру 🙏")
        return
    if not ctx.shop.cert_min <= amount <= ctx.shop.cert_max:
        await message.answer("Сумма сертификата указана неверно. Откройте раздел «🎁 Сертификат» и выберите сумму заново.")
        return
    try:
        info = await billing.pay_for_certificate(ctx, user.id, amount, phone, attempt=recent + 1)
    except (YooKassaError, ValueError) as exc:
        log.error("Не удалось создать платёж за сертификат: %s", exc)
        await message.answer("Платёжная система сейчас недоступна. Попробуйте позже или напишите менеджеру — он поможет 🙏")
        return
    await message.answer(
        f"🎁 <b>Сертификат на {rub(amount)}</b>\n\nНажмите «Оплатить» — откроется защищённая страница ЮKassa. "
        "После оплаты сертификат придёт сюда автоматически.", reply_markup=kb.pay_link(info.confirmation_url or "", info.id))


# ───────────────────────── проверить баланс ─────────────────────────

@router.callback_query(Ce.filter(F.act == "balance"))
async def balance_start(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    if message := msg_of(call):
        await state.set_state(CertFlow.balance)
        await message.answer("🔑 Введите код сертификата (вида ABCD-EFGH-JKLM):\n\n<i>Передумали? Нажмите любую кнопку меню.</i>")


@router.message(CertFlow.balance, F.text)
async def balance_code(message: Message, state: FSMContext, ctx: Ctx) -> None:
    cert, error = certs.check_code(ctx.db, message.from_user.id, message.text)
    if cert is None:
        await message.answer("⚠️ " + esc(error))
        return
    await state.clear()
    if error:
        await message.answer(f"Сертификат {esc(cert.code)}: {esc(error)}", reply_markup=menu_for(ctx, message.from_user.id))
        return
    await message.answer(f"🎁 Сертификат <code>{cert.code}</code>\nБаланс: <b>{rub(cert.balance)}</b> из {rub(cert.amount)}\n"
                         f"Действует до {cert.expires:%d.%m.%Y}", reply_markup=menu_for(ctx, message.from_user.id))


@router.message(CertFlow.amount)
@router.message(CertFlow.phone)
@router.message(CertFlow.balance)
async def need_text(message: Message) -> None:
    await message.answer("Ответьте, пожалуйста, текстом 🙂")
