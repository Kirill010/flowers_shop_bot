"""Напоминания сотрудникам: заказ или обращение ждёт ответа дольше, чем мы обещали клиенту («в течение 15 минут»).

Проверка раз в минуту и только в рабочие часы: ночью никого не будим, а утром, когда магазин открылся, напоминаем
про всё, что накопилось за ночь. На каждое дело — не больше двух напоминаний (через 15 минут и через час),
чтобы напоминания не превратились в шум. Если ждут несколько дел сразу — одно сообщение со списком, а не россыпь."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from sqlite3 import Row

from aiogram import Bot

from . import crm, keyboards as kb, notify, orders, texts
from .callbacks import Adm
from .ctx import Ctx
from .util import parse_db_time

log = logging.getLogger(__name__)
CHECK_SECONDS = 60
URGENT_MINUTES = 5  # «Срочный заказ» ждать не должен
REPEAT_FACTOR = 4  # повторное напоминание — через столько «обещанных» сроков (15 минут → через час)
LIMIT = 50  # сколько самых свежих дел проверяем за раз


@dataclass
class Waiting:
    """Дело, которое ждёт сотрудника: заказ без подтверждения или обращение без ответа."""
    key: tuple[str, int]
    minutes: int  # сколько уже ждёт
    level: int  # какого по счёту напоминания заслуживает: 0 — ещё рано, 1 — первое, 2 — повторное
    order: orders.Order | None = None
    lead: Row | None = None


def _level(minutes: int, promise: int) -> int:
    if minutes >= promise * REPEAT_FACTOR:
        return 2
    return 1 if minutes >= promise else 0


def _minutes_since(ctx: Ctx, created_at: str) -> int:
    return max(0, int((ctx.now() - parse_db_time(created_at)).total_seconds() // 60))


def pending(ctx: Ctx) -> list[Waiting]:
    """Всё, что ждёт сотрудников. Онлайн-заказ, который клиент ещё не оплатил, сюда не входит: ему платить, а не вам."""
    promise = ctx.shop.response_minutes
    items: list[Waiting] = []
    for order in orders.list_orders(ctx.db, statuses=("new",), limit=LIMIT):
        if order.pay_method == "online" and order.pay_status == "pending":
            continue
        minutes = _minutes_since(ctx, order.created_at)
        items.append(Waiting(("order", order.id), minutes, _level(minutes, promise), order=order))
    for lead in crm.list_leads(ctx.db, "new", LIMIT):
        minutes = _minutes_since(ctx, lead["created_at"])
        need = URGENT_MINUTES if lead["kind"] == "urgent" else promise
        items.append(Waiting(("lead", lead["id"]), minutes, _level(minutes, need), lead=lead))
    return items


async def _send_one(ctx: Ctx, bot: Bot, item: Waiting) -> None:
    from .handlers import admin  # карточки для сотрудников собираются там же, где и в панели

    waited = texts.waiting(item.minutes)
    if item.order is not None:
        text = (f"⏰ <b>Заказ №{item.order.id} ждёт подтверждения уже {waited}</b>\n\n"
                + admin.order_for_staff(ctx, item.order))
        markup = kb.order_staff(item.order)
    else:
        text = f"⏰ <b>Обращение ждёт ответа уже {waited}</b>\n\n" + admin.lead_for_staff(ctx, item.lead)
        markup = kb.lead(item.lead)
    await notify.to_staff(bot, ctx, text, markup)


async def _send_digest(ctx: Ctx, bot: Bot, due: list[Waiting]) -> None:
    n_orders = sum(1 for item in due if item.order is not None)
    n_leads = len(due) - n_orders
    lines = ["⏰ <b>Ждут ответа</b>"]
    buttons = []
    if n_orders:
        lines.append(f"📦 заказов: {n_orders}")
        buttons.append(kb.btn("📦 Открыть заказы", Adm(act="list", arg="new")))
    if n_leads:
        lines.append(f"📬 обращений: {n_leads}")
        buttons.append(kb.btn("📬 Открыть обращения", Adm(act="leads")))
    lines.append(f"Дольше всех — {texts.waiting(max(item.minutes for item in due))}.")
    await notify.to_staff(bot, ctx, "\n".join(lines), kb.kb(buttons))


async def remind_once(ctx: Ctx, bot: Bot) -> int:
    """Одна проверка. Возвращает, о скольких делах напомнили."""
    items = pending(ctx)
    alive = {item.key for item in items}
    for key in [k for k in ctx.nudged if k not in alive]:  # дело сделано — забываем про него
        del ctx.nudged[key]
    if not ctx.is_open:  # ночью не будим: утром напомним про всё сразу
        return 0
    due = [item for item in items if item.level > ctx.nudged.get(item.key, 0)]
    if not due:
        return 0
    if len(due) == 1:
        await _send_one(ctx, bot, due[0])
    else:
        await _send_digest(ctx, bot, due)
    for item in due:  # отмечаем даже если сообщение не дошло: в журнале уже есть ошибка, а повторять каждую минуту не нужно
        ctx.nudged[item.key] = item.level
    return len(due)


async def reminders_loop(ctx: Ctx, bot: Bot, interval: float = CHECK_SECONDS) -> None:
    """Фоновая задача: раз в минуту смотрит, не заждался ли кто-нибудь."""
    while True:
        await asyncio.sleep(interval)
        try:
            await remind_once(ctx, bot)
        except Exception:
            log.exception("Ошибка при проверке ожидающих заказов и обращений")
