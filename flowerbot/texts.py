"""Все тексты для клиентов и сотрудников в одном месте. Всё, что приходит от пользователей, — только через esc().

Правило текстов: коротко, по делу, одно сообщение — одна мысль. Подробности — по нажатию кнопки."""
from __future__ import annotations

from datetime import date

from .catalog import CATEGORIES, CartLine, Product
from .config import Shop
from .orders import PAY_METHODS, STATUS, Order, Quote
from .util import esc, format_phone, human_day, long_date, rub

# Кнопки главного меню. Порядок в меню: самое частое — сверху.
BTN_CATALOG = "🌸 Каталог"
BTN_CART = "🛒 Корзина"
BTN_ORDERS = "🧾 Мои заказы"
BTN_MANAGER = "💬 Менеджер"
BTN_DELIVERY = "🚚 Доставка и оплата"
BTN_CERT = "🎁 Сертификат"
BTN_MAP = "📍 Мы на карте"
BTN_REVIEWS = "⭐ Отзывы"
BTN_PANEL = "👑 Панель"  # только у сотрудников
CUSTOMER_BUTTONS = {BTN_CATALOG, BTN_CART, BTN_ORDERS, BTN_MANAGER, BTN_DELIVERY, BTN_CERT, BTN_MAP, BTN_REVIEWS}
MENU_BUTTONS = CUSTOMER_BUTTONS | {BTN_PANEL}


def plural(n: int, one: str, few: str, many: str) -> str:
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return one
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return few
    return many


def duration(minutes: int) -> str:
    if minutes and minutes % 60 == 0:
        h = minutes // 60
        return f"{h} {plural(h, 'час', 'часа', 'часов')}"
    return f"{minutes} {plural(minutes, 'минуту', 'минуты', 'минут')}"


def slot_text(slot: str) -> str:
    """'Как можно скорее' → 'как можно скорее' (после слов «Сегодня,»); окна времени — как есть."""
    return slot[:1].lower() + slot[1:] if slot.startswith("Как можно") else slot


# ───────────────────────── общие ─────────────────────────

def greeting(shop: Shop) -> str:
    where = esc(shop.address) + (f" ({esc(shop.landmark)})" if shop.landmark else "")
    return (f"Здравствуйте! 🌸 Это <b>{esc(shop.name)}</b>.\n\n"
            "Выберите букет, оформите доставку или самовывоз — всё здесь, за пару минут. "
            "Есть вопрос — просто напишите его сюда, передам менеджеру.\n\n"
            f"📍 {where}\n🕒 {esc(shop.hours)}")


def help_text(shop: Shop, is_admin: bool) -> str:
    text = ("🌸 <b>Как пользоваться ботом</b>\n\n"
            "Всё — в кнопках внизу экрана. Если их не видно, нажмите /menu.\n"
            "/cart — корзина · /cancel — отменить ввод · /myid — ваш номер в Telegram\n\n"
            f"Позвонить: {esc(shop.phone)}")
    if is_admin:
        text += ("\n\n👑 <b>Для сотрудников</b>\n"
                 f"Кнопка «{BTN_PANEL}» внизу (или /admin): заказы, обращения, наличие, фото товаров.\n"
                 "/order 12 — открыть заказ · /cert — сертификаты · /pause и /resume — приём заказов · "
                 "/reload — перечитать shop.toml и catalog.toml")
    return text


# ───────────────────────── товары ─────────────────────────

def list_text(cat: str, products: list[Product]) -> str:
    emoji, name = CATEGORIES[cat]
    if products and all(p.on_request for p in products):
        hint = "Нажмите на растение — расскажу про уход. Цену и наличие уточним у менеджера."
    else:
        hint = "Нажмите на товар — покажу фото, состав и цену."
    return f"{emoji} <b>{name}</b>\n{hint}"


def _price_line(p: Product) -> str:
    return "💬 Цена по запросу" if p.on_request else f"💰 {rub(p.price)}"


def _status_line(p: Product, month: int) -> str:
    if p.on_request:
        return ""
    if not p.in_season(month):
        return f"🌷 Сезонный товар: бывает {p.season_note}"
    if not p.in_stock:
        return "⛔ Сейчас нет в наличии"
    return ""


