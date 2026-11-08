"""Панель сотрудников: заказы, обращения, отзывы, наличие, статистика, сертификаты, фото, пауза.

ВАЖНО: фильтр IsAdmin висит на всём роутере. Любая команда и любая кнопка отсюда работает только у тех,
чей номер указан в ADMIN_IDS. Забыть проверку в отдельной команде нельзя — в старом боте именно так и вышло."""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject, Filter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, TelegramObject

from .. import catalog, certs, crm, keyboards as kb, notify, orders, texts
from ..callbacks import Adm, Mg
from ..config import ConfigError
from ..crm import LEAD_KINDS
from ..ctx import Ctx
from ..states import AdminFlow
from ..ui import msg_of, safe_edit
from ..util import clean_text, db_time, esc, rub

log = logging.getLogger(__name__)


class IsAdmin(Filter):
    async def __call__(self, event: TelegramObject, ctx: Ctx) -> bool:
        user = getattr(event, "from_user", None)
        return bool(user and ctx.is_admin(user.id))


router = Router(name="admin")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


# ───────────────────────── панель ─────────────────────────

def panel_text(ctx: Ctx) -> str:
    new_orders = ctx.db.scalar("SELECT COUNT(*) FROM orders WHERE status = 'new'", (), 0)
    new_leads = ctx.db.scalar("SELECT COUNT(*) FROM leads WHERE status = 'new'", (), 0)
    today = ctx.local_now().date().isoformat()
    today_orders = ctx.db.scalar("SELECT COUNT(*) FROM orders WHERE date = ? AND status IN "
                                 "('new','confirmed','assembling','on_way','ready')", (today,), 0)
    out = ["👑 <b>Панель сотрудников</b>", "",
           f"📦 Новых заказов: <b>{new_orders}</b>", f"🗓 Заказов на сегодня в работе: <b>{today_orders}</b>",
           f"📬 Необработанных обращений: <b>{new_leads}</b>"]
    if ctx.paused:
        out.append("\n⏸ <b>Приём заказов приостановлен</b>")
    return "\n".join(out)


@router.message(Command("admin"))
async def open_panel(message: Message, state: FSMContext, ctx: Ctx) -> None:
    await state.clear()
    await message.answer(panel_text(ctx), reply_markup=kb.admin_panel(ctx.paused))


@router.callback_query(Adm.filter(F.act == "panel"))
async def panel(call: CallbackQuery, state: FSMContext, ctx: Ctx) -> None:
    await call.answer()
    await state.clear()
    if message := msg_of(call):
        await safe_edit(message, panel_text(ctx), kb.admin_panel(ctx.paused))


@router.callback_query(Adm.filter(F.act == "pause"))
async def toggle_pause(call: CallbackQuery, ctx: Ctx) -> None:
    ctx.db.set_meta("paused", "0" if ctx.db.get_meta("paused") == "1" else "1")
    await call.answer("Приём заказов приостановлен" if ctx.paused else "Приём заказов возобновлён")
    if message := msg_of(call):
        await safe_edit(message, panel_text(ctx), kb.admin_panel(ctx.paused))


@router.message(Command("pause"))
async def pause_cmd(message: Message, ctx: Ctx) -> None:
    ctx.db.set_meta("paused", "1")
    await message.answer("⏸ Приём заказов приостановлен. Каталог виден, но оформить заказ нельзя. Вернуть: /resume")


@router.message(Command("resume"))
async def resume_cmd(message: Message, ctx: Ctx) -> None:
    ctx.db.set_meta("paused", "0")
    if ctx.shop.paused:
        await message.answer("В файле shop.toml стоит paused = true — заказы закрыты оттуда. Поменяйте на false и напишите /reload.")
        return
    await message.answer("▶️ Приём заказов возобновлён.")


@router.message(Command("reload"))
async def reload_cmd(message: Message, ctx: Ctx) -> None:
    try:
        count = ctx.reload()
    except ConfigError as exc:
        await message.answer(f"⚠️ Не получилось перечитать файлы — всё осталось как было.\n\n{esc(str(exc))}")
        return
    await message.answer(f"✅ Настройки и каталог обновлены. Товаров: {count}.")


# ───────────────────────── заказы ─────────────────────────

def _card(ctx: Ctx, order: orders.Order) -> str:
    profile = crm.get_user(ctx.db, order.user_id)
    return texts.order_card(order, ctx.shop, staff=True, username=profile["username"] if profile else "")


