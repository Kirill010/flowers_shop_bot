"""Онлайн-платежи: создание ссылки, проверка статуса, выполнение заказа или выпуск сертификата."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta
from sqlite3 import Row

from aiogram import Bot

from . import certs, notify, orders
from .ctx import Ctx
from .util import parse_db_time, rub
from .yookassa import PaymentInfo, YooKassaError, certificate_receipt_items, order_receipt_items

log = logging.getLogger(__name__)
POLL_SECONDS = 30
GIVE_UP_AFTER = timedelta(days=3)


async def pay_for_order(ctx: Ctx, order: orders.Order) -> PaymentInfo:
    """Создаёт платёж за заказ. Ключ идемпотентности привязан к заказу: повторный вызов не создаст второй платёж."""
    assert ctx.yookassa is not None
    info = await ctx.yookassa.create_payment(
        amount=order.to_pay, description=f"Заказ №{order.id}", return_url=f"https://t.me/{ctx.bot_username}",
        metadata={"order_id": order.id, "user_id": order.user_id}, idempotence_key=f"order-{order.id}",
        customer_phone=order.phone, items=order_receipt_items(order))
    orders.add_payment(ctx.db, info.id, "order", order.user_id, order.to_pay, order_id=order.id)
    return info


async def pay_for_certificate(ctx: Ctx, user_id: int, amount: int, phone: str, attempt: int) -> PaymentInfo:
    assert ctx.yookassa is not None
    info = await ctx.yookassa.create_payment(
        amount=amount, description=f"Подарочный сертификат на {amount} руб.",
        return_url=f"https://t.me/{ctx.bot_username}", metadata={"kind": "certificate", "user_id": user_id},
        idempotence_key=f"cert-{user_id}-{ctx.now():%Y%m%d%H%M%S}-{attempt}", customer_phone=phone,
        items=certificate_receipt_items(amount))
    orders.add_payment(ctx.db, info.id, "cert", user_id, amount, payload={"amount": amount})
    return info


async def fulfill_order_payment(ctx: Ctx, bot: Bot, order_id: int) -> None:
    order = orders.get_order(ctx.db, order_id)
    if order is None or not orders.mark_paid(ctx.db, order_id):
        return
    if order.status == "cancelled":
        await notify.to_staff(bot, ctx, f"⚠️ <b>Оплата пришла по ОТМЕНЁННОМУ заказу №{order_id}</b> ({rub(order.to_pay)}). "
                                        "Верните деньги в кабинете ЮKassa или восстановите заказ вручную.")
        await notify.to_user(bot, order.user_id, f"Мы получили оплату по заказу №{order_id}, но он был отменён. "
                                                 "Менеджер свяжется с вами и всё уладит.")
        return
    await notify.to_user(bot, order.user_id, f"✅ Оплата заказа №{order_id} получена ({rub(order.to_pay)}). Спасибо!")
    await notify.to_staff(bot, ctx, f"💰 Заказ №{order_id} оплачен онлайн — {rub(order.to_pay)}.")


async def fulfill_certificate(ctx: Ctx, bot: Bot, row: Row) -> None:
    cert = certs.issue(ctx.db, ctx.shop, row["amount"], buyer_id=row["user_id"], payment_id=row["payment_id"],
                       note="Продан онлайн")
    if not await notify.send_certificate(bot, ctx, cert, row["user_id"]):
        await notify.to_staff(bot, ctx, f"⚠️ Сертификат {cert.code} на {rub(cert.amount)} оплачен, но клиенту "
                                        f"(id {row['user_id']}) отправить не удалось. Передайте код вручную.")
    await notify.to_staff(bot, ctx, f"🎁 Продан сертификат на {rub(cert.amount)} (онлайн). Код: <code>{cert.code}</code>")


async def poll_payment(ctx: Ctx, bot: Bot, row: Row) -> str:
    """Спрашивает ЮKassa о платеже. Если оплачен — выполняет заказ или выпускает сертификат (ровно один раз)."""
    if ctx.yookassa is None:
        return row["status"]
    pid = row["payment_id"]
    info = await ctx.yookassa.get_payment(pid)

    if info.status == "succeeded" and info.paid:
        mismatch = info.amount != row["amount"] or (
            row["kind"] == "order" and str(info.metadata.get("order_id", "")) != str(row["order_id"]))
        if mismatch:
            if orders.finish_payment(ctx.db, pid, "mismatch"):
                await notify.to_staff(bot, ctx, f"⚠️ Платёж {pid}: сумма или номер заказа не совпали с ожидаемыми "
                                                f"({row['amount']} ₽). Проверьте в кабинете ЮKassa, автоматически ничего не сделано.")
            return "mismatch"
        if orders.finish_payment(ctx.db, pid, "succeeded"):
            if row["kind"] == "order":
                await fulfill_order_payment(ctx, bot, row["order_id"])
            else:
                await fulfill_certificate(ctx, bot, row)
        return "succeeded"

    if info.status == "canceled":
        if orders.finish_payment(ctx.db, pid, "canceled"):
            if row["kind"] == "order":
                orders.set_pay_status(ctx.db, row["order_id"], "failed")
                await notify.to_staff(bot, ctx, f"⚠️ Платёж по заказу №{row['order_id']} не прошёл или истёк. "
                                                "Свяжитесь с клиентом: предложите другой способ оплаты.")
                await notify.to_user(bot, row["user_id"], f"Платёж по заказу №{row['order_id']} не прошёл или истёк. "
                                                          "Менеджер свяжется с вами, чтобы подсказать, как оплатить.")
            else:
                await notify.to_user(bot, row["user_id"], "Платёж за сертификат не прошёл или истёк. "
                                                          "Попробуйте ещё раз в разделе «🎁 Сертификат».")
        return "canceled"

    if ctx.now() - parse_db_time(row["created_at"]) > GIVE_UP_AFTER:
        orders.finish_payment(ctx.db, pid, "expired")
        return "expired"
    return "pending"


async def poll_all(ctx: Ctx, bot: Bot) -> None:
    for row in orders.pending_payments(ctx.db):
        try:
            await poll_payment(ctx, bot, row)
        except YooKassaError as exc:
            log.warning("Не удалось проверить платёж %s: %s", row["payment_id"], exc)
        except Exception:  # один плохой платёж не должен останавливать проверку остальных
            log.exception("Ошибка при проверке платежа %s", row["payment_id"])


async def payments_loop(ctx: Ctx, bot: Bot, interval: float = POLL_SECONDS) -> None:
    """Фоновая задача: раз в полминуты проверяет ожидающие платежи."""
    while True:
        await asyncio.sleep(interval)
        if ctx.yookassa is not None:
            await poll_all(ctx, bot)
