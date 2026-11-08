"""Все тексты для клиентов и сотрудников в одном месте. Всё, что приходит от пользователей, — только через esc()."""
from __future__ import annotations

from datetime import date

from .catalog import CATEGORIES, CartLine, Product
from .config import Shop
from .orders import PAY_METHODS, PAY_STATUS, STATUS, Order, Quote
from .util import esc, format_phone, long_date, rub

# Кнопки главного меню
BTN_CATALOG = "🌸 Каталог"
BTN_CART = "🛒 Корзина"
BTN_DELIVERY = "🚚 Доставка и оплата"
BTN_CERT = "🎁 Сертификат"
BTN_MANAGER = "💬 Менеджер"
BTN_MAP = "📍 Мы на карте"
BTN_REVIEWS = "⭐ Отзывы"
BTN_ORDERS = "🧾 Мои заказы"
MENU_BUTTONS = {BTN_CATALOG, BTN_CART, BTN_DELIVERY, BTN_CERT, BTN_MANAGER, BTN_MAP, BTN_REVIEWS, BTN_ORDERS}


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


# ───────────────────────── общие ─────────────────────────

def greeting(shop: Shop) -> str:
    where = esc(shop.address) + (f" ({esc(shop.landmark)})" if shop.landmark else "")
    return (f"Здравствуйте! 🌸 Вас приветствует <b>{esc(shop.name)}</b>.\n\n"
            "Выбирайте букеты и растения, оформляйте доставку или самовывоз, покупайте подарочные "
            "сертификаты и пишите менеджеру — всё прямо здесь.\n\n"
            f"📍 {where}\n🕒 {esc(shop.hours)}")


def help_text(shop: Shop, is_admin: bool) -> str:
    text = ("🌸 <b>Как пользоваться ботом</b>\n\n"
            "Все разделы — в меню внизу экрана. Если меню пропало, нажмите /menu.\n\n"
            "/menu — главное меню\n/cart — корзина\n/cancel — отменить то, что вводите сейчас\n"
            "/myid — ваш номер в Telegram\n\n"
            f"Позвонить: {esc(shop.phone)}")
    if is_admin:
        text += ("\n\n👑 <b>Для сотрудников</b>\n/admin — панель: заказы, обращения, наличие, статистика\n"
                 "/order 12 — открыть заказ №12\n/cert — подарочные сертификаты\n"
                 "/pause и /resume — остановить и возобновить приём заказов\n"
                 "/reload — перечитать shop.toml и catalog.toml\n"
                 "Фото товара: отправьте фото с подписью <code>/photo classic</code> (classic — имя товара из catalog.toml)")
    return text


def catalog_intro() -> str:
    return "🌸 <b>Каталог</b>\n\nВыберите раздел:"


# ───────────────────────── товары ─────────────────────────

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


def category_title(cat: str) -> str:
    emoji, name = CATEGORIES[cat]
    return f"{emoji} <b>{name}</b>"


# ───────────────────────── корзина ─────────────────────────

def cart_text(lines: list[CartLine], shop: Shop, month: int) -> str:
    if not lines:
        return "🛒 <b>Корзина пуста</b>\n\nЗагляните в каталог — там есть из чего выбрать 🌸"
    out = ["🛒 <b>Ваша корзина</b>", ""]
    for i, line in enumerate(lines, 1):
        p = line.product
        row = f"{i}. {esc(p.name)} — {rub(p.price)} × {line.qty} = <b>{rub(line.total)}</b>"
        if reason := p.unavailable_reason(month):
            row += f"\n    ⚠️ {esc(reason)}"
        out.append(row)
    goods = sum(line.total for line in lines)
    out += ["", f"Итого за товары: <b>{rub(goods)}</b>", ""]
    if shop.delivery_enabled and shop.zones:
        fee = min(z.price for z in shop.zones)
        if shop.free_from and goods >= shop.free_from:
            out.append("🚚 Доставка по Москве — бесплатно 🎉")
        elif shop.free_from:
            out.append(f"🚚 Доставка от {rub(fee)}. До бесплатной доставки осталось {rub(shop.free_from - goods)}")
        else:
            out.append(f"🚚 Доставка от {rub(fee)}")
    out.append("🛍 Самовывоз — бесплатно")
    return "\n".join(out)


# ───────────────────────── доставка, оплата, адрес ─────────────────────────

def info_menu() -> str:
    return "🚚 <b>Доставка и оплата</b>\n\nВыберите, что рассказать подробнее:"