def product_card(p: Product, month: int) -> str:
    title = f"<b>{esc(p.name)}</b>" + (f"  ·  🔥 {esc(p.badge)}" if p.badge else "")
    lines = [title]
    if p.short:
        lines.append(esc(p.short))
    lines.append(_price_line(p))
    if status := _status_line(p, month):
        lines.append(status)
    return "\n".join(lines)


def product_details(p: Product, month: int) -> str:
    lines = [f"<b>{esc(p.name)}</b>" + (f"  ·  🔥 {esc(p.badge)}" if p.badge else ""), ""]
    if p.description:
        lines += [esc(p.description), ""]
    if p.composition:
        lines.append("<b>Состав:</b>")
        lines += [f"• {esc(c)}" for c in p.composition]
        lines.append("")
    if p.care:
        lines += ["<b>Как ухаживать:</b>", esc(p.care), ""]
    lines.append(_price_line(p) if not p.on_request else "💬 <b>Цена по запросу</b> — уточните у менеджера")
    if status := _status_line(p, month):
        lines.append(status)
    return "\n".join(lines)


# ───────────────────────── корзина ─────────────────────────

def cart_text(lines: list[CartLine], shop: Shop, month: int) -> str:
    if not lines:
        return "🛒 <b>Корзина пуста</b>\n\nВыберите что-нибудь в каталоге 🌸"
    out = ["🛒 <b>Ваша корзина</b>", ""]
    for i, line in enumerate(lines, 1):
        p = line.product
        row = f"{i}. {esc(p.name)} — {rub(p.price)} × {line.qty} = <b>{rub(line.total)}</b>"
        if reason := p.unavailable_reason(month):
            row += f"\n    ⚠️ {esc(reason)}"
        out.append(row)
    goods = sum(line.total for line in lines)
    out += ["", f"Итого: <b>{rub(goods)}</b>"]
    if shop.delivery_enabled and shop.zones:
        fee = min(z.price for z in shop.zones)
        if shop.free_from and goods >= shop.free_from:
            out.append("🚚 Доставка по Москве — бесплатно 🎉")
        elif shop.free_from:
            out.append(f"🚚 Доставка {rub(fee)}. Добавьте ещё на {rub(shop.free_from - goods)} — будет бесплатной")
        else:
            out.append(f"🚚 Доставка {rub(fee)}")
    out.append("🛍 Самовывоз — бесплатно")
    return "\n".join(out)


# ───────────────────────── доставка, оплата, адрес ─────────────────────────

def info_menu() -> str:
    return "🚚 <b>Доставка и оплата</b>\n\nЧто рассказать?"


def delivery_text(shop: Shop) -> str:
    if not shop.delivery_enabled:
        return "🚚 <b>Доставка</b>\n\nСейчас курьерская доставка не работает — можно оформить самовывоз."
    out = ["🚚 <b>Доставка</b>", ""]
    out += [f"• {esc(z.name)} — {rub(z.price)}" for z in shop.zones]
    if shop.free_from:
        out.append(f"🎁 От {rub(shop.free_from)} — бесплатно.")
    out += [esc(shop.area_text), "",
            f"⏱ На сегодня — закажите минимум за {duration(shop.delivery_lead_minutes)} до приезда. "
            f"На другой день — до {shop.max_days_ahead} дней вперёд."]
    if shop.prepay_future_orders:
        out.append("Букет на определённую дату — только с полной предоплатой.")
    return "\n".join(out)


def payment_text(shop: Shop, online: bool) -> str:
    out = ["💳 <b>Оплата</b>", ""]
    if shop.allow_pay_on_receipt:
        out.append("• При получении — наличными или картой (для заказа на сегодня).")
    if online:
        out.append("• Онлайн — картой или через СБП на защищённой странице ЮKassa.")
    out.append("• Переводом (в том числе по СБП) — менеджер пришлёт реквизиты или ссылку.")
    out.append("• Подарочным сертификатом — полностью или частично.")
    if shop.prepay_future_orders:
        out += ["", "ℹ️ Заказ на другой день — только по предоплате: букет на определённую дату собираем после оплаты."]
    return "\n".join(out)


def pickup_text(shop: Shop) -> str:
    out = ["🛍 <b>Самовывоз — бесплатно</b>", "", f"📍 {esc(shop.address)}"]
    if shop.landmark:
        out.append(f"({esc(shop.landmark)})")
    out += [f"🕒 {esc(shop.hours)} · 📞 {esc(shop.phone)}", "",
            f"Закажете в боте — соберём за {duration(shop.pickup_lead_minutes)}. Готовые букеты можно взять сразу."]
    return "\n".join(out)


