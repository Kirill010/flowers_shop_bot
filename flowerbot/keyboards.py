"""Все кнопки бота."""
from __future__ import annotations

from datetime import date
from sqlite3 import Row

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from . import texts
from .callbacks import Adm, Cart, Cat, Ce, Co, Info, Mg, Mo, Pay, Prod, Rv
from .catalog import CATEGORIES, CartLine, Product
from .config import Shop
from .crm import LEAD_KINDS
from .orders import FLOW, STATUS, Order
from .util import day_label, rub

BTN_CANCEL = "❌ Отмена"


def btn(text: str, data: CallbackData) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data.pack())


def url_btn(text: str, url: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, url=url)


def kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows if r])


def main_menu() -> ReplyKeyboardMarkup:
    t = texts
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t.BTN_CATALOG), KeyboardButton(text=t.BTN_CART)],
                  [KeyboardButton(text=t.BTN_DELIVERY), KeyboardButton(text=t.BTN_CERT)],
                  [KeyboardButton(text=t.BTN_MANAGER), KeyboardButton(text=t.BTN_MAP)],
                  [KeyboardButton(text=t.BTN_REVIEWS), KeyboardButton(text=t.BTN_ORDERS)]],
        resize_keyboard=True, is_persistent=True, input_field_placeholder="Выберите раздел…")


def contact_request() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Поделиться номером", request_contact=True)], [KeyboardButton(text=BTN_CANCEL)]],
        resize_keyboard=True, one_time_keyboard=True, input_field_placeholder="+7 900 123-45-67")


def cancel_only() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=BTN_CANCEL)]], resize_keyboard=True, one_time_keyboard=True)


# ───────────────────────── каталог ─────────────────────────

def catalog_root() -> InlineKeyboardMarkup:
    rows = [[btn(f"{emoji} {name}", Cat(cat=cat))] for cat, (emoji, name) in CATEGORIES.items()]
    rows.append([btn("💰 Подбор под бюджет", Mg(act="budget"))])
    return kb(*rows)


def product_card(p: Product, month: int) -> InlineKeyboardMarkup:
    if p.orderable(month):
        action = btn("🛒 В корзину", Prod(act="add", sku=p.sku))
    else:
        action = btn("🔎 Цена и наличие" if p.on_request else "🔎 Наличие", Prod(act="ask", sku=p.sku))
    return kb([btn("📖 Подробнее", Prod(act="info", sku=p.sku)), action], [btn("⬅️ Назад", Cat(cat="root"))])


def product_details(p: Product, month: int) -> InlineKeyboardMarkup:
    if p.orderable(month):
        action = btn("🛒 Добавить в корзину", Prod(act="add", sku=p.sku))
    else:
        action = btn("🔎 Узнать цену и наличие" if p.on_request else "🔎 Узнать о наличии", Prod(act="ask", sku=p.sku))
    return kb([action], [btn("💬 Задать вопрос менеджеру", Prod(act="question", sku=p.sku))],
              [btn("⬅️ Назад к каталогу", Cat(cat="root"))])


def catalog_nav(cat: str, page: int, shown_to: int, total: int) -> InlineKeyboardMarkup:
    rows = []
    if shown_to < total:
        rows.append([btn("Показать ещё ▶️", Cat(cat=cat, page=page + 1))])
    rows.append([btn("🛒 Корзина", Cart(act="show")), btn("⬅️ К разделам", Cat(cat="root"))])
    return kb(*rows)


# ───────────────────────── корзина ─────────────────────────

def cart(lines: list[CartLine]) -> InlineKeyboardMarkup:
    rows = []
    for line in lines:
        sku = line.product.sku
        label = f"{line.product.name[:14]} × {line.qty}"
        rows.append([btn("➖", Cart(act="dec", sku=sku)), btn(label, Cart(act="noop")),
                     btn("➕", Cart(act="inc", sku=sku)), btn("🗑", Cart(act="del", sku=sku))])
    if lines:
        rows.append([btn("💳 Перейти к оплате", Cart(act="pay"))])
        rows.append([btn("🔎 Узнать о наличии", Cart(act="check"))])
        rows.append([btn("🧹 Очистить", Cart(act="clear")), btn("🌸 В каталог", Cat(cat="root"))])
    else:
        rows.append([btn("🌸 В каталог", Cat(cat="root"))])
    return kb(*rows)


