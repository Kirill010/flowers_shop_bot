"""Когда можно получить заказ: даты и окна времени с учётом часов работы и запаса на сборку."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from .config import Shop, Slot

ASAP = "Как можно скорее"


def local_now(shop: Shop, now_utc: datetime) -> datetime:
    return now_utc.astimezone(shop.tz)


def is_open(shop: Shop, local: datetime) -> bool:
    return shop.open <= local.time() < shop.close


def slots_for(shop: Shop, method: str, day: date, local: datetime) -> list[str]:
    """Подписи окон, доступные на эту дату. Для сегодняшнего дня — только те, что ещё успеваем."""
    if day < local.date() or day > local.date() + timedelta(days=shop.max_days_ahead):
        return []
    if day > local.date():
        return [s.label for s in shop.slots]

    result: list[str] = []
    if method == "pickup":
        ready = local + timedelta(minutes=shop.pickup_lead_minutes)
        if is_open(shop, local) and ready.time() < shop.close and ready.date() == day:
            result.append(ASAP)
        for s in shop.slots:
            if datetime.combine(day, s.end, tzinfo=local.tzinfo) > ready:
                result.append(s.label)
    else:
        lead = timedelta(minutes=shop.delivery_lead_minutes)
        for s in shop.slots:
            if datetime.combine(day, s.start, tzinfo=local.tzinfo) - local >= lead:
                result.append(s.label)
    return result


def date_choices(shop: Shop, method: str, local: datetime, count: int = 7) -> list[date]:
    """Ближайшие даты, на которые ещё есть свободные окна (для кнопок)."""
    days: list[date] = []
    for offset in range(0, min(shop.max_days_ahead, 30) + 1):
        day = local.date() + timedelta(days=offset)
        if slots_for(shop, method, day, local):
            days.append(day)
        if len(days) >= count:
            break
    return days


def nearest_options(shop: Shop, method: str, local: datetime, count: int = 3) -> list[tuple[date, str]]:
    """Ближайшие свободные варианты «когда» для трёх быстрых кнопок: два в первый подходящий день и дальше по одному.
    Так клиент в большинстве случаев выбирает время одним нажатием, а не листает календарь."""
    options: list[tuple[date, str]] = []
    for offset in range(0, min(shop.max_days_ahead, 30) + 1):
        day = local.date() + timedelta(days=offset)
        labels = slots_for(shop, method, day, local)
        if not labels:
            continue
        for label in labels[: 2 if not options else 1]:
            options.append((day, label))
        if len(options) >= count:
            break
    return options[:count]


def date_problem(shop: Shop, day: date, local: datetime) -> str:
    """Почему эту дату выбрать нельзя (пусто — можно)."""
    if day < local.date():
        return "Эта дата уже прошла."
    if day > local.date() + timedelta(days=shop.max_days_ahead):
        return f"Заказать можно не дальше, чем на {shop.max_days_ahead} дней вперёд."
    return ""


def slot_valid(shop: Shop, method: str, day: date, local: datetime, label: str) -> bool:
    """Проверка при оформлении: окно существует и ещё не прошло."""
    return label in slots_for(shop, method, day, local)


def find_slot(shop: Shop, label: str) -> Slot | None:
    return next((s for s in shop.slots if s.label == label), None)
