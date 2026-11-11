"""Шаги диалогов, где бот ждёт от клиента текст."""
from aiogram.fsm.state import State, StatesGroup


class Ask(StatesGroup):
    text = State()           # вопрос / срочный заказ / «уточнить наличие» / «за МКАД» (вид — в данных состояния)


class Budget(StatesGroup):
    amount = State()
    phone = State()
    wishes = State()


class Callback(StatesGroup):
    phone = State()


class Checkout(StatesGroup):
    name = State()
    phone = State()
    address = State()
    recipient_name = State()
    recipient_phone = State()
    custom_date = State()
    comment = State()
    code = State()           # промокод или код сертификата — одно поле на оба


class CertFlow(StatesGroup):
    amount = State()         # своя сумма сертификата
    phone = State()          # телефон покупателя (нужен для чека ЮKassa)
    balance = State()        # проверка баланса по коду


class ReviewFlow(StatesGroup):
    text = State()


class AdminFlow(StatesGroup):
    reply = State()          # ответ клиенту
    order_number = State()   # поиск заказа по номеру
    photo = State()          # ждём фото для выбранного товара