# ───────────────────────── доставка, адрес, менеджер ─────────────────────────

def info_menu() -> InlineKeyboardMarkup:
    return kb([btn("🚚 Условия доставки", Info(act="delivery")), btn("💳 Способы оплаты", Info(act="payment"))],
              [btn("🛍 Самовывоз", Info(act="pickup"))],
              [btn("💬 Спросить менеджера", Mg(act="menu"))])


def info_back() -> InlineKeyboardMarkup:
    return kb([btn("⬅️ Назад", Info(act="menu"))], [btn("🌸 Выбрать букет", Cat(cat="root"))])


def map_buttons(shop: Shop) -> InlineKeyboardMarkup:
    rows = []
    if shop.map_url:
        rows.append([url_btn("🗺 Яндекс Карты", shop.map_url)])
    if shop.address:
        rows.append([url_btn("🧭 Google Карты", shop.google_maps_url)])
    if shop.channel_url:
        rows.append([url_btn("📢 Наш Telegram-канал", shop.channel_url)])
    return kb(*rows)


def manager(shop: Shop) -> InlineKeyboardMarkup:
    rows = []
    if shop.manager_url:
        rows.append([url_btn("💬 Перейти в чат с менеджером", shop.manager_url)])
    rows += [[btn("❓ Задать вопрос", Mg(act="ask")), btn("🔎 Уточнить наличие", Mg(act="avail"))],
             [btn("⚡ Срочный заказ", Mg(act="urgent")), btn("📞 Оставить номер", Mg(act="phone"))]]
    return kb(*rows)


# ───────────────────────── оформление заказа ─────────────────────────

def co_saved(name: str, phone: str) -> InlineKeyboardMarkup:
    return kb([btn("✅ Да, всё верно", Co(act="saved")), btn("✏️ Изменить", Co(act="edit"))])


def co_method(shop: Shop) -> InlineKeyboardMarkup:
    row = []
    if shop.delivery_enabled:
        row.append(btn("🚚 Доставка", Co(act="method", val="delivery")))
    row.append(btn("🛍 Самовывоз", Co(act="method", val="pickup")))
    return kb(row, [btn("❌ Отмена", Co(act="cancel"))])


def co_zones(shop: Shop) -> InlineKeyboardMarkup:
    rows = [[btn(f"{z.name} — {rub(z.price)}", Co(act="zone", val=str(i)))] for i, z in enumerate(shop.zones)]
    rows.append([btn("🚗 За МКАД / в другой город", Co(act="outside"))])
    rows.append([btn("⬅️ Назад", Co(act="back_method"))])
    return kb(*rows)


def co_recipient() -> InlineKeyboardMarkup:
    return kb([btn("👤 Получатель — я", Co(act="rcp", val="me")), btn("🎁 Другой человек", Co(act="rcp", val="other"))])


def co_dates(days: list[date], today: date) -> InlineKeyboardMarkup:
    rows, row = [], []
    for d in days:
        row.append(btn(day_label(d, today), Co(act="date", val=d.isoformat())))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([btn("📅 Другая дата", Co(act="other_date"))])
    rows.append([btn("❌ Отмена", Co(act="cancel"))])
    return kb(*rows)


