"""Заказы: расчёт цены, оформление, статусы, платежи, статистика.

Правило: все деньги считает `quote_for` из текущих данных базы. Цифры, которые клиент видит на кнопках,
при оформлении не используются — их пересчитывают заново.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from sqlite3 import Row

from . import catalog, certs, pricing, schedule
from .catalog import CartLine
from .config import Promo, Shop
from .db import Database
from .util import rub

STATUS = {
    "new": "🕒 Принят, ждёт подтверждения",
    "confirmed": "✅ Подтверждён",
    "assembling": "💐 Собираем",
    "on_way": "🚚 В пути",
    "ready": "🛍 Готов к выдаче",
    "done": "🏁 Выполнен",
    "cancelled": "❌ Отменён",
}
FLOW = {
    "new": ("confirmed", "cancelled"),
    "confirmed": ("assembling", "on_way", "ready", "done", "cancelled"),
    "assembling": ("on_way", "ready", "done", "cancelled"),
    "on_way": ("done", "cancelled"),
    "ready": ("done", "cancelled"),
    "done": (),
    "cancelled": (),
}
ACTIVE = ("new", "confirmed", "assembling", "on_way", "ready")
PAY_METHODS = {
    "online": "💳 Онлайн (карта или СБП)",
    "cash": "💵 Наличными при получении",
    "card": "💳 Картой при получении",
    "transfer": "🔗 Перевод / ссылка от менеджера",
    "cert": "🎁 Подарочный сертификат",
}
PAY_STATUS = {"none": "оплата при получении", "pending": "ждёт оплаты", "paid": "оплачен", "failed": "платёж не прошёл"}


class OrderError(Exception):
    """Заказ оформить нельзя; текст можно показать клиенту."""


class PriceChanged(OrderError):
    def __init__(self, new_total: int):
        super().__init__("Пока вы оформляли заказ, цена изменилась.")
        self.new_total = new_total


# ───────────────────────── модели ─────────────────────────

@dataclass(frozen=True)
class OrderItem:
    sku: str
    name: str
    price: int
    qty: int

    @property
    def total(self) -> int:
        return self.price * self.qty


@dataclass(frozen=True)
class Order:
    id: int
    user_id: int
    status: str
    pay_method: str
    pay_status: str
    name: str
    phone: str
    method: str
    zone: str
    address: str
    recipient_name: str
    recipient_phone: str
    date: date
    slot: str
    comment: str
    goods: int
    discount: int
    promo: str
    delivery: int
    total: int
    cert_code: str
    cert_amount: int
    created_at: str
    items: tuple[OrderItem, ...]

    @property
    def to_pay(self) -> int:
        return self.total - self.cert_amount

    @property
    def is_final(self) -> bool:
        return self.status in ("done", "cancelled")

    @property
    def is_delivery(self) -> bool:
        return self.method == "delivery"


@dataclass
class Draft:
    """Что клиент ввёл при оформлении (цены здесь нет — её считает quote_for)."""
    user_id: int
    name: str
    phone: str
    method: str
    zone: str
    address: str
    recipient_name: str
    recipient_phone: str
    date: date
    slot: str
    comment: str
    pay_method: str
    promo: str = ""
    cert_code: str = ""


@dataclass(frozen=True)
class Quote:
    lines: tuple[CartLine, ...]
    goods: int
    discount: int
    delivery: int
    total: int
    cert_amount: int
    to_pay: int
    promo: Promo | None
    cert: certs.Certificate | None
    promo_error: str = ""
    cert_error: str = ""
    problems: tuple[str, ...] = ()
    free_left: int = 0


# ───────────────────────── расчёт ─────────────────────────

def is_first_order(db: Database, user_id: int) -> bool:
    return db.scalar("SELECT COUNT(*) FROM orders WHERE user_id = ? AND status != 'cancelled'", (user_id,), 0) == 0


def prepay_required(shop: Shop, day: date, today: date) -> bool:
    """Правило магазина: букет на определённую дату — только с полной предоплатой."""
    return shop.prepay_future_orders and day > today


def allowed_pay_methods(shop: Shop, online_enabled: bool, day: date, today: date, to_pay: int) -> list[str]:
    if to_pay <= 0:
        return ["cert"]
    methods: list[str] = []
    if online_enabled:
        methods.append("online")
    if shop.allow_pay_on_receipt and not prepay_required(shop, day, today):
        methods += ["cash", "card"]
    methods.append("transfer")
    return methods


def quote_for(db: Database, shop: Shop, user_id: int, *, method: str, zone_name: str, promo_code: str,
              cert_code: str, today: date) -> Quote:
    lines = tuple(catalog.cart_lines(db, user_id))
    problems: list[str] = []
    if not lines:
        problems.append("Корзина пуста.")
    for line in lines:
        if reason := line.product.unavailable_reason(today.month):
            problems.append(f"«{line.product.name}»: {reason}.")
    goods = sum(line.total for line in lines)

    promo, promo_error, discount = None, "", 0
    if promo_code:
        found = shop.promo(promo_code)
        if found is None:
            promo_error = "Такого промокода нет."
        elif err := pricing.promo_problem(found, goods, is_first_order(db, user_id), today):
            promo_error = err
        else:
            promo, discount = found, pricing.promo_discount(found, goods)

    after = goods - discount
    if lines and after < shop.min_order:
        problems.append(f"Минимальная сумма заказа — {rub(shop.min_order)}.")
    delivery, free_left = 0, 0
    if method == "delivery":
        zone = shop.zone(zone_name)
        if zone is not None:
            delivery = pricing.delivery_fee(shop, zone, after)
            if shop.free_from and after < shop.free_from:
                free_left = shop.free_from - after
    total = after + delivery

    cert, cert_error, cert_amount = None, "", 0
    if cert_code:
        found_cert = certs.get_by_code(db, certs.normalize_code(cert_code))
        if found_cert is None:
            cert_error = "Сертификат не найден."
        elif problem := found_cert.problem(db.clock()):
            cert_error = problem
        else:
            cert, cert_amount = found_cert, min(found_cert.balance, total)

    return Quote(lines=lines, goods=goods, discount=discount, delivery=delivery, total=total,
                 cert_amount=cert_amount, to_pay=total - cert_amount, promo=promo, cert=cert,
                 promo_error=promo_error, cert_error=cert_error, problems=tuple(problems), free_left=free_left)


# ───────────────────────── оформление ─────────────────────────

def place_order(db: Database, shop: Shop, draft: Draft, *, now_utc: datetime, online_enabled: bool,
                expected_total: int | None = None) -> int:
    """Оформляет заказ одной транзакцией: проверки → запись → списание сертификата → очистка корзины.
    Любая ошибка — и в базе ничего не меняется."""
    local = schedule.local_now(shop, now_utc)
    today = local.date()
    with db.tx():
        if shop.paused or db.get_meta("paused") == "1":
            raise OrderError(shop.pause_message)
        if draft.method not in ("delivery", "pickup"):
            raise OrderError("Выберите доставку или самовывоз.")
        if draft.method == "delivery":
            if not shop.delivery_enabled:
                raise OrderError("Доставка сейчас не работает — выберите самовывоз.")
            if shop.zone(draft.zone) is None:
                raise OrderError("Выберите зону доставки.")
            if len(draft.address) < 6:
                raise OrderError("Укажите адрес доставки: улица, дом, квартира.")
        if schedule.date_problem(shop, draft.date, local):
            raise OrderError(schedule.date_problem(shop, draft.date, local))
        if not schedule.slot_valid(shop, draft.method, draft.date, local, draft.slot):
            raise OrderError("Это время уже недоступно — выберите другое.")

        marks = ",".join("?" * len(ACTIVE))
        active = db.scalar(f"SELECT COUNT(*) FROM orders WHERE user_id = ? AND status IN ({marks})",
                           (draft.user_id, *ACTIVE), 0)
        if active >= shop.max_active_orders:
            raise OrderError("У вас уже несколько заказов в работе. Дождитесь их выполнения или напишите менеджеру.")

        q = quote_for(db, shop, draft.user_id, method=draft.method, zone_name=draft.zone,
                      promo_code=draft.promo, cert_code=draft.cert_code, today=today)
        if q.problems:
            raise OrderError(" ".join(q.problems))
        if q.promo_error:
            raise OrderError(q.promo_error)
        if q.cert_error:
            raise OrderError(q.cert_error)
        if draft.pay_method not in allowed_pay_methods(shop, online_enabled, draft.date, today, q.to_pay):
            raise OrderError("Этот способ оплаты недоступен для вашего заказа. Выберите другой.")
        if expected_total is not None and q.total != expected_total:
            raise PriceChanged(q.total)

        pay_status = "paid" if q.to_pay == 0 else ("pending" if draft.pay_method in ("online", "transfer") else "none")
        now = db.now()
        cur = db.run(
            """INSERT INTO orders (user_id, status, pay_method, pay_status, name, phone, method, zone, address,
                                   recipient_name, recipient_phone, date, slot, comment, goods, discount, promo,
                                   delivery, total, cert_code, cert_amount, created_at, updated_at)
               VALUES (?, 'new', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (draft.user_id, draft.pay_method, pay_status, draft.name, draft.phone, draft.method,
             draft.zone if draft.method == "delivery" else "", draft.address if draft.method == "delivery" else "",
             draft.recipient_name, draft.recipient_phone, draft.date.isoformat(), draft.slot, draft.comment,
             q.goods, q.discount, q.promo.code if q.promo else "", q.delivery, q.total,
             q.cert.code if q.cert else "", q.cert_amount, now, now))
        order_id = cur.lastrowid
        for line in q.lines:
            db.run("INSERT INTO order_items (order_id, sku, name, price, qty) VALUES (?, ?, ?, ?, ?)",
                   (order_id, line.product.sku, line.product.name, line.product.price, line.qty))
        if q.cert and q.cert_amount:
            try:
                certs.redeem(db, q.cert.code, q.cert_amount, order_id, f"Оплата заказа №{order_id}")
            except certs.CertError as exc:
                raise OrderError(str(exc)) from exc
        db.run("INSERT INTO order_events (order_id, status, by_user, at) VALUES (?, 'new', ?, ?)",
               (order_id, draft.user_id, now))
        catalog.cart_clear(db, draft.user_id)
    return order_id


