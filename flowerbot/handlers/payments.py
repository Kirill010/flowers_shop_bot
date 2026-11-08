"""Кнопка «Проверить оплату» под ссылкой на оплату."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery

from .. import billing, orders
from ..callbacks import Pay
from ..ctx import Ctx
from ..yookassa import YooKassaError

router = Router(name="payments")

ANSWERS = {
    "succeeded": "✅ Оплата получена — спасибо!",
    "pending": "Платёж пока не завершён. Если вы уже оплатили — подождите минуту и нажмите снова.",
    "canceled": "Платёж не прошёл или истёк. Напишите менеджеру — подскажем, как оплатить.",
    "expired": "Время оплаты истекло. Напишите менеджеру — подскажем, как оплатить.",
    "mismatch": "Платёж нужно проверить вручную — менеджер уже уведомлён.",
}


@router.callback_query(Pay.filter(F.act == "check"))
async def check(call: CallbackQuery, callback_data: Pay, ctx: Ctx, bot: Bot) -> None:
    row = orders.get_payment(ctx.db, callback_data.id)
    if row is None or row["user_id"] != call.from_user.id:  # чужие платежи не показываем
        await call.answer("Платёж не найден.", show_alert=True)
        return
    status = row["status"]
    if status == "pending":
        try:
            status = await billing.poll_payment(ctx, bot, row)
        except YooKassaError:
            await call.answer("Не удалось связаться с платёжной системой. Попробуйте через минуту.", show_alert=True)
            return
    await call.answer(ANSWERS.get(status, "Статус платежа уточняется."), show_alert=True)