def map_text(shop: Shop) -> str:
    out = ["📍 <b>Мы на карте</b>", "", f"<b>{esc(shop.name)}</b>", esc(shop.address)]
    if shop.landmark:
        out.append(f"({esc(shop.landmark)})")
    out += ["", f"🕒 {esc(shop.hours)}", f"📞 {esc(shop.phone)}"]
    return "\n".join(out)


def manager_text(shop: Shop) -> str:
    return ("💬 <b>Менеджер</b>\n\n"
            "Напишите свой вопрос — менеджер ответит в ближайшее время "
            f"(в рабочее время — в течение {duration(shop.response_minutes)}).\n"
            "Или нажмите кнопку, чтобы перейти в чат.\n\n"
            f"🕒 {esc(shop.hours)} · 📞 {esc(shop.phone)}")


# ───────────────────────── заказ ─────────────────────────

_PAY_STATE = {"none": "", "pending": " — ждёт оплаты", "paid": " — оплачен ✅", "failed": " — платёж не прошёл ⚠️"}


def order_card(order: Order, shop: Shop, *, staff: bool = False, username: str = "") -> str:
    out = [f"📦 <b>Заказ №{order.id}</b> — {STATUS[order.status]}"]
    if staff and not order.is_final and order.pay_status == "pending" and order.pay_method in ("online", "transfer"):
        out.append("⏳ <b>Ждём предоплату — не собирать до оплаты</b>")
    out.append(f"🗓 {long_date(order.date)}, {esc(slot_text(order.slot))}")
    if order.is_delivery:
        out.append(f"🚚 Доставка: {esc(order.address)}")
    else:
        out.append(f"🛍 Самовывоз: {esc(shop.address)}")
    out.append(f"👤 {esc(order.name)}, {esc(format_phone(order.phone))}")
    if order.recipient_name:
        out.append(f"🎁 Получатель: {esc(order.recipient_name)}, {esc(format_phone(order.recipient_phone))}")
    if order.comment:
        out.append(f"💌 {esc(order.comment)}")
    out.append("")
    out += [f"• {esc(i.name)} × {i.qty} — {rub(i.total)}" for i in order.items]
    out.append("")
    if order.discount or order.is_delivery:
        out.append(f"Товары: {rub(order.goods)}")
    if order.discount:
        out.append(f"Промокод {esc(order.promo)}: −{rub(order.discount)}")
    if order.is_delivery:
        out.append("Доставка: бесплатно" if order.delivery == 0 else f"Доставка: {rub(order.delivery)}")
    out.append(f"<b>Итого: {rub(order.total)}</b>")
    if order.cert_amount:
        out.append(f"Сертификат: −{rub(order.cert_amount)}")
        out.append(f"<b>К оплате: {rub(order.to_pay)}</b>")
    if order.to_pay == 0:
        out.append("🎁 Оплачен сертификатом")
    else:
        out.append(f"{PAY_METHODS[order.pay_method]}{_PAY_STATE[order.pay_status]}")
    if staff:
        who = f"@{esc(username)}" if username else "профиль"
        out.append(f'🆔 Клиент: <a href="tg://user?id={order.user_id}">{who}</a>')
    return "\n".join(out)