def co_slots(items: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """items — пары (подпись, код окна)."""
    rows, row = [], []
    for label, code in items:
        row.append(btn(label, Co(act="slot", val=code)))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([btn("⬅️ Другая дата", Co(act="back_date"))])
    return kb(*rows)


def co_skip(act: str) -> InlineKeyboardMarkup:
    return kb([btn("Пропустить ▶️", Co(act=act))])


def co_summary(methods: list[str], has_promo: bool, has_cert: bool, certs_possible: bool = True) -> InlineKeyboardMarkup:
    from .orders import PAY_METHODS
    rows = []
    if methods == ["cert"]:
        rows.append([btn("✅ Подтвердить заказ", Co(act="pay", val="cert"))])
    else:
        rows += [[btn(PAY_METHODS[m], Co(act="pay", val=m))] for m in methods]
    rows.append([btn("❌ Убрать промокод", Co(act="promo_off")) if has_promo else btn("🏷 Промокод", Co(act="promo"))])
    if certs_possible:
        rows.append([btn("❌ Убрать сертификат", Co(act="cert_off")) if has_cert else btn("🎁 Сертификат", Co(act="cert"))])
    rows.append([btn("✏️ Изменить данные", Co(act="restart")), btn("❌ Отмена", Co(act="cancel"))])
    return kb(*rows)


def pay_link(url: str, payment_id: str) -> InlineKeyboardMarkup:
    return kb([url_btn("💳 Оплатить", url)], [btn("🔄 Проверить оплату", Pay(act="check", id=payment_id))])


# ───────────────────────── мои заказы ─────────────────────────

def my_orders(orders: list[Order]) -> InlineKeyboardMarkup:
    rows = [[btn(f"№{o.id} · {day_label(o.date)} · {rub(o.total)} · {STATUS[o.status].split(' ', 1)[0]}",
                 Mo(act="open", id=o.id))] for o in orders]
    return kb(*rows)


def my_order(order: Order) -> InlineKeyboardMarkup:
    rows = [[btn("🔁 Повторить заказ", Mo(act="repeat", id=order.id))],
            [btn("💬 Вопрос по заказу", Mo(act="ask", id=order.id))]]
    if order.status == "new" and order.pay_status != "paid":
        rows.append([btn("❌ Отменить заказ", Mo(act="cancel", id=order.id))])
    if order.status == "done":
        rows.append([btn("⭐ Оценить заказ", Mo(act="review", id=order.id))])
    rows.append([btn("⬅️ К списку", Mo(act="list"))])
    return kb(*rows)


def review_request(order_id: int) -> InlineKeyboardMarkup:
    return kb([btn("⭐ Оценить заказ", Mo(act="review", id=order_id))])


# ───────────────────────── отзывы, сертификаты ─────────────────────────

def reviews_menu(shop: Shop) -> InlineKeyboardMarkup:
    rows = [[btn("📖 Читать отзывы", Rv(act="read")), btn("✍️ Оставить отзыв", Rv(act="write"))]]
    if shop.reviews_url:
        rows.append([url_btn("⭐ Отзыв на Яндекс Картах", shop.reviews_url)])
    return kb(*rows)


def rating() -> InlineKeyboardMarkup:
    return kb([btn("⭐" * n, Rv(act="rate", val=n)) for n in (1, 2, 3)],
              [btn("⭐" * n, Rv(act="rate", val=n)) for n in (4, 5)])


def reviews_page(page: int, has_more: bool) -> InlineKeyboardMarkup:
    row = []
    if has_more:
        row.append(btn("Ещё отзывы ▶️", Rv(act="read", val=page + 1)))
    row.append(btn("✍️ Оставить отзыв", Rv(act="write")))
    return kb(row)


def certs_menu(shop: Shop) -> InlineKeyboardMarkup:
    row = [btn(rub(a), Ce(act="buy", val=a)) for a in shop.cert_amounts[:3]]
    rows = [row] if row else []
    extra = [btn(rub(a), Ce(act="buy", val=a)) for a in shop.cert_amounts[3:6]]
    if extra:
        rows.append(extra)
    rows.append([btn("✏️ Другая сумма", Ce(act="other")), btn("🔑 Проверить баланс", Ce(act="balance"))])
    return kb(*rows)


# ───────────────────────── администратор ─────────────────────────

def admin_panel(paused: bool) -> InlineKeyboardMarkup:
    return kb([btn("📦 Новые заказы", Adm(act="list", arg="new")), btn("🔥 В работе", Adm(act="list", arg="work"))],
              [btn("📅 На сегодня", Adm(act="list", arg="today")), btn("📅 На завтра", Adm(act="list", arg="tomorrow"))],
              [btn("📬 Обращения", Adm(act="leads")), btn("⭐ Отзывы", Adm(act="reviews"))],
              [btn("🛒 Наличие", Adm(act="stock")), btn("📊 Статистика", Adm(act="stats"))],
              [btn("🔎 Заказ по номеру", Adm(act="find")), btn("📷 Фото товара", Adm(act="photo_help"))],
              [btn("▶️ Возобновить приём заказов" if paused else "⏸ Пауза заказов", Adm(act="pause"))])


def admin_back() -> InlineKeyboardMarkup:
    return kb([btn("⬅️ В панель", Adm(act="panel"))])


def order_list(orders: list[Order]) -> InlineKeyboardMarkup:
    rows = [[btn(f"№{o.id} · {day_label(o.date)} {o.slot[:5]} · {o.name[:12]} · {rub(o.total)}", Adm(act="order", id=o.id))]
            for o in orders]
    rows.append([btn("⬅️ В панель", Adm(act="panel"))])
    return kb(*rows)


_NEXT = {
    "new": [("✅ Принять", "confirmed")],
    "confirmed": [("💐 Собираем", "assembling")],
    "assembling": [("🚚 В пути", "on_way"), ("🛍 Готов к выдаче", "ready")],
    "on_way": [("🏁 Выполнен", "done")],
    "ready": [("🏁 Выполнен", "done")],
}


def order_staff(order: Order) -> InlineKeyboardMarkup:
    rows = []
    if not order.is_final:
        steps = [(t, s) for t, s in _NEXT.get(order.status, []) if s in FLOW[order.status]
                 and not (s == "on_way" and not order.is_delivery) and not (s == "ready" and order.is_delivery)]
        rows.append([btn(t, Adm(act="status", id=order.id, arg=s)) for t, s in steps])
        extra = []
        if order.pay_status != "paid" and order.to_pay > 0:
            extra.append(btn("💰 Оплата получена", Adm(act="paid", id=order.id)))
        extra.append(btn("❌ Отменить", Adm(act="status", id=order.id, arg="cancelled")))
        rows.append(extra)
    rows.append([btn("💬 Написать клиенту", Adm(act="msg", id=order.user_id))])
    return kb(*rows)


def lead(row: Row) -> InlineKeyboardMarkup:
    rows = [[btn("↩️ Ответить", Adm(act="msg", id=row["user_id"])), btn("✅ Готово", Adm(act="lead_done", id=row["id"]))]]
    if row["kind"] == "certificate" and row["amount"] > 0:
        rows.append([btn("🎁 Оплата получена — выдать сертификат", Adm(act="issue", id=row["id"]))])
    return kb(*rows)


def lead_list(rows_: list[Row]) -> InlineKeyboardMarkup:
    rows = [[btn(f"#{r['id']} · {LEAD_KINDS.get(r['kind'], r['kind'])} · {r['name'][:14]}", Adm(act="lead", id=r["id"]))]
            for r in rows_]
    rows.append([btn("⬅️ В панель", Adm(act="panel"))])
    return kb(*rows)


def stock(products: list[Product]) -> InlineKeyboardMarkup:
    rows = [[btn(("✅ " if p.in_stock else "⛔ ") + p.name, Adm(act="toggle", arg=p.sku))] for p in products]
    rows.append([btn("⬅️ В панель", Adm(act="panel"))])
    return kb(*rows)


def stats_periods() -> InlineKeyboardMarkup:
    return kb([btn("Сегодня", Adm(act="stats", arg="1")), btn("7 дней", Adm(act="stats", arg="7")),
               btn("30 дней", Adm(act="stats", arg="30"))], [btn("⬅️ В панель", Adm(act="panel"))])


def reviews_admin(reviews: list[Row]) -> InlineKeyboardMarkup:
    rows = [[btn(f"🙈 Скрыть отзыв #{r['id']}", Adm(act="review_hide", id=r["id"]))] for r in reviews]
    rows.append([btn("⬅️ В панель", Adm(act="panel"))])
    return kb(*rows)
