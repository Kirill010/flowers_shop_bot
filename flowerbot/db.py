"""База данных (SQLite): один файл data/shop.db. Здесь только подключение и схема."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

from .util import db_time, utcnow

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    first_name TEXT NOT NULL DEFAULT '', last_name TEXT NOT NULL DEFAULT '', username TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    sku TEXT PRIMARY KEY, category TEXT NOT NULL, name TEXT NOT NULL, short TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '', composition TEXT NOT NULL DEFAULT '[]', care TEXT NOT NULL DEFAULT '',
    price INTEGER NOT NULL DEFAULT 0, badge TEXT NOT NULL DEFAULT '',
    season_from INTEGER, season_to INTEGER,
    sort INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, in_stock INTEGER NOT NULL DEFAULT 1,
    photo TEXT, photo_sig TEXT, photo_file_id TEXT
);

CREATE TABLE IF NOT EXISTS cart (
    user_id INTEGER NOT NULL, sku TEXT NOT NULL, qty INTEGER NOT NULL CHECK (qty > 0),
    PRIMARY KEY (user_id, sku)
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    pay_method TEXT NOT NULL,
    pay_status TEXT NOT NULL DEFAULT 'none',
    name TEXT NOT NULL, phone TEXT NOT NULL,
    method TEXT NOT NULL, zone TEXT NOT NULL DEFAULT '', address TEXT NOT NULL DEFAULT '',
    recipient_name TEXT NOT NULL DEFAULT '', recipient_phone TEXT NOT NULL DEFAULT '',
    date TEXT NOT NULL, slot TEXT NOT NULL, comment TEXT NOT NULL DEFAULT '',
    goods INTEGER NOT NULL, discount INTEGER NOT NULL DEFAULT 0, promo TEXT NOT NULL DEFAULT '',
    delivery INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL,
    cert_code TEXT NOT NULL DEFAULT '', cert_amount INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders (user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders (status);
CREATE INDEX IF NOT EXISTS idx_orders_date ON orders (date);

CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders (id) ON DELETE CASCADE,
    sku TEXT NOT NULL, name TEXT NOT NULL, price INTEGER NOT NULL, qty INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS order_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders (id) ON DELETE CASCADE,
    status TEXT NOT NULL, by_user INTEGER, at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    payment_id TEXT PRIMARY KEY, kind TEXT NOT NULL, order_id INTEGER, user_id INTEGER NOT NULL,
    amount INTEGER NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_payments_status ON payments (status);

CREATE TABLE IF NOT EXISTS certificates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE, amount INTEGER NOT NULL, balance INTEGER NOT NULL CHECK (balance >= 0),
    buyer_id INTEGER, payment_id TEXT UNIQUE, status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL, expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cert_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cert_id INTEGER NOT NULL REFERENCES certificates (id),
    delta INTEGER NOT NULL, order_id INTEGER, note TEXT NOT NULL DEFAULT '', at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cert_attempts (
    user_id INTEGER PRIMARY KEY, fails INTEGER NOT NULL DEFAULT 0, blocked_until TEXT
);

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL, name TEXT NOT NULL, text TEXT NOT NULL,
    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    order_id INTEGER, visible INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL, kind TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '', text TEXT NOT NULL DEFAULT '',
    amount INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'new', created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads (status);
"""


class Database:
    """Тонкая обёртка над sqlite3. Одно соединение, транзакции через `with db.tx():`."""

    def __init__(self, path: str | Path = ":memory:", clock: Callable = utcnow):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
        self.clock = clock
        self._depth = 0
        self._migrate()

    def _migrate(self) -> None:
        current = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if current < SCHEMA_VERSION:
            self.conn.executescript(SCHEMA)
            self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def close(self) -> None:
        self.conn.close()

    def __del__(self) -> None:  # соединение закрывается само, даже если про него забыли
        try:
            self.conn.close()
        except Exception:
            pass

    def now(self) -> str:
        """Текущее время в UTC в виде текста для базы."""
        return db_time(self.clock())

    @contextmanager
    def tx(self) -> Iterator["Database"]:
        """Всё внутри блока — одна транзакция: либо всё сохранится, либо ничего. Можно вкладывать."""
        if self._depth == 0:
            self.conn.execute("BEGIN IMMEDIATE")
        self._depth += 1
        try:
            yield self
        except BaseException:
            self._depth -= 1
            if self._depth == 0:
                self.conn.execute("ROLLBACK")
            raise
        else:
            self._depth -= 1
            if self._depth == 0:
                self.conn.execute("COMMIT")

    @staticmethod
    def _params(params: Sequence[Any] | Mapping[str, Any]) -> Any:
        return params if isinstance(params, Mapping) else tuple(params)

    def run(self, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, self._params(params))

    def one(self, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, self._params(params)).fetchone()

    def all(self, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, self._params(params)).fetchall()

    def scalar(self, sql: str, params: Sequence[Any] | Mapping[str, Any] = (), default: Any = None) -> Any:
        row = self.conn.execute(sql, self._params(params)).fetchone()
        return default if row is None or row[0] is None else row[0]

    # настройки, которые меняются во время работы (например, пауза заказов)
    def get_meta(self, key: str, default: str = "") -> str:
        return self.scalar("SELECT value FROM meta WHERE key = ?", (key,), default)

    def set_meta(self, key: str, value: str) -> None:
        self.run("INSERT INTO meta (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