def delivery_text(shop: Shop) -> str:
    if not shop.delivery_enabled:
        return "🚚 <b>Доставка</b>\n\nСейчас курьерская доставка не работает — можно оформить самовывоз."
    out = ["🚚 <b>Условия доставки</b>", ""]
    out += [f"• {esc(z.name)} — {rub(z.price)}" for z in shop.zones]
    if shop.free_from:
        out.append(f"🎁 При заказе от {rub(shop.free_from)} доставка бесплатная.")
    out += ["", esc(shop.area_text), "", "<b>Сроки</b>",
            f"• На сегодня — если оформите минимум за {duration(shop.delivery_lead_minutes)} до начала удобного окна.",
            f"• На другой день — на любую дату в течение {shop.max_days_ahead} дней вперёд.",
            "• Окна доставки: " + ", ".join(s.label for s in shop.slots) + "."]
    if shop.prepay_future_orders:
        out.append("• Букет на определённую дату — только с полной предоплатой.")
    return "\n".join(out)


def payment_text(shop: Shop, online: bool) -> str:
    out = ["💳 <b>Способы оплаты</b>", ""]
    if online:
        out.append("• Онлайн — банковской картой или через СБП, на защищённой странице ЮKassa.")
    if shop.allow_pay_on_receipt:
        out.append("• При получении — наличными или картой (для заказов на сегодня).")
    out.append("• Переводом (в том числе по СБП) или по ссылке — менеджер пришлёт реквизиты или ссылку на оплату.")
    out.append("• Подарочным сертификатом — полностью или частично.")
    if shop.prepay_future_orders:
        out += ["", "ℹ️ Заказ на другой день принимаем только по предоплате: букет на определённую дату "
                    "мы собираем после оплаты."]
    return "\n".join(out)


def pickup_text(shop: Shop) -> str:
    out = ["🛍 <b>Самовывоз — бесплатно</b>", "", f"📍 {esc(shop.address)}"]
    if shop.landmark:
        out.append(f"({esc(shop.landmark)})")
    out += [f"🕒 {esc(shop.hours)}", f"📞 {esc(shop.phone)}", "",
            f"Заказали в боте — заберёте через {duration(shop.pickup_lead_minutes)}. "
            "Готовые букеты в магазине можно взять сразу."]
    return "\n".join(out)


def map_text(shop: Shop) -> str:
    out = ["📍 <b>Мы на карте</b>", "", f"<b>{esc(shop.name)}</b>", esc(shop.address)]
    if shop.landmark:
        out.append(f"({esc(shop.landmark)})")
    out += ["", f"🕒 {esc(shop.hours)}", f"📞 {esc(shop.phone)}"]
    return "\n".join(out)


def manager_text(shop: Shop) -> str:
    return ("💬 <b>Связь с менеджером</b>\n\n"
            "Напишите свой вопрос — менеджер ответит в ближайшее время "
            f"(в рабочее время — в течение {duration(shop.response_minutes)}).\n"
            "Или нажмите кнопку, чтобы перейти в чат.\n\n"
            f"🕒 {esc(shop.hours)}\n📞 {esc(shop.phone)}")


# ───────────────────────── заказ ─────────────────────────

def order_card(order: Order, shop: Shop, *, staff: bool = False, username: str = "") -> str:
    out = [f"📦 <b>Заказ №{order.id}</b> — {STATUS[order.status]}",
           f"🗓 {long_date(order.date)}, {esc(order.slot)}"]
    if order.is_delivery:
        out.append(f"🚚 Доставка ({esc(order.zone)}): {esc(order.address)}")
    else:
        out.append(f"🛍 Самовывоз: {esc(shop.address)}")
    out.append(f"👤 {esc(order.name)}, {esc(format_phone(order.phone))}")
    if order.recipient_name:
        out.append(f"🎁 Получатель: {esc(order.recipient_name)}, {esc(format_phone(order.recipient_phone))}")
    if order.comment:
        out.append(f"💬 {esc(order.comment)}")
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
        out.append(f"💳 {PAY_METHODS[order.pay_method]} — {PAY_STATUS[order.pay_status]}")
    if staff:
        who = f"@{esc(username)}" if username else "профиль"
        out.append(f'🆔 Клиент: <a href="tg://user?id={order.user_id}">{who}</a>')
    return "\n".join(out)


