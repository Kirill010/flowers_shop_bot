"""Все кнопки бота.

Правило удобства: на экране одно главное действие, всё остальное — на втором плане или в раскрывающемся меню
(«✏️ Изменить», «⚙️ Ещё»). Не больше 3 кнопок в ряду и около 6–7 рядов на экран."""
from __future__ import annotations

from datetime import date
from math import ceil
from sqlite3 import Row

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from . import texts
from .callbacks import Adm, Cart, Cat, Ce, Co, Info, Mg, Mo, Pay, Prod, Rv
from .catalog import CATEGORIES, CartLine, Product
from .config import Shop
from .crm import LEAD_KINDS
from .orders import PAY_BUTTONS, STATUS_SHORT, Order
from .util import day_label, rub, slot_short

BTN_CANCEL = "❌ Отмена"
BTN_MANUAL = "✏️ Ввести вручную"
TAB_NAMES = {"bouquet": "💐 Букеты", "plant": "🌱 Растения"}
LIST_PAGE = 8  # сколько товаров на странице списка


def btn(text: str, data: CallbackData) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data.pack())


def url_btn(text: str, url: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, url=url)


def kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows if r])


def noop() -> Cart:
    return Cart(act="noop")


# ───────────────────────── постоянное меню ─────────────────────────

def main_menu(is_admin: bool = False) -> ReplyKeyboardMarkup:
    t = texts
    rows = [[t.BTN_CATALOG, t.BTN_CART], [t.BTN_ORDERS, t.BTN_MANAGER], [t.BTN_DELIVERY, t.BTN_CERT],
            [t.BTN_MAP, t.BTN_REVIEWS]]
    if is_admin:
        rows.append([t.BTN_PANEL])
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=x) for x in row] for row in rows], resize_keyboard=True,
                               is_persistent=True, input_field_placeholder="Выберите раздел…")


def contact_request(manual: bool = False) -> ReplyKeyboardMarkup:
    """Кнопка «Поделиться номером». manual=True добавляет «Ввести вручную» (для оформления заказа: там нужны ещё и имя)."""
    bottom = [KeyboardButton(text=BTN_MANUAL), KeyboardButton(text=BTN_CANCEL)] if manual else [KeyboardButton(text=BTN_CANCEL)]
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📱 Поделиться номером", request_contact=True)], bottom],
                               resize_keyboard=True, one_time_keyboard=True, input_field_placeholder="+7 900 123-45-67")


# ───────────────────────── каталог: список → карточка ─────────────────────────

def cart_button(count: int, total: int) -> InlineKeyboardButton:
    return btn(f"🛒 Корзина · {count} · {rub(total)}", Cart(act="show"))


def item_label(p: Product, month: int) -> str:
    price = "по запросу" if p.on_request else rub(p.price)
    mark = ""
    if not p.on_request:
        mark = "⛔ " if not p.in_stock else ("🌷 " if not p.in_season(month) else "")
    return f"{mark}{p.name} · {price}"


def catalog_list(cat: str, items: list[Product], page: int, month: int, cart_count: int = 0,
                 cart_total: int = 0) -> InlineKeyboardMarkup:
    rows = [[btn(("• " if c == cat else "") + TAB_NAMES[c], Cat(cat=c)) for c in CATEGORIES]]
    for p in items[page * LIST_PAGE:(page + 1) * LIST_PAGE]:
        rows.append([btn(item_label(p, month), Prod(act="open", sku=p.sku))])
    pages = max(1, ceil(len(items) / LIST_PAGE))
    if pages > 1:
        rows.append([btn("◀", Cat(cat=cat, page=(page - 1) % pages)), btn(f"{page + 1} из {pages}", noop()),
                     btn("▶", Cat(cat=cat, page=(page + 1) % pages))])
    rows.append([btn("💰 Подбор под бюджет", Mg(act="budget"))])
    if cart_count:
        rows.append([cart_button(cart_count, cart_total)])
    return kb(*rows)