@router.callback_query(Adm.filter(F.act == "list"))
async def list_orders(call: CallbackQuery, callback_data: Adm, ctx: Ctx) -> None:
    await call.answer()
    message = msg_of(call)
    if message is None:
        return
    today = ctx.local_now().date()
    work = ("confirmed", "assembling", "on_way", "ready")
    views = {
        "new": ("Новые заказы", dict(statuses=("new",))),
        "work": ("В работе", dict(statuses=work)),
        "today": ("На сегодня", dict(statuses=orders.ACTIVE, day=today)),
        "tomorrow": ("На завтра", dict(statuses=orders.ACTIVE, day=today + timedelta(days=1))),
    }
    title, query = views.get(callback_data.arg, views["new"])
    items = orders.list_orders(ctx.db, limit=15, **query)
    text = f"<b>{title}</b> — {len(items)}" if items else f"<b>{title}</b>\n\nПока ничего нет 🙂"
    await safe_edit(message, text, kb.order_list(items))


@router.callback_query(Adm.filter(F.act == "order"))
async def show_order(call: CallbackQuery, callback_data: Adm, ctx: Ctx) -> None:
    order = orders.get_order(ctx.db, callback_data.id)
    if order is None:
        await call.answer("Заказ не найден.", show_alert=True)
        return
    await call.answer()
    if message := msg_of(call):
        await message.answer(_card(ctx, order), reply_markup=kb.order_staff(order))


@router.callback_query(Adm.filter(F.act == "status"))
async def change_status(call: CallbackQuery, callback_data: Adm, ctx: Ctx, bot: Bot) -> None:
    new = callback_data.arg
    before = orders.get_order(ctx.db, callback_data.id)
    if new not in orders.STATUS or before is None:
        await call.answer("Заказ не найден.", show_alert=True)
        return
    order = orders.set_status(ctx.db, before.id, new, by_user=call.from_user.id)
    message = msg_of(call)
    if order is None:
        await call.answer("Статус уже изменён или такой переход невозможен.", show_alert=True)
        if message and (fresh := orders.get_order(ctx.db, before.id)):
            await safe_edit(message, _card(ctx, fresh), kb.order_staff(fresh))
        return
    await call.answer(orders.STATUS[new])
    extra = ""
    if new == "cancelled" and before.pay_status == "paid" and before.to_pay > 0:
        extra = "\n\n⚠️ <b>Заказ был оплачен</b> — верните деньги клиенту (для онлайн-оплаты — в кабинете ЮKassa)."
    if message:
        await safe_edit(message, _card(ctx, order) + extra, kb.order_staff(order))
    markup = kb.review_request(order.id) if new == "done" else None
    if not await notify.to_user(bot, order.user_id, texts.status_notice(order, ctx.shop), markup) and message:
        await message.answer("⚠️ Клиенту не удалось отправить уведомление (возможно, он заблокировал бота). Свяжитесь по телефону.")


@router.callback_query(Adm.filter(F.act == "paid"))
async def mark_paid(call: CallbackQuery, callback_data: Adm, ctx: Ctx, bot: Bot) -> None:
    order = orders.get_order(ctx.db, callback_data.id)
    if order is None:
        await call.answer("Заказ не найден.", show_alert=True)
        return
    if not orders.mark_paid(ctx.db, order.id, by_user=call.from_user.id):
        await call.answer("Оплата уже отмечена.", show_alert=True)
        return
    await call.answer("Оплата отмечена")
    fresh = orders.get_order(ctx.db, order.id)
    if message := msg_of(call):
        await safe_edit(message, _card(ctx, fresh), kb.order_staff(fresh))
    await notify.to_user(bot, order.user_id, f"✅ Оплата заказа №{order.id} получена. Спасибо!")