def checkout_summary(shop: Shop, data: dict, quote: Quote, day: date, today: date) -> str:
    out = ["🧾 <b>Проверьте заказ</b>", ""]
    if data.get("method") == "delivery":
        out.append(f"🚚 Доставка ({esc(data.get('zone', ''))}): {esc(data.get('address', ''))}")
    else:
        out.append(f"🛍 Самовывоз: {esc(shop.address)}")
    out.append(f"🗓 {long_date(day)}, {esc(data.get('slot', ''))}")
    out.append(f"👤 {esc(data.get('name', ''))}, {esc(format_phone(data.get('phone', '')))}")
    if data.get("recipient_name"):
        out.append(f"🎁 Получатель: {esc(data['recipient_name'])}, {esc(format_phone(data.get('recipient_phone', '')))}")
    if data.get("comment"):
        out.append(f"💬 {esc(data['comment'])}")
    out.append("")
    out += [f"• {esc(l.product.name)} × {l.qty} — {rub(l.total)}" for l in quote.lines]
    out.append("")
    out.append(f"Товары: {rub(quote.goods)}")
    if quote.promo:
        out.append(f"Промокод {esc(quote.promo.code)}: −{rub(quote.discount)}")
    if data.get("method") == "delivery":
        out.append("Доставка: бесплатно" if quote.delivery == 0 else f"Доставка: {rub(quote.delivery)}")
        if quote.free_left:
            out.append(f"<i>Добавьте товаров ещё на {rub(quote.free_left)} — и доставка будет бесплатной</i>")
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
            out += ["", "ℹ️ Заказ на другой день принимаем только по предоплате."]
        out += ["", "Выберите способ оплаты — заказ оформится сразу 👇"]
    else:
        out += ["", "Сертификат покрывает весь заказ 🎁 Подтвердите оформление 👇"]
    return "\n".join(out)


def order_created(order: Order, shop: Shop, online_failed: bool = False) -> str:
    out = [f"✅ <b>Заказ №{order.id} оформлен!</b>", "", order_card(order, shop), ""]
    if order.pay_method == "online" and order.pay_status == "pending":
        if online_failed:
            out.append("⚠️ Не удалось создать ссылку на оплату. Заказ сохранён — менеджер пришлёт ссылку в ближайшее время.")
        else:
            out.append("Нажмите «Оплатить» — откроется защищённая страница ЮKassa. После оплаты я сообщу вам здесь.")
    elif order.pay_method == "transfer" and order.pay_status == "pending":
        if shop.payment_details:
            out += ["<b>Как оплатить:</b>", esc(shop.payment_details), f"В комментарии к переводу укажите: заказ №{order.id}.",
                    "Когда оплатите, напишите менеджеру — мы отметим оплату."]
        else:
            out.append("Менеджер пришлёт вам реквизиты или ссылку на оплату. Заказ на дату собираем после оплаты.")
    elif order.pay_method in ("cash", "card"):
        out.append("Оплата — при получении.")
    out.append(f"\nМенеджер подтвердит заказ в ближайшее время (в рабочее время — в течение "
               f"{duration(shop.response_minutes)}). Статус всегда виден в разделе «{BTN_ORDERS}».")
    return "\n".join(out)


def status_notice(order: Order, shop: Shop) -> str:
    n = order.id
    return {
        "confirmed": f"✅ Заказ №{n} подтверждён. Приступаем к сборке!",
        "assembling": f"💐 Собираем ваш заказ №{n}.",
        "on_way": f"🚚 Заказ №{n} в пути! Курьер скоро будет у вас.",
        "ready": f"🛍 Заказ №{n} готов! Ждём вас: {esc(shop.address)}, {esc(shop.hours)}.",
        "done": f"🏁 Заказ №{n} выполнен. Спасибо, что выбрали {esc(shop.name)}! 🌸",
        "cancelled": f"❌ Заказ №{n} отменён. Если это ошибка — напишите менеджеру в разделе «{BTN_MANAGER}».",
    }[order.status]


def lead_card(lead_title: str, name: str, username: str, user_id: int, phone: str, text: str) -> str:
    who = f"@{esc(username)}" if username else "профиль"
    out = [f"<b>{lead_title}</b>", f"👤 {esc(name)} — " + f'<a href="tg://user?id={user_id}">{who}</a>']
    if phone:
        out.append(f"📞 {esc(format_phone(phone))}")
    if text:
        out += ["", esc(text)]
    return "\n".join(out)
