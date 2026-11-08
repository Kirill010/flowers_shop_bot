"""Настройки: секреты — из файла .env, всё про магазин — из shop.toml.

Ошибки здесь пишутся простым языком: их увидит владелец в чёрном окне.
"""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent

TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")
SLOT_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*[-–—]\s*(\d{1,2}):(\d{2})\s*$")


class ConfigError(Exception):
    """Понятная человеку ошибка настроек."""


# ───────────────────────── магазин (shop.toml) ─────────────────────────

@dataclass(frozen=True)
class Zone:
    name: str
    price: int


@dataclass(frozen=True)
class Promo:
    code: str
    percent: int = 0
    amount: int = 0
    min_order: int = 0
    first_order_only: bool = False
    until: date | None = None


@dataclass(frozen=True)
class Slot:
    start: time
    end: time

    @property
    def label(self) -> str:
        return f"{self.start:%H:%M}–{self.end:%H:%M}"


@dataclass(frozen=True)
class Shop:
    name: str
    phone: str
    address: str
    landmark: str
    open: time
    close: time
    hours_text: str
    map_url: str
    reviews_url: str
    telegram_channel: str
    manager_username: str
    timezone: str
    response_minutes: int
    # заказы
    paused: bool
    pause_message: str
    min_order: int
    max_days_ahead: int
    pickup_lead_minutes: int
    delivery_lead_minutes: int
    slots: tuple[Slot, ...]
    prepay_future_orders: bool
    allow_pay_on_receipt: bool
    payment_details: str
    max_active_orders: int
    # доставка
    delivery_enabled: bool
    free_from: int
    area_text: str
    zones: tuple[Zone, ...]
    # сертификаты
    cert_amounts: tuple[int, ...]
    cert_min: int
    cert_max: int
    cert_valid_days: int
    promos: tuple[Promo, ...]

    @property
    def hours(self) -> str:
        if self.hours_text:
            return self.hours_text
        return f"Ежедневно, {self.open.hour}:{self.open.minute:02d}–{self.close.hour}:{self.close.minute:02d}"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def manager_url(self) -> str:
        return f"https://t.me/{self.manager_username}" if self.manager_username else ""

    @property
    def channel_url(self) -> str:
        return f"https://t.me/{self.telegram_channel}" if self.telegram_channel else ""

    @property
    def google_maps_url(self) -> str:
        from urllib.parse import quote
        return "https://www.google.com/maps/search/?api=1&query=" + quote(self.address)

    def zone(self, name: str) -> Zone | None:
        return next((z for z in self.zones if z.name == name), None)

    def promo(self, code: str) -> Promo | None:
        code = (code or "").strip().casefold()
        return next((p for p in self.promos if p.code.casefold() == code), None)


def parse_clock(value: object, where: str) -> time:
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(value))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ConfigError(f"В shop.toml в строке {where} время написано неправильно: {value!r}. Нужно так: \"08:00\".")
    return time(int(m.group(1)), int(m.group(2)))


def parse_slot(value: object) -> Slot:
    m = SLOT_RE.match(str(value))
    if not m:
        raise ConfigError(f"В shop.toml в списке slots окно {value!r} написано неправильно. Нужно так: \"09:00-11:00\".")
    h1, m1, h2, m2 = (int(x) for x in m.groups())
    if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59 or (h1, m1) >= (h2, m2):
        raise ConfigError(f"В shop.toml окно {value!r}: начало должно быть раньше конца.")
    return Slot(time(h1, m1), time(h2, m2))


def _section(data: Mapping, name: str) -> Mapping:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"В shop.toml раздел [{name}] написан неправильно.")
    return value


def _int(section: Mapping, key: str, default: int, where: str, minimum: int = 0) -> int:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigError(f"В shop.toml в {where} значение {key} должно быть целым числом (не меньше {minimum}).")
    return value


def _bool(section: Mapping, key: str, default: bool, where: str) -> bool:
    value = section.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"В shop.toml в {where} значение {key} должно быть true или false (без кавычек).")
    return value


def _str(section: Mapping, key: str, default: str = "") -> str:
    return str(section.get(key, default) or "").strip()