def checkout_summary(shop: Shop, data: dict, quote: Quote, day: date, today: date) -> str:
    out = ["🧾 <b>Проверьте заказ</b>", ""]
    if data.get("method") == "delivery":
        out.append(f"🚚 Доставка: {esc(data.get('address', ''))}")
    else:
        out.append(f"🛍 Самовывоз: {esc(shop.address)}")
    out.append(f"🗓 {human_day(day, today)}, {esc(slot_text(data.get('slot', '')))}")
    out.append(f"👤 {esc(data.get('name', ''))}, {esc(format_phone(data.get('phone', '')))}")
    if data.get("recipient_name"):
        out.append(f"🎁 Получатель: {esc(data['recipient_name'])}, {esc(format_phone(data.get('recipient_phone', '')))}")
    if data.get("comment"):
        out.append(f"💌 {esc(data['comment'])}")
    out.append("")
    out += [f"• {esc(l.product.name)} × {l.qty} — {rub(l.total)}" for l in quote.lines]
    out.append("")
    if quote.promo or data.get("method") == "delivery":
        out.append(f"Товары: {rub(quote.goods)}")
    if quote.promo:
        out.append(f"Промокод {esc(quote.promo.code)}: −{rub(quote.discount)}")
    if data.get("method") == "delivery":
        out.append("Доставка: бесплатно" if quote.delivery == 0 else f"Доставка: {rub(quote.delivery)}")
        if quote.free_left:
            out.append(f"<i>Добавьте товаров ещё на {rub(quote.free_left)} — доставка будет бесплатной</i>")
    out.append(f"<b>Итого: {rub(quote.total)}</b>")
    if quote.cert:
        out.append(f"Сертификат {esc(quote.cert.code)}: −{rub(quote.cert_amount)}")
        out.append(f"<b>К оплате: {rub(quote.to_pay)}</b>")
    if quote.promo_error:
        out.append(f"\n⚠️ {esc(quote.promo_error)}")
    if quote.cert_error:
        out.append(f"\n⚠️ {esc(quote.cert_error)}")
    if quote.to_pay > 0:
        if shop.prepay_future_orders and day > today:
            out += ["", "ℹ️ На другой день — только по предоплате."]
        out += ["", "Как будете платить? 👇"]
    else:
        out += ["", "Сертификат покрывает весь заказ 🎁 Подтвердите оформление 👇"]
    return "\n".join(out)


def order_created(order: Order, shop: Shop, online_failed: bool = False) -> str:
    out = [f"✅ <b>Заказ №{order.id} принят</b>", "",
           f"🗓 {long_date(order.date)}, {esc(slot_text(order.slot))}",
           f"🚚 Доставка: {esc(order.address)}" if order.is_delivery else f"🛍 Самовывоз: {esc(shop.address)}",
           f"💰 {rub(order.total)}"]
    if order.to_pay == 0:
        out[-1] += " · оплачено сертификатом"
    elif order.pay_method == "online" and order.pay_status == "pending":
        out[-1] += " · оплатите по кнопке ниже" if not online_failed else ""
        if online_failed:
            out += ["", "⚠️ Не удалось создать ссылку на оплату. Заказ сохранён — менеджер пришлёт ссылку в ближайшее время."]
    elif order.pay_method == "transfer" and order.pay_status == "pending":
        if shop.payment_details:
            out += ["", "<b>Как оплатить:</b>", esc(shop.payment_details),
                    f"В комментарии к переводу укажите: заказ №{order.id}. Когда оплатите, напишите менеджеру."]
        else:
            out += ["", "Менеджер пришлёт реквизиты или ссылку на оплату — букет на дату собираем после оплаты."]
    else:
        out[-1] += " · оплата при получении"
    out += ["", f"Подтвердим в течение {duration(shop.response_minutes)} (в рабочее время). "
                f"Статус — в разделе «{BTN_ORDERS}»."]
    return "\n".join(out)


def status_notice(order: Order, shop: Shop) -> str | None:
    """Что написать клиенту при смене статуса. None — клиента не беспокоим (внутренний шаг: «собираем»)."""
    n = order.id
    return {
        "confirmed": f"✅ Заказ №{n} подтверждён. Начинаем собирать!",
        "on_way": f"🚚 Заказ №{n} в пути! Курьер скоро будет у вас.",
        "ready": f"🛍 Заказ №{n} готов! Ждём вас: {esc(shop.address)}, {esc(shop.hours)}.",
        "done": f"🏁 Заказ №{n} выполнен. Спасибо, что выбрали {esc(shop.name)}! 🌸",
        "cancelled": f"❌ Заказ №{n} отменён. Если это ошибка — напишите менеджеру в разделе «{BTN_MANAGER}».",
    }.get(order.status)


def lead_card(lead_title: str, name: str, username: str, user_id: int, phone: str, text: str) -> str:
    who = f"@{esc(username)}" if username else "профиль"
    out = [f"<b>{lead_title}</b>", f"👤 {esc(name)} — " + f'<a href="tg://user?id={user_id}">{who}</a>']
    if phone:
        out.append(f"📞 {esc(format_phone(phone))}")
    if text:
        out += ["", esc(text)]
    return "\n".join(out)
