"""Подарочные сертификаты: выпуск, проверка кода, списание, картинка для клиента."""
from __future__ import annotations

import io
import math
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from sqlite3 import Row

from PIL import Image, ImageDraw, ImageFont

from .config import Shop
from .db import Database
from .util import db_time, parse_db_time, rub

# Без похожих символов (0/O, 1/I): код читают вслух и переписывают с экрана
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
MAX_FAILS = 5
BLOCK_MINUTES = 30
ASSETS = Path(__file__).resolve().parent / "assets"


class CertError(Exception):
    """Ошибка, текст которой можно показать клиенту."""


@dataclass(frozen=True)
class Certificate:
    id: int
    code: str
    amount: int
    balance: int
    buyer_id: int | None
    payment_id: str | None
    status: str
    created_at: str
    expires_at: str

    @classmethod
    def from_row(cls, row: Row) -> "Certificate":
        return cls(**{k: row[k] for k in ("id", "code", "amount", "balance", "buyer_id", "payment_id",
                                          "status", "created_at", "expires_at")})

    @property
    def expires(self) -> datetime:
        return parse_db_time(self.expires_at)

    def problem(self, now: datetime) -> str:
        """Почему сертификатом нельзя расплатиться (пусто — можно)."""
        if self.status != "active":
            return "Этот сертификат уже использован или отменён."
        if self.expires < now:
            return "Срок действия сертификата закончился."
        if self.balance <= 0:
            return "Баланс сертификата 0 ₽: он уже полностью использован."
        return ""


def new_code() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def normalize_code(raw: object) -> str | None:
    """'abcd efgh jklm' → 'ABCD-EFGH-JKLM'. Не похоже на код → None."""
    s = re.sub(r"[\s\-_]", "", str(raw or "").upper())
    if len(s) != 12 or any(c not in ALPHABET for c in s):
        return None
    return f"{s[:4]}-{s[4:8]}-{s[8:]}"


def get_by_code(db: Database, code: str | None) -> Certificate | None:
    if not code:
        return None
    row = db.one("SELECT * FROM certificates WHERE code = ?", (code,))
    return Certificate.from_row(row) if row else None


def get_by_payment(db: Database, payment_id: str) -> Certificate | None:
    row = db.one("SELECT * FROM certificates WHERE payment_id = ?", (payment_id,))
    return Certificate.from_row(row) if row else None


def issue(db: Database, shop: Shop, amount: int, buyer_id: int | None = None,
          payment_id: str | None = None, note: str = "Выпущен") -> Certificate:
    """Выпускает сертификат. Один платёж — один сертификат: повторный вызов вернёт уже выпущенный."""
    if amount <= 0:
        raise CertError("Сумма сертификата должна быть больше нуля.")
    if payment_id and (existing := get_by_payment(db, payment_id)):
        return existing
    now = db.clock()
    expires = db_time(now + timedelta(days=shop.cert_valid_days))
    for _ in range(10):
        code = new_code()
        try:
            with db.tx():
                cur = db.run(
                    "INSERT INTO certificates (code, amount, balance, buyer_id, payment_id, created_at, expires_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)", (code, amount, amount, buyer_id, payment_id, db.now(), expires))
                db.run("INSERT INTO cert_ledger (cert_id, delta, note, at) VALUES (?, ?, ?, ?)",
                       (cur.lastrowid, amount, note, db.now()))
            return get_by_code(db, code)  # type: ignore[return-value]
        except sqlite3.IntegrityError:
            if payment_id and (existing := get_by_payment(db, payment_id)):
                return existing
    raise CertError("Не удалось выпустить сертификат, попробуйте ещё раз.")


def check_code(db: Database, user_id: int, raw: object) -> tuple[Certificate | None, str]:
    """Клиент ввёл код. Возвращает (сертификат, '') или (None, текст ошибки).
    После 5 неверных попыток подряд ввод блокируется на 30 минут — коды нельзя подобрать."""
    now = db.clock()
    row = db.one("SELECT fails, blocked_until FROM cert_attempts WHERE user_id = ?", (user_id,))
    if row and row["blocked_until"]:
        until = parse_db_time(row["blocked_until"])
        if until > now:
            minutes = max(1, math.ceil((until - now).total_seconds() / 60))
            return None, f"Слишком много неверных попыток. Попробуйте снова через {minutes} мин."

    cert = get_by_code(db, normalize_code(raw))
    if cert is None:
        fails = (row["fails"] if row else 0) + 1
        blocked = None
        if fails >= MAX_FAILS:
            fails, blocked = 0, db_time(now + timedelta(minutes=BLOCK_MINUTES))
        db.run("INSERT INTO cert_attempts (user_id, fails, blocked_until) VALUES (?, ?, ?) "
               "ON CONFLICT(user_id) DO UPDATE SET fails = excluded.fails, blocked_until = excluded.blocked_until",
               (user_id, fails, blocked))
        if blocked:
            return None, f"Сертификат не найден. Попытки закончились — повторите через {BLOCK_MINUTES} мин."
        return None, "Сертификат с таким кодом не найден. Проверьте код: он вида ABCD-EFGH-JKLM."

    db.run("DELETE FROM cert_attempts WHERE user_id = ?", (user_id,))
    return cert, cert.problem(now)