# ───────────────────────── чтение ─────────────────────────

def _order_from_row(db: Database, row: Row) -> Order:
    items = tuple(OrderItem(r["sku"], r["name"], r["price"], r["qty"])
                  for r in db.all("SELECT * FROM order_items WHERE order_id = ? ORDER BY id", (row["id"],)))
    fields = {k: row[k] for k in ("id", "user_id", "status", "pay_method", "pay_status", "name", "phone", "method",
                                  "zone", "address", "recipient_name", "recipient_phone", "slot", "comment",
                                  "goods", "discount", "promo", "delivery", "total", "cert_code", "cert_amount",
                                  "created_at")}
    return Order(**fields, date=date.fromisoformat(row["date"]), items=items)


def get_order(db: Database, order_id: int) -> Order | None:
    row = db.one("SELECT * FROM orders WHERE id = ?", (order_id,))
    return _order_from_row(db, row) if row else None


def get_user_order(db: Database, order_id: int, user_id: int) -> Order | None:
    """Заказ клиента. Чужой заказ не отдаём, даже если номер угадали."""
    row = db.one("SELECT * FROM orders WHERE id = ? AND user_id = ?", (order_id, user_id))
    return _order_from_row(db, row) if row else None


def list_orders(db: Database, *, user_id: int | None = None, statuses: tuple[str, ...] | None = None,
                day: date | None = None, limit: int = 10) -> list[Order]:
    sql, params = "SELECT * FROM orders WHERE 1=1", []
    if user_id is not None:
        sql += " AND user_id = ?"
        params.append(user_id)
    if statuses:
        sql += f" AND status IN ({','.join('?' * len(statuses))})"
        params += list(statuses)
    if day is not None:
        sql += " AND date = ?"
        params.append(day.isoformat())
    rows = db.all(sql + " ORDER BY id DESC LIMIT ?", (*params, limit))
    return [_order_from_row(db, r) for r in rows]


