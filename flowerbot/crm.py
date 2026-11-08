"""Клиенты, обращения к менеджеру («заявки») и отзывы."""
from __future__ import annotations

from sqlite3 import Row

from .db import Database

LEAD_KINDS = {
    "question": "❓ Вопрос",
    "urgent": "⚡ Срочный заказ",
    "budget": "💰 Подбор под бюджет",
    "availability": "🔎 Узнать о наличии",
    "callback": "📞 Просьба перезвонить",
    "certificate": "🎁 Покупка сертификата",
    "outside": "🚗 Доставка за МКАД",
}


# ───────────── клиенты ─────────────

def upsert_user(db: Database, user_id: int, first_name: str, last_name: str | None, username: str | None) -> None:
    now = db.now()
    db.run(
        """INSERT INTO users (user_id, first_name, last_name, username, created_at, last_seen)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET first_name = excluded.first_name, last_name = excluded.last_name,
                                              username = excluded.username, last_seen = excluded.last_seen""",
        (user_id, first_name or "", last_name or "", username or "", now, now))


def get_user(db: Database, user_id: int) -> Row | None:
    return db.one("SELECT * FROM users WHERE user_id = ?", (user_id,))


def save_contact(db: Database, user_id: int, name: str | None = None, phone: str | None = None) -> None:
    if name:
        db.run("UPDATE users SET name = ? WHERE user_id = ?", (name, user_id))
    if phone:
        db.run("UPDATE users SET phone = ? WHERE user_id = ?", (phone, user_id))


# ───────────── заявки ─────────────

def add_lead(db: Database, user_id: int, kind: str, text: str, name: str = "", phone: str = "",
             amount: int = 0) -> int:
    cur = db.run("INSERT INTO leads (user_id, kind, name, phone, text, amount, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (user_id, kind, name, phone, text, amount, db.now()))
    return cur.lastrowid


def recent_leads(db: Database, user_id: int, since: str) -> int:
    """Сколько обращений клиент отправил за период (защита сотрудников от спама)."""
    return db.scalar("SELECT COUNT(*) FROM leads WHERE user_id = ? AND created_at >= ?", (user_id, since), 0)


def get_lead(db: Database, lead_id: int) -> Row | None:
    return db.one("SELECT * FROM leads WHERE id = ?", (lead_id,))


def list_leads(db: Database, status: str = "new", limit: int = 10) -> list[Row]:
    return db.all("SELECT * FROM leads WHERE status = ? ORDER BY id DESC LIMIT ?", (status, limit))


def set_lead_status(db: Database, lead_id: int, status: str) -> bool:
    return db.run("UPDATE leads SET status = ? WHERE id = ? AND status != ?", (status, lead_id, status)).rowcount == 1


# ───────────── отзывы ─────────────

def add_review(db: Database, user_id: int, name: str, text: str, rating: int, order_id: int | None = None) -> int:
    cur = db.run("INSERT INTO reviews (user_id, name, text, rating, order_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                 (user_id, name, text, rating, order_id, db.now()))
    return cur.lastrowid


def list_reviews(db: Database, limit: int = 5, offset: int = 0, visible_only: bool = True) -> list[Row]:
    where = "WHERE visible = 1" if visible_only else ""
    return db.all(f"SELECT * FROM reviews {where} ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset))


def count_reviews(db: Database, visible_only: bool = True) -> int:
    where = "WHERE visible = 1" if visible_only else ""
    return db.scalar(f"SELECT COUNT(*) FROM reviews {where}", (), 0)


def set_review_visible(db: Database, review_id: int, visible: bool) -> bool:
    return db.run("UPDATE reviews SET visible = ? WHERE id = ?", (int(visible), review_id)).rowcount == 1