def card(p: Product, month: int, *, expanded: bool, prev_sku: str, next_sku: str, index: int, total: int,
         cart_count: int = 0, cart_total: int = 0) -> InlineKeyboardMarkup:
    if p.orderable(month):
        main = btn("🛒 В корзину", Prod(act="add", sku=p.sku))
    else:
        main = btn("🔎 Цена и наличие" if p.on_request else "🔎 Наличие", Prod(act="ask", sku=p.sku))
    more = btn("📖 Свернуть", Prod(act="open", sku=p.sku)) if expanded else btn("📖 Подробнее", Prod(act="more", sku=p.sku))
    rows = [[main, more]]
    if total > 1:
        rows.append([btn("◀", Prod(act="open", sku=prev_sku)), btn(f"{index} из {total}", noop()),
                     btn("▶", Prod(act="open", sku=next_sku))])
    if expanded:
        rows.append([btn("💬 Вопрос менеджеру", Prod(act="question", sku=p.sku))])
    rows.append([btn("⬅️ Назад к списку", Cat(cat=p.category))])
    if cart_count:
        rows.append([cart_button(cart_count, cart_total)])
    return kb(*rows)


# ───────────────────────── корзина ─────────────────────────

def cart(lines: list[CartLine]) -> InlineKeyboardMarkup:
    rows = []
    for line in lines:
        sku = line.product.sku
        rows.append([btn("➖", Cart(act="dec", sku=sku)), btn(f"{line.product.name[:14]} × {line.qty}", noop()),
                     btn("➕", Cart(act="inc", sku=sku)), btn("🗑", Cart(act="del", sku=sku))])
    if lines:
        rows.append([btn("✅ Оформить заказ", Cart(act="pay"))])
        rows.append([btn("🔎 Узнать о наличии", Cart(act="check"))])
        rows.append([btn("🌸 Добавить ещё", Cat(cat="bouquet")), btn("🧹 Очистить", Cart(act="clear"))])
    else:
        rows.append([btn("🌸 В каталог", Cat(cat="bouquet"))])
    return kb(*rows)


# ───────────────────────── доставка, адрес, менеджер ─────────────────────────

def info_menu() -> InlineKeyboardMarkup:
    return kb([btn("🚚 Условия доставки", Info(act="delivery")), btn("💳 Способы оплаты", Info(act="payment"))],
              [btn("🛍 Самовывоз", Info(act="pickup")), btn("💬 Спросить менеджера", Mg(act="menu"))])


def info_back() -> InlineKeyboardMarkup:
    return kb([btn("⬅️ Назад", Info(act="menu")), btn("🌸 Выбрать букет", Cat(cat="bouquet", new=1))])


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


def forward_question() -> InlineKeyboardMarkup:
    return kb([btn("✅ Отправить менеджеру", Mg(act="fwd")), btn("Нет, спасибо", Mg(act="dismiss"))])


# ───────────────────────── оформление заказа ─────────────────────────

def co_saved() -> InlineKeyboardMarkup:
    return kb([btn("✅ Да, всё верно", Co(act="saved")), btn("✏️ Изменить", Co(act="edit_contact"))])


def co_name(first_name: str) -> InlineKeyboardMarkup:
    return kb([btn(f"👤 {first_name}", Co(act="name_tg"))])


def co_method(shop: Shop) -> InlineKeyboardMarkup:
    row = []
    if shop.delivery_enabled:
        row.append(btn("🚚 Доставка", Co(act="method", val="delivery")))
    row.append(btn("🛍 Самовывоз", Co(act="method", val="pickup")))
    return kb(row, [btn("❌ Отмена", Co(act="cancel"))])


def co_zones(shop: Shop) -> InlineKeyboardMarkup:
    rows = [[btn(f"{z.name} — {rub(z.price)}", Co(act="zone", val=str(i)))] for i, z in enumerate(shop.zones)]
    rows.append([btn("🚗 За МКАД — цену назовёт менеджер", Co(act="outside"))])
    rows.append([btn("⬅️ Назад", Co(act="back_method"))])
    return kb(*rows)


def co_address() -> InlineKeyboardMarkup:
    return kb([btn("🚗 За МКАД — цену назовёт менеджер", Co(act="outside"))], [btn("⬅️ Назад", Co(act="back_method"))])