def redeem(db: Database, code: str, amount: int, order_id: int | None = None, note: str = "") -> Certificate:
    """Списывает amount с сертификата. Всё или ничего: при ошибке баланс не меняется."""
    with db.tx():
        cert = get_by_code(db, code)
        if cert is None:
            raise CertError("Сертификат не найден.")
        if problem := cert.problem(db.clock()):
            raise CertError(problem)
        if amount <= 0 or amount > cert.balance:
            raise CertError(f"На сертификате {rub(cert.balance)} — этого недостаточно.")
        balance = cert.balance - amount
        changed = db.run("UPDATE certificates SET balance = ?, status = ? WHERE id = ? AND balance = ?",
                         (balance, "used" if balance == 0 else "active", cert.id, cert.balance)).rowcount
        if changed != 1:
            raise CertError("Баланс только что изменился. Попробуйте ещё раз.")
        db.run("INSERT INTO cert_ledger (cert_id, delta, order_id, note, at) VALUES (?, ?, ?, ?, ?)",
               (cert.id, -amount, order_id, note, db.now()))
    return get_by_code(db, code)  # type: ignore[return-value]


def release_for_order(db: Database, order_id: int) -> int:
    """Возвращает на сертификат деньги, списанные за отменённый заказ. Повторный вызов ничего не делает."""
    restored = 0
    with db.tx():
        for row in db.all("SELECT cert_id, SUM(delta) AS net FROM cert_ledger WHERE order_id = ? GROUP BY cert_id",
                          (order_id,)):
            if row["net"] < 0:
                amount = -row["net"]
                db.run("UPDATE certificates SET balance = balance + ?, status = 'active' WHERE id = ?",
                       (amount, row["cert_id"]))
                db.run("INSERT INTO cert_ledger (cert_id, delta, order_id, note, at) VALUES (?, ?, ?, ?, ?)",
                       (row["cert_id"], amount, order_id, "Возврат: заказ отменён", db.now()))
                restored += amount
    return restored


# ───────────────────────── картинка сертификата ─────────────────────────

@lru_cache(maxsize=16)
def _font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(ASSETS / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")), size)


def _center(draw: ImageDraw.ImageDraw, width: int, y: int, text: str, font, fill) -> None:
    left, _, right, _ = draw.textbbox((0, 0), text, font=font)
    draw.text(((width - (right - left)) / 2 - left, y), text, font=font, fill=fill)


def render_png(cert: Certificate, shop: Shop) -> bytes:
    """Электронный сертификат: красивая картинка, которую можно переслать получателю."""
    w, h = 1200, 760
    top, bottom = (255, 248, 244), (252, 224, 232)
    image = Image.new("RGB", (w, h), top)
    draw = ImageDraw.Draw(image)
    for y in range(h):
        t = y / (h - 1)
        draw.line([(0, y), (w, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))

    # лепестки по углам
    petals = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    pd = ImageDraw.Draw(petals)
    for cx, cy, r, alpha in [(40, 40, 190, 70), (150, 20, 120, 60), (20, 170, 110, 60),
                             (w - 40, h - 40, 200, 70), (w - 170, h - 20, 120, 60), (w - 20, h - 180, 110, 60)]:
        pd.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(224, 88, 124, alpha))
    image = Image.alpha_composite(image.convert("RGBA"), petals).convert("RGB")
    draw = ImageDraw.Draw(image)

    rose, dark, grey = (194, 65, 108), (74, 32, 48), (120, 96, 104)
    draw.rounded_rectangle((28, 28, w - 28, h - 28), radius=34, outline=rose, width=5)
    draw.rounded_rectangle((44, 44, w - 44, h - 44), radius=26, outline=(236, 170, 190), width=2)

    _center(draw, w, 78, shop.name.upper(), _font(True, 34), rose)
    _center(draw, w, 135, "ПОДАРОЧНЫЙ СЕРТИФИКАТ", _font(True, 58), dark)
    # в шрифте DejaVu этой версии нет знака ₽, поэтому на картинке пишем «руб.»
    _center(draw, w, 235, f"{cert.amount:,}".replace(",", " ") + " руб.", _font(True, 140), rose)

    code_font = _font(True, 62)
    code_text = cert.code
    left, _, right, _ = draw.textbbox((0, 0), code_text, font=code_font)
    box_w = right - left + 90
    x0 = (w - box_w) // 2
    draw.rounded_rectangle((x0, 440, x0 + box_w, 540), radius=22, fill=(255, 255, 255), outline=rose, width=3)
    _center(draw, w, 458, code_text, code_font, dark)

    _center(draw, w, 570, f"Действует до {cert.expires:%d.%m.%Y}", _font(False, 32), dark)
    _center(draw, w, 628, "На любые букеты и растения. Назовите код в магазине", _font(False, 27), grey)
    _center(draw, w, 664, "или введите его в боте при оформлении заказа", _font(False, 27), grey)
    footer = " · ".join(x for x in (shop.address, shop.phone) if x)
    if footer:
        _center(draw, w, 704, footer, _font(False, 22), grey)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