# ───────────────────────── статусы ─────────────────────────

def set_status(db: Database, order_id: int, new_status: str, by_user: int | None = None) -> Order | None:
    """Меняет статус, если такой переход разрешён. Иначе None (например, заказ уже выполнен или отменён)."""
    with db.tx():
        row = db.one("SELECT status FROM orders WHERE id = ?", (order_id,))
        if row is None or new_status not in FLOW.get(row["status"], ()):
            return None
        now = db.now()
        db.run("UPDATE orders SET status = ?, updated_at = ? WHERE id = ?", (new_status, now, order_id))
        db.run("INSERT INTO order_events (order_id, status, by_user, at) VALUES (?, ?, ?, ?)",
               (order_id, new_status, by_user, now))
        if new_status == "cancelled":
            certs.release_for_order(db, order_id)
    return get_order(db, order_id)


def mark_paid(db: Database, order_id: int, by_user: int | None = None) -> bool:
    """Отмечает заказ оплаченным. True — только в первый раз (чтобы не уведомлять и не начислять дважды)."""
    with db.tx():
        changed = db.run("UPDATE orders SET pay_status = 'paid', updated_at = ? "
                         "WHERE id = ? AND pay_status != 'paid'", (db.now(), order_id)).rowcount
        if changed:
            db.run("INSERT INTO order_events (order_id, status, by_user, at) VALUES (?, 'paid', ?, ?)",
                   (order_id, by_user, db.now()))
        return changed == 1