def read_toml(path: Path) -> dict:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise ConfigError(f"Не найден файл {path.name}. Он должен лежать рядом с start.bat.") from None
    try:
        return tomllib.loads(raw.decode("utf-8-sig"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(
            f"Файл {path.name} сломан: {exc}. Проверьте кавычки и квадратные скобки у последней правки "
            f"или верните предыдущий вариант файла."
        ) from None


def parse_shop(data: Mapping) -> Shop:
    s, o, d, c = (_section(data, n) for n in ("shop", "orders", "delivery", "certificates"))

    tz = _str(s, "timezone", "Europe/Moscow")
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError(f"В shop.toml неизвестный часовой пояс {tz!r}. Для Москвы: \"Europe/Moscow\".") from None

    slots = tuple(parse_slot(x) for x in o.get("slots", ["09:00-11:00", "11:00-13:00", "13:00-15:00",
                                                         "15:00-17:00", "17:00-19:00", "19:00-21:00"]))
    if not slots:
        raise ConfigError("В shop.toml список slots пуст: добавьте хотя бы одно окно времени.")

    zones_raw = d.get("zones", [{"name": "По Москве", "price": 300}])
    zones: list[Zone] = []
    for z in zones_raw:
        if not isinstance(z, dict) or not str(z.get("name", "")).strip() or not isinstance(z.get("price"), int) \
                or isinstance(z.get("price"), bool) or z["price"] < 0:
            raise ConfigError("В shop.toml в зонах доставки у каждой строки нужны name = \"...\" и price = число.")
        zones.append(Zone(str(z["name"]).strip(), z["price"]))
    if not zones:
        raise ConfigError("В shop.toml список zones пуст. Добавьте зону доставки или поставьте enabled = false.")

    amounts = tuple(c.get("amounts", [1000, 3000, 5000]))
    if not amounts or any(isinstance(a, bool) or not isinstance(a, int) or a <= 0 for a in amounts):
        raise ConfigError("В shop.toml в [certificates] amounts должен быть списком сумм, например [1000, 3000, 5000].")

    promos: list[Promo] = []
    for p in data.get("promo", []):
        code = str(p.get("code", "")).strip()
        if not code:
            raise ConfigError("В shop.toml у промокода нет code = \"...\".")
        percent = _int(p, "percent", 0, f"промокоде {code}")
        amount = _int(p, "amount", 0, f"промокоде {code}")
        if percent > 100 or (percent == 0 and amount == 0) or (percent and amount):
            raise ConfigError(f"В shop.toml у промокода {code} укажите либо percent (1–100), либо amount (рубли).")
        until = p.get("until")
        if until is not None and not isinstance(until, date):
            raise ConfigError(f"В shop.toml у промокода {code} дата until должна быть в виде 2026-12-31 (без кавычек).")
        promos.append(Promo(code, percent, amount, _int(p, "min_order", 0, f"промокоде {code}"),
                            _bool(p, "first_order_only", False, f"промокоде {code}"), until))

    shop = Shop(
        name=_str(s, "name", "Магазин цветов"),
        phone=_str(s, "phone"),
        address=_str(s, "address"),
        landmark=_str(s, "landmark"),
        open=parse_clock(s.get("open", "08:00"), "[shop] open"),
        close=parse_clock(s.get("close", "21:00"), "[shop] close"),
        hours_text=_str(s, "hours_text"),
        map_url=_str(s, "map_url"),
        reviews_url=_str(s, "reviews_url"),
        telegram_channel=_str(s, "telegram_channel").lstrip("@"),
        manager_username=_str(s, "manager_username").lstrip("@"),
        timezone=tz,
        response_minutes=_int(s, "response_minutes", 15, "[shop]", 1),
        paused=_bool(o, "paused", False, "[orders]"),
        pause_message=_str(o, "pause_message", "Сейчас не принимаем заказы в боте — позвоните нам."),
        min_order=_int(o, "min_order", 0, "[orders]"),
        max_days_ahead=_int(o, "max_days_ahead", 60, "[orders]", 1),
        pickup_lead_minutes=_int(o, "pickup_lead_minutes", 20, "[orders]"),
        delivery_lead_minutes=_int(o, "delivery_lead_minutes", 120, "[orders]"),
        slots=slots,
        prepay_future_orders=_bool(o, "prepay_future_orders", True, "[orders]"),
        allow_pay_on_receipt=_bool(o, "allow_pay_on_receipt", True, "[orders]"),
        payment_details=_str(o, "payment_details"),
        max_active_orders=_int(o, "max_active_orders", 5, "[orders]", 1),
        delivery_enabled=_bool(d, "enabled", True, "[delivery]"),
        free_from=_int(d, "free_from", 3000, "[delivery]"),
        area_text=_str(d, "area_text", "Доставляем по Москве. За МКАД — по договорённости."),
        zones=tuple(zones),
        cert_amounts=amounts,
        cert_min=_int(c, "min", 500, "[certificates]", 1),
        cert_max=_int(c, "max", 30000, "[certificates]", 1),
        cert_valid_days=_int(c, "valid_days", 365, "[certificates]", 1),
        promos=tuple(promos),
    )
    if shop.cert_min > shop.cert_max:
        raise ConfigError("В shop.toml в [certificates] min больше max.")
    if shop.open >= shop.close:
        raise ConfigError("В shop.toml время открытия (open) должно быть раньше закрытия (close).")
    return shop


def load_shop(path: Path) -> Shop:
    return parse_shop(read_toml(path))


# ───────────────────────── секреты (.env) ─────────────────────────

@dataclass(frozen=True)
class Settings:
    root: Path
    bot_token: str
    admin_ids: tuple[int, ...]
    orders_chat_id: int | None
    yookassa_shop_id: str
    yookassa_secret_key: str
    yookassa_receipt: bool
    yookassa_vat_code: int
    proxy_url: str
    log_level: str
    api_url: str = ""          # для опытных: адрес своего сервера Bot API (в тестах — поддельный сервер)

    @property
    def yookassa_enabled(self) -> bool:
        return bool(self.yookassa_shop_id and self.yookassa_secret_key)

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "shop.db"

    @property
    def photos_dir(self) -> Path:
        return self.root / "photos"

    @property
    def shop_path(self) -> Path:
        return self.root / "shop.toml"

    @property
    def catalog_path(self) -> Path:
        return self.root / "catalog.toml"


def _parse_ids(raw: str, name: str) -> tuple[int, ...]:
    ids: list[int] = []
    for part in re.split(r"[,\s;]+", raw.strip()):
        if not part:
            continue
        if not re.fullmatch(r"\d{4,15}", part):
            raise ConfigError(
                f"В .env в строке {name} есть {part!r}, но нужны только числа через запятую. "
                f"Свой номер покажет бот по команде /myid."
            )
        ids.append(int(part))
    return tuple(dict.fromkeys(ids))


def load_settings(root: Path = ROOT, environ: Mapping[str, str] | None = None) -> Settings:
    """Читает .env (переменные окружения главнее). environ=... — для тестов."""
    if environ is None:
        values: dict[str, str] = {k: v for k, v in dotenv_values(root / ".env").items() if v is not None}
        values.update(os.environ)
    else:
        values = dict(environ)

    def get(key: str, default: str = "") -> str:
        return (values.get(key) or default).strip()

    token = get("BOT_TOKEN")
    if not token:
        raise ConfigError(
            "В файле .env не указан BOT_TOKEN.\n"
            "  1) В Telegram откройте @BotFather, напишите ему /newbot (или /mybots → ваш бот → API Token).\n"
            "  2) Скопируйте длинный ключ вида 1234567890:AA... и вставьте после BOT_TOKEN= в файле .env."
        )
    if not TOKEN_RE.match(token):
        raise ConfigError(
            "BOT_TOKEN в файле .env выглядит неправильно. Он должен быть таким: 1234567890:AAExample... "
            "(цифры, двоеточие, длинный набор букв). Скопируйте его из @BotFather целиком, без пробелов и кавычек."
        )

    chat_raw = get("ORDERS_CHAT_ID")
    chat_id: int | None = None
    if chat_raw:
        if not re.fullmatch(r"-?\d{5,20}", chat_raw):
            raise ConfigError("ORDERS_CHAT_ID в .env должен быть числом (у групп оно начинается с минуса).")
        chat_id = int(chat_raw)

    shop_id, secret = get("YOOKASSA_SHOP_ID"), get("YOOKASSA_SECRET_KEY")
    if bool(shop_id) != bool(secret):
        raise ConfigError("Для оплаты ЮKassa нужны оба значения в .env: YOOKASSA_SHOP_ID и YOOKASSA_SECRET_KEY. "
                          "Если оплата не нужна — оставьте обе строки пустыми.")
    if shop_id and not re.fullmatch(r"\d{3,12}", shop_id):
        raise ConfigError("YOOKASSA_SHOP_ID в .env должен состоять только из цифр.")
    if secret and not re.match(r"^(live|test)_[A-Za-z0-9_-]{20,}$", secret):
        raise ConfigError("YOOKASSA_SECRET_KEY в .env выглядит неправильно: он начинается с live_ (боевой) или test_ (пробный).")

    vat = get("YOOKASSA_VAT_CODE", "1")
    if not vat.isdigit() or not 1 <= int(vat) <= 6:
        raise ConfigError("YOOKASSA_VAT_CODE в .env должен быть числом от 1 до 6 (1 — без НДС).")

    proxy = get("PROXY_URL")
    if proxy and not re.match(r"^(socks5h?|socks4|https?)://", proxy, re.I):
        raise ConfigError("PROXY_URL в .env должен начинаться с socks5:// или http:// (например socks5://127.0.0.1:1080).")

    api_url = get("TELEGRAM_API_URL")
    if api_url and not re.fullmatch(r"https?://\S+", api_url):
        raise ConfigError("TELEGRAM_API_URL в .env должен начинаться с http:// или https://. Если вы не знаете, что это, "
                          "оставьте строку пустой.")

    level = get("LOG_LEVEL", "INFO").upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        level = "INFO"

    return Settings(
        root=root,
        bot_token=token,
        admin_ids=_parse_ids(get("ADMIN_IDS"), "ADMIN_IDS"),
        orders_chat_id=chat_id,
        yookassa_shop_id=shop_id,
        yookassa_secret_key=secret,
        yookassa_receipt=get("YOOKASSA_RECEIPT", "1") not in {"0", "false", "False", "нет"},
        yookassa_vat_code=int(vat),
        proxy_url=proxy,
        log_level=level,
        api_url=api_url,
    )
