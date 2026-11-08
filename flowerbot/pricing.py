"""Расчёт цен. Единственное место, где считаются скидка, доставка и итог:
корзина, оформление и заказ берут цифры только отсюда — цифрам с кнопок не верим."""
from __future__ import annotations

from datetime import date

from .config import Promo, Shop, Zone
from .util import rub


def delivery_fee(shop: Shop, zone: Zone, goods_after_discount: int) -> int:
    if shop.free_from and goods_after_discount >= shop.free_from:
        return 0
    return zone.price


def promo_problem(promo: Promo, goods: int, is_first_order: bool, today: date) -> str:
    """Почему промокод сейчас не работает (пусто — работает)."""
    if promo.until and today > promo.until:
        return "Акция по этому промокоду уже закончилась."
    if goods < promo.min_order:
        return f"Этот промокод действует при заказе букетов от {rub(promo.min_order)}."
    if promo.first_order_only and not is_first_order:
        return "Этот промокод действует только на первый заказ."
    return ""


def promo_discount(promo: Promo, goods: int) -> int:
    value = goods * promo.percent // 100 if promo.percent else promo.amount
    return max(0, min(value, goods))


def allocate(total: int, weights: list[int]) -> list[int]:
    """Делит сумму total между позициями пропорционально weights так, чтобы в сумме вышло ровно total.
    Нужно, чтобы скидка в чеке разошлась по позициям без потери копеек."""
    weight_sum = sum(weights)
    if total <= 0 or weight_sum <= 0:
        return [0] * len(weights)
    shares = [total * w // weight_sum for w in weights]
    remainders = [(total * w) % weight_sum for w in weights]
    leftover = total - sum(shares)
    for index in sorted(range(len(weights)), key=lambda i: (-remainders[i], i))[:leftover]:
        shares[index] += 1
    return shares