def set_pay_status(db: Database, order_id: int, pay_status: str) -> None:
    db.run("UPDATE orders SET pay_status = ?, updated_at = ? WHERE id = ? AND pay_status != 'paid'",
           (pay_status, db.now(), order_id))


# ───────────────────────── платежи ЮKassa ─────────────────────────

def add_payment(db: Database, payment_id: str, kind: str, user_id: int, amount: int,
                order_id: int | None = None, payload: dict | None = None) -> None:
    now = db.now()
    db.run("INSERT OR IGNORE INTO payments (payment_id, kind, order_id, user_id, amount, status, payload, "
           "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
           (payment_id, kind, order_id, user_id, amount, json.dumps(payload or {}, ensure_ascii=False), now, now))


def get_payment(db: Database, payment_id: str) -> Row | None:
    return db.one("SELECT * FROM payments WHERE payment_id = ?", (payment_id,))


def pending_payments(db: Database) -> list[Row]:
    return db.all("SELECT * FROM payments WHERE status = 'pending' ORDER BY created_at")


def finish_payment(db: Database, payment_id: str, status: str) -> bool:
    """Переводит платёж из «ожидает» в итоговый статус. True — если это сделали мы (сработает один раз)."""
    return db.run("UPDATE payments SET status = ?, updated_at = ? WHERE payment_id = ? AND status = 'pending'",
                  (status, db.now(), payment_id)).rowcount == 1


def count_recent_payments(db: Database, user_id: int, kind: str, since: str) -> int:
    return db.scalar("SELECT COUNT(*) FROM payments WHERE user_id = ? AND kind = ? AND created_at >= ?",
                     (user_id, kind, since), 0)


# ───────────────────────── статистика ─────────────────────────

def stats(db: Database, since: str) -> dict:
    row = db.one("SELECT COUNT(*) AS n, COALESCE(SUM(total), 0) AS revenue FROM orders "
                 "WHERE created_at >= ? AND status != 'cancelled'", (since,))
    cancelled = db.scalar("SELECT COUNT(*) FROM orders WHERE created_at >= ? AND status = 'cancelled'", (since,), 0)
    top = db.all("SELECT oi.name AS name, SUM(oi.qty) AS qty FROM order_items oi JOIN orders o ON o.id = oi.order_id "
                 "WHERE o.created_at >= ? AND o.status != 'cancelled' GROUP BY oi.sku ORDER BY qty DESC, name LIMIT 5",
                 (since,))
    n, revenue = row["n"], row["revenue"]
    return {"orders": n, "revenue": revenue, "average": revenue // n if n else 0, "cancelled": cancelled,
            "top": [(r["name"], r["qty"]) for r in top]}