def co_when(items: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """items — пары (подпись кнопки, значение). Три ближайших варианта и «Другая дата»."""
    rows = [[btn(label, Co(act="when", val=val))] for label, val in items]
    rows.append([btn("🗓 Другая дата", Co(act="pick_date"))])
    return kb(*rows)


def co_dates(days: list[date], today: date) -> InlineKeyboardMarkup:
    rows, row = [], []
    for d in days:
        row.append(btn(day_label(d, today), Co(act="date", val=d.isoformat())))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([btn("📅 Ввести дату", Co(act="other_date")), btn("⬅️ Назад", Co(act="back_when"))])
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
    rows.append([btn("⬅️ Другая дата", Co(act="pick_date"))])
    return kb(*rows)


def co_skip(act: str) -> InlineKeyboardMarkup:
    return kb([btn("Пропустить ▶️", Co(act=act))])


def co_summary(methods: list[str]) -> InlineKeyboardMarkup:
    """Главное — способы оплаты. Всё остальное — одна кнопка «Изменить», чтобы экран не был перегружен."""
    rows = [[btn(PAY_BUTTONS[m], Co(act="pay", val=m))] for m in methods]
    rows.append([btn("✏️ Изменить или добавить", Co(act="edit")), btn("❌ Отмена", Co(act="cancel"))])
    return kb(*rows)


def co_edit(delivery: bool, has_promo: bool, has_cert: bool) -> InlineKeyboardMarkup:
    rows = [[btn("🕒 Время", Co(act="edit_when"))] + ([btn("📍 Адрес", Co(act="edit_address"))] if delivery else []),
            [btn("💌 Открытка", Co(act="note"))] + ([btn("🎁 Получатель", Co(act="rcp"))] if delivery else []),
            [btn("👤 Имя и телефон", Co(act="edit_contact")), btn("🏷 Промокод или сертификат", Co(act="code"))]]
    if has_promo:
        rows.append([btn("❌ Убрать промокод", Co(act="drop_promo"))])
    if has_cert:
        rows.append([btn("❌ Убрать сертификат", Co(act="drop_cert"))])
    rows.append([btn("⬅️ Назад к заказу", Co(act="summary"))])
    return kb(*rows)


def pay_link(url: str, payment_id: str) -> InlineKeyboardMarkup:
    return kb([url_btn("💳 Оплатить", url)], [btn("🔄 Проверить оплату", Pay(act="check", id=payment_id))])


# ───────────────────────── мои заказы ─────────────────────────

def my_orders(orders: list[Order]) -> InlineKeyboardMarkup:
    rows = [[btn(f"№{o.id} · {day_label(o.date)} · {rub(o.total)} · {STATUS_SHORT[o.status]}", Mo(act="open", id=o.id))]
            for o in orders]
    return kb(*rows)


def my_order(order: Order) -> InlineKeyboardMarkup:
    rows = [[btn("🔁 Повторить заказ", Mo(act="repeat", id=order.id)), btn("💬 Вопрос по заказу", Mo(act="ask", id=order.id))]]
    if order.status == "new" and order.pay_status != "paid":
        rows.append([btn("❌ Отменить заказ", Mo(act="cancel_ask", id=order.id))])
    if order.status == "done":
        rows.append([btn("⭐ Оценить заказ", Mo(act="review", id=order.id))])
    rows.append([btn("⬅️ К списку", Mo(act="list"))])
    return kb(*rows)


def confirm_cancel_order(order_id: int) -> InlineKeyboardMarkup:
    return kb([btn("✅ Да, отменить", Mo(act="cancel", id=order_id)), btn("↩️ Нет", Mo(act="open", id=order_id))])


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


# ───────────────────────── сотрудники ─────────────────────────

def _counted(text: str, n: int) -> str:
    return f"{text} ({n})" if n else text


def admin_panel(counts: dict, paused: bool = False) -> InlineKeyboardMarkup:
    """Первый экран: только то, что нужно каждый день. Остальное — в «⚙️ Ещё»."""
    return kb([btn(_counted("📦 Новые", counts.get("new", 0)), Adm(act="list", arg="new")),
               btn(_counted("🗓 Сегодня", counts.get("today", 0)), Adm(act="list", arg="today"))],
              [btn(_counted("📬 Обращения", counts.get("leads", 0)), Adm(act="leads")),
               btn("🛒 Наличие", Adm(act="stock"))],
              [btn("⚙️ Ещё", Adm(act="more"))])


def admin_more(paused: bool) -> InlineKeyboardMarkup:
    return kb([btn("📅 Завтра", Adm(act="list", arg="tomorrow")), btn("🔥 В работе", Adm(act="list", arg="work"))],
              [btn("⭐ Отзывы", Adm(act="reviews")), btn("📊 Статистика", Adm(act="stats"))],
              [btn("📷 Фото товаров", Adm(act="photo_help")), btn("🔎 Заказ по номеру", Adm(act="find"))],
              [btn("▶️ Возобновить приём заказов" if paused else "⏸ Пауза заказов", Adm(act="pause"))],
              [btn("⬅️ В панель", Adm(act="panel"))])


def admin_back() -> InlineKeyboardMarkup:
    return kb([btn("⬅️ В панель", Adm(act="panel"))])


def order_list(orders: list[Order], today: date) -> InlineKeyboardMarkup:
    def when(o: Order) -> str:
        return slot_short(o.slot) if o.date == today else f"{day_label(o.date)} {slot_short(o.slot)}"
    rows = [[btn(f"№{o.id} · {when(o)} · {o.name[:12]} · {rub(o.total)}", Adm(act="order", id=o.id))] for o in orders]
    rows.append([btn("⬅️ В панель", Adm(act="panel"))])
    return kb(*rows)


def order_staff(order: Order, back: bool = False) -> InlineKeyboardMarkup:
    """Под заказом — только следующий шаг. Лишних шагов нет: принять → готов/передан курьеру → выполнен.
    back=True — заказ открыт из списка в панели: добавляем «назад», чтобы не засорять чат новыми сообщениями."""
    rows = []
    if not order.is_final:
        if order.status == "new":
            rows.append([btn("✅ Принять", Adm(act="status", id=order.id, arg="confirmed"))])
        elif order.status in ("confirmed", "assembling"):
            rows.append([btn("🚚 Передан курьеру", Adm(act="status", id=order.id, arg="on_way"))] if order.is_delivery
                        else [btn("🛍 Готов к выдаче", Adm(act="status", id=order.id, arg="ready"))])
        else:  # on_way, ready
            rows.append([btn("🏁 Выполнен", Adm(act="status", id=order.id, arg="done"))])
        if order.pay_status == "pending" and order.to_pay > 0:
            rows.append([btn("💰 Оплата получена", Adm(act="paid", id=order.id))])
    last = [btn("💬 Написать клиенту", Adm(act="msg", id=order.user_id))]
    if not order.is_final:
        last.append(btn("❌ Отменить", Adm(act="cancel_ask", id=order.id)))
    rows.append(last)
    if back:
        rows.append([btn("⬅️ В панель", Adm(act="panel"))])
    return kb(*rows)


def confirm_cancel_staff(order_id: int) -> InlineKeyboardMarkup:
    return kb([btn("✅ Да, отменить заказ", Adm(act="status", id=order_id, arg="cancelled")),
               btn("↩️ Нет", Adm(act="back", id=order_id))])


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


def photo_pick(products: list[Product]) -> InlineKeyboardMarkup:
    rows = [[btn(("🖼 " if p.photo else "📷 ") + p.name, Adm(act="photo", arg=p.sku))] for p in products]
    rows.append([btn("⬅️ Назад", Adm(act="more"))])
    return kb(*rows)


def stats_periods() -> InlineKeyboardMarkup:
    return kb([btn("Сегодня", Adm(act="stats", arg="1")), btn("7 дней", Adm(act="stats", arg="7")),
               btn("30 дней", Adm(act="stats", arg="30"))], [btn("⬅️ В панель", Adm(act="panel"))])


def reviews_admin(reviews: list[Row]) -> InlineKeyboardMarkup:
    rows = [[btn(f"🙈 Скрыть отзыв #{r['id']}", Adm(act="review_hide", id=r["id"]))] for r in reviews]
    rows.append([btn("⬅️ В панель", Adm(act="panel"))])
    return kb(*rows)
