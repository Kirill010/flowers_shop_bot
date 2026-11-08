"""Мелкие помощники: безопасный вывод текста, телефоны, деньги, даты."""
from __future__ import annotations

import html
import re
from datetime import date, datetime, timedelta, timezone

NBSP = " "
WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня",
              "июля", "августа", "сентября", "октября", "ноября", "декабря"]
MONTHS_NOM = ["январь", "февраль", "март", "апрель", "май", "июнь",
              "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]


def esc(value: object) -> str:
    """Экранирует текст для сообщений с HTML-разметкой: всё, что пишет клиент, выводим только так."""
    return html.escape(str(value), quote=False)


# Управляющие и «невидимые» символы (в том числе разворачивающие текст справа налево — ими подделывают подписи)
_BAD_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏‪-‮⁠-⁩﻿]")


def clean_text(value: object, max_len: int = 500) -> str:
    text = _BAD_CHARS.sub("", str(value or "")).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:max_len].rstrip()


def normalize_phone(raw: object) -> str | None:
    """'8 (917) 549-49-41' → '+79175494941'. Не телефон → None."""
    s = str(raw or "").strip()
    if not re.fullmatch(r"[+\d\s().\-]{7,25}", s):
        return None
    digits = re.sub(r"\D", "", s)
    if s.startswith("+"):
        if digits.startswith("7") and len(digits) != 11:
            return None
        return "+" + digits if 10 <= len(digits) <= 15 else None
    if len(digits) == 11 and digits[0] in "78":
        return "+7" + digits[1:]
    if len(digits) == 10 and digits[0] == "9":
        return "+7" + digits
    return None


def phone_from_input(raw: object) -> str | None:
    """Как normalize_phone, но понимает и номер без «+» от кнопки «Поделиться номером» (Telegram присылает 7917…)."""
    phone = normalize_phone(raw)
    if phone is None and re.fullmatch(r"\d{10,15}", str(raw or "").strip()):
        phone = normalize_phone("+" + str(raw).strip())
    return phone


def format_phone(phone: str) -> str:
    """'+79175494941' → '+7 917 549-49-41' (для чужих форматов — как есть)."""
    m = re.fullmatch(r"\+7(\d{3})(\d{3})(\d{2})(\d{2})", phone or "")
    return f"+7 {m.group(1)} {m.group(2)}-{m.group(3)}-{m.group(4)}" if m else phone


def rub(amount: int) -> str:
    return f"{int(amount):,}".replace(",", NBSP) + NBSP + "₽"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def db_time(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def parse_db_time(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def day_label(d: date, today: date | None = None) -> str:
    """'Сегодня, 14.11', 'Завтра, 15.11', 'Пт, 21.11'."""
    base = f"{d.day:02d}.{d.month:02d}"
    if today is not None:
        if d == today:
            return f"Сегодня, {base}"
        if d == today + timedelta(days=1):
            return f"Завтра, {base}"
    return f"{WEEKDAYS[d.weekday()]}, {base}"


def long_date(d: date) -> str:
    return f"{d.day} {MONTHS_GEN[d.month - 1]}, {WEEKDAYS[d.weekday()].lower()}"


def parse_user_date(text: str, today: date) -> date | None:
    """'14.11', '14.11.26', '14.11.2026', '14/11' → дата. Если день уже прошёл — берём следующий год."""
    m = re.fullmatch(r"\s*(\d{1,2})\s*[./\-]\s*(\d{1,2})(?:\s*[./\-]\s*(\d{2}|\d{4}))?\s*", str(text or ""))
    if not m:
        return None
    day, month = int(m.group(1)), int(m.group(2))
    year_raw = m.group(3)
    try:
        if year_raw:
            year = int(year_raw) + (2000 if len(year_raw) == 2 else 0)
            return date(year, month, day)
        result = date(today.year, month, day)
        if result < today:
            result = date(today.year + 1, month, day)
        return result
    except ValueError:
        return None


def season_text(month_from: int | None, month_to: int | None) -> str:
    if not month_from or not month_to:
        return ""
    return f"с {MONTHS_GEN[month_from - 1]} по {MONTHS_NOM[month_to - 1]}"