@router.callback_query(Adm.filter(F.act == "find"))
async def find_start(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    if message := msg_of(call):
        await state.set_state(AdminFlow.order_number)
        await message.answer("Введите номер заказа (только цифры):")


@router.message(AdminFlow.order_number, F.text)
async def find_number(message: Message, state: FSMContext, ctx: Ctx) -> None:
    digits = "".join(ch for ch in message.text if ch.isdigit())
    order = orders.get_order(ctx.db, int(digits)) if digits and len(digits) < 10 else None
    if order is None:
        await message.answer("Заказ с таким номером не найден. Введите другой номер или нажмите /admin.")
        return
    await state.clear()
    await message.answer(_card(ctx, order), reply_markup=kb.order_staff(order))


@router.message(Command("order"))
async def order_cmd(message: Message, command: CommandObject, ctx: Ctx) -> None:
    digits = "".join(ch for ch in (command.args or "") if ch.isdigit())
    order = orders.get_order(ctx.db, int(digits)) if digits and len(digits) < 10 else None
    if order is None:
        await message.answer("Напишите так: /order 12 (номер заказа).")
        return
    await message.answer(_card(ctx, order), reply_markup=kb.order_staff(order))


# ───────────────────────── ответ клиенту ─────────────────────────

@router.callback_query(Adm.filter(F.act == "msg"))
async def reply_start(call: CallbackQuery, callback_data: Adm, state: FSMContext) -> None:
    await call.answer()
    if message := msg_of(call):
        await state.set_state(AdminFlow.reply)
        await state.update_data(target=callback_data.id)
        await message.answer("✍️ Напишите ответ клиенту (текст или фото) — он придёт от имени магазина. Отмена: /cancel")


@router.message(AdminFlow.reply, F.text | F.photo)
async def reply_send(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    target = data.get("target")
    await state.clear()
    try:
        if message.photo:
            caption = "💬 <b>Магазин:</b>\n" + esc(clean_text(message.caption or "", 800))
            await bot.send_photo(target, message.photo[-1].file_id, caption=caption,
                                 reply_markup=kb.kb([kb.btn("↩️ Ответить", Mg(act="ask"))]))
        else:
            await bot.send_message(target, "💬 <b>Магазин:</b>\n" + esc(clean_text(message.text, 3000)),
                                   reply_markup=kb.kb([kb.btn("↩️ Ответить", Mg(act="ask"))]))
    except TelegramAPIError as exc:
        log.warning("Ответ клиенту %s не доставлен: %s", target, exc)
        await message.answer("⚠️ Не доставлено: клиент мог заблокировать бота. Свяжитесь с ним по телефону.")
        return
    await message.answer("✅ Отправлено клиенту.")


# ───────────────────────── обращения ─────────────────────────

def _lead_text(ctx: Ctx, row) -> str:
    profile = crm.get_user(ctx.db, row["user_id"])
    status = "" if row["status"] == "new" else "\n\n✅ Обработано"
    return texts.lead_card(LEAD_KINDS.get(row["kind"], row["kind"]), row["name"], profile["username"] if profile else "",
                           row["user_id"], row["phone"], row["text"]) + status


@router.callback_query(Adm.filter(F.act == "leads"))
async def list_leads(call: CallbackQuery, ctx: Ctx) -> None:
    await call.answer()
    message = msg_of(call)
    if message is None:
        return
    rows = crm.list_leads(ctx.db, "new", 15)
    text = f"📬 <b>Обращения</b> — необработанных: {len(rows)}" if rows else "📬 <b>Обращения</b>\n\nВсё обработано 🙂"
    await safe_edit(message, text, kb.lead_list(rows))


@router.callback_query(Adm.filter(F.act == "lead"))
async def show_lead(call: CallbackQuery, callback_data: Adm, ctx: Ctx) -> None:
    row = crm.get_lead(ctx.db, callback_data.id)
    if row is None:
        await call.answer("Обращение не найдено.", show_alert=True)
        return
    await call.answer()
    if message := msg_of(call):
        await message.answer(_lead_text(ctx, row), reply_markup=kb.lead(row))


@router.callback_query(Adm.filter(F.act == "lead_done"))
async def lead_done(call: CallbackQuery, callback_data: Adm, ctx: Ctx) -> None:
    row = crm.get_lead(ctx.db, callback_data.id)
    if row is None:
        await call.answer("Обращение не найдено.", show_alert=True)
        return
    crm.set_lead_status(ctx.db, row["id"], "done")
    await call.answer("Отмечено как обработанное")
    if message := msg_of(call):
        await safe_edit(message, _lead_text(ctx, crm.get_lead(ctx.db, row["id"])), None)


@router.callback_query(Adm.filter(F.act == "issue"))
async def issue_certificate(call: CallbackQuery, callback_data: Adm, ctx: Ctx, bot: Bot) -> None:
    """Оплату сертификата получили (перевод, наличные) — выпускаем его и отправляем покупателю."""
    row = crm.get_lead(ctx.db, callback_data.id)
    if row is None or row["kind"] != "certificate" or row["amount"] <= 0:
        await call.answer("Это не заявка на сертификат.", show_alert=True)
        return
    if not crm.set_lead_status(ctx.db, row["id"], "done"):
        await call.answer("Сертификат по этой заявке уже выдан.", show_alert=True)
        return
    cert = certs.issue(ctx.db, ctx.shop, row["amount"], buyer_id=row["user_id"], payment_id=f"lead-{row['id']}",
                       note="Выдан после оплаты через менеджера")
    sent = await notify.send_certificate(bot, ctx, cert, row["user_id"])
    await call.answer("Сертификат выдан")
    if message := msg_of(call):
        await message.answer(f"✅ Сертификат на {rub(cert.amount)} выдан. Код: <code>{cert.code}</code>\n"
                             + ("Отправлен клиенту." if sent else "⚠️ Клиенту отправить не удалось — передайте код сами."))


# ───────────────────────── сертификаты вручную ─────────────────────────

CERT_HELP = ("🎁 <b>Подарочные сертификаты</b>\n\n"
             "<code>/cert new 3000</code> — выпустить сертификат на 3000 ₽ (продали в магазине)\n"
             "<code>/cert ABCD-EFGH-JKLM</code> — проверить баланс\n"
             "<code>/cert ABCD-EFGH-JKLM списать 1500</code> — списать сумму (клиент расплатился в магазине)")


@router.message(Command("cert"))
async def cert_cmd(message: Message, command: CommandObject, ctx: Ctx, bot: Bot) -> None:
    args = (command.args or "").split()
    if not args:
        await message.answer(CERT_HELP)
        return
    if args[0].lower() == "new":
        if len(args) < 2 or not args[1].isdigit() or not ctx.shop.cert_min <= int(args[1]) <= ctx.shop.cert_max:
            await message.answer(f"Укажите сумму от {rub(ctx.shop.cert_min)} до {rub(ctx.shop.cert_max)}: /cert new 3000")
            return
        cert = certs.issue(ctx.db, ctx.shop, int(args[1]), note=f"Продан в магазине (выдал {message.from_user.id})")
        await message.answer(f"✅ Сертификат выпущен. Код: <code>{cert.code}</code>")
        await notify.send_certificate(bot, ctx, cert, message.from_user.id)
        return
    cert = certs.get_by_code(ctx.db, certs.normalize_code(args[0]))
    if cert is None:
        await message.answer("Сертификат с таким кодом не найден.")
        return
    if len(args) >= 3 and args[1].lower() in ("списать", "spisat", "writeoff"):
        if not args[2].isdigit():
            await message.answer("Сумму укажите числом: /cert КОД списать 1500")
            return
        try:
            cert = certs.redeem(ctx.db, cert.code, int(args[2]), note=f"Списано в магазине (сотрудник {message.from_user.id})")
        except certs.CertError as exc:
            await message.answer("⚠️ " + esc(str(exc)))
            return
        await message.answer(f"✅ Списано {rub(int(args[2]))}. Остаток: <b>{rub(cert.balance)}</b>")
        return
    problem = cert.problem(ctx.now())
    await message.answer(f"🎁 <code>{cert.code}</code>\nНоминал: {rub(cert.amount)}\nБаланс: <b>{rub(cert.balance)}</b>\n"
                         f"Действует до {cert.expires:%d.%m.%Y}" + (f"\n⚠️ {esc(problem)}" if problem else ""))


# ───────────────────────── отзывы ─────────────────────────

@router.callback_query(Adm.filter(F.act == "reviews"))
async def list_reviews(call: CallbackQuery, ctx: Ctx) -> None:
    await call.answer()
    message = msg_of(call)
    if message is None:
        return
    rows = crm.list_reviews(ctx.db, 5)
    if not rows:
        await safe_edit(message, "⭐ <b>Отзывы</b>\n\nПока нет отзывов.", kb.admin_back())
        return
    text = "⭐ <b>Последние отзывы</b>\n\n" + "\n\n".join(
        f"#{r['id']} {'⭐' * r['rating']} — {esc(r['name'])}\n{esc(r['text'])}" for r in rows)
    await safe_edit(message, text, kb.reviews_admin(rows))


@router.callback_query(Adm.filter(F.act == "review_hide"))
async def hide_review(call: CallbackQuery, callback_data: Adm, ctx: Ctx) -> None:
    crm.set_review_visible(ctx.db, callback_data.id, False)
    await call.answer("Отзыв скрыт для клиентов")
    await list_reviews(call, ctx)


# ───────────────────────── наличие ─────────────────────────

@router.callback_query(Adm.filter(F.act == "stock"))
async def stock(call: CallbackQuery, ctx: Ctx) -> None:
    await call.answer()
    if message := msg_of(call):
        await safe_edit(message, "🛒 <b>Наличие</b>\n\nНажмите на товар, чтобы отметить «нет сегодня» (⛔) или вернуть в наличие (✅).",
                        kb.stock(catalog.list_products(ctx.db)))


@router.callback_query(Adm.filter(F.act == "toggle"))
async def toggle_stock(call: CallbackQuery, callback_data: Adm, ctx: Ctx) -> None:
    product = catalog.get_product(ctx.db, callback_data.arg)
    if product is None:
        await call.answer("Товар не найден.", show_alert=True)
        return
    catalog.set_in_stock(ctx.db, product.sku, not product.in_stock)
    await call.answer(("⛔ Нет в наличии: " if product.in_stock else "✅ В наличии: ") + product.name)
    if message := msg_of(call):
        await safe_edit(message, "🛒 <b>Наличие</b>\n\nНажмите на товар, чтобы отметить «нет сегодня» (⛔) или вернуть в наличие (✅).",
                        kb.stock(catalog.list_products(ctx.db)))


# ───────────────────────── статистика ─────────────────────────

@router.callback_query(Adm.filter(F.act == "stats"))
async def show_stats(call: CallbackQuery, callback_data: Adm, ctx: Ctx) -> None:
    await call.answer()
    message = msg_of(call)
    if message is None:
        return
    days = int(callback_data.arg) if callback_data.arg in ("1", "7", "30") else 7
    local = ctx.local_now()
    start = datetime.combine(local.date(), time(0, 0), tzinfo=local.tzinfo) - timedelta(days=days - 1)
    s = orders.stats(ctx.db, db_time(start))
    title = "сегодня" if days == 1 else f"последние {days} дней"
    out = [f"📊 <b>Статистика: {title}</b>", "",
           f"Заказов (без отменённых): <b>{s['orders']}</b>", f"Сумма заказов: <b>{rub(s['revenue'])}</b>",
           f"Средний чек: <b>{rub(s['average'])}</b>", f"Отменено: {s['cancelled']}"]
    if s["top"]:
        out += ["", "<b>Чаще всего заказывают:</b>"] + [f"• {esc(name)} — {qty} шт." for name, qty in s["top"]]
    out += ["", "<i>Это заказы из бота. Продажи в магазине и на других площадках сюда не входят.</i>"]
    await safe_edit(message, "\n".join(out), kb.stats_periods())


# ───────────────────────── фото товаров ─────────────────────────

PHOTO_HELP = ("📷 <b>Фото товара</b>\n\nОтправьте боту фото с подписью <code>/photo имя</code>, например "
              "<code>/photo classic</code>. Имена товаров:\n")


@router.callback_query(Adm.filter(F.act == "photo_help"))
async def photo_help_cb(call: CallbackQuery, ctx: Ctx) -> None:
    await call.answer()
    if message := msg_of(call):
        await message.answer(PHOTO_HELP + _sku_list(ctx))


def _sku_list(ctx: Ctx) -> str:
    return "\n".join(f"<code>{p.sku}</code> — {esc(p.name)}" for p in catalog.list_products(ctx.db))


@router.message(F.photo, F.caption.regexp(r"^/photo(@\w+)?\s+\S+"))
async def upload_photo(message: Message, ctx: Ctx, bot: Bot) -> None:
    sku = message.caption.split()[1].strip().lower()
    product = catalog.get_product(ctx.db, sku)
    if product is None:
        await message.answer(f"Нет товара с именем «{esc(sku)}». Доступные имена:\n" + _sku_list(ctx))
        return
    folder = ctx.settings.photos_dir
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{sku}.jpg"
    for ext in catalog.PHOTO_EXTS:  # старые файлы этого товара убираем, чтобы не путать
        old = folder / f"{sku}{ext}"
        if old != target and old.exists():
            old.unlink()
    await bot.download(message.photo[-1], destination=target)
    catalog.refresh_photo(ctx.db, folder, sku)
    await message.answer(f"✅ Фото для «{esc(product.name)}» сохранено — оно появится в карточке.")


@router.message(Command("photo"))
async def photo_cmd(message: Message, ctx: Ctx) -> None:
    await message.answer(PHOTO_HELP + _sku_list(ctx))
