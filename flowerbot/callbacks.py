"""Данные, которые прячутся за кнопками. Telegram позволяет ограниченный размер (64 байта) и не даёт «:» внутри.
Типизированные классы сами проверяют, что пришло: неправильные данные просто не совпадут ни с одной кнопкой."""
from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class Cat(CallbackData, prefix="cat"):
    """Раздел каталога. cat='root' — список разделов."""
    cat: str
    page: int = 0


class Prod(CallbackData, prefix="p"):
    act: str          # info — подробнее, add — в корзину, ask — узнать цену/наличие или вопрос
    sku: str


class Cart(CallbackData, prefix="k"):
    act: str          # inc, dec, del, clear, check, pay
    sku: str = ""


class Co(CallbackData, prefix="co"):
    """Шаги оформления заказа."""
    act: str
    val: str = ""


class Mo(CallbackData, prefix="mo"):
    """«Мои заказы»."""
    act: str          # list, open, repeat, cancel, ask, review
    id: int = 0


class Rv(CallbackData, prefix="rv"):
    act: str          # menu, read, write, rate
    val: int = 0


class Ce(CallbackData, prefix="ce"):
    """Подарочные сертификаты."""
    act: str          # menu, buy, other, balance
    val: int = 0


class Mg(CallbackData, prefix="mg"):
    """Связь с менеджером."""
    act: str          # menu, ask, avail, urgent, phone, outside
    val: str = ""


class Info(CallbackData, prefix="in"):
    act: str          # menu, delivery, payment, pickup


class Pay(CallbackData, prefix="py"):
    act: str          # check
    id: str


class Adm(CallbackData, prefix="ad"):
    """Кнопки администратора. Работают только у тех, кто указан в ADMIN_IDS."""
    act: str
    id: int = 0
    arg: str = ""
