"""Каталог (товары из catalog.toml) и корзина клиента."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from sqlite3 import Row

from .config import ConfigError, read_toml
from .db import Database
from .util import season_text

CATEGORIES = {
    "bouquet": ("💐", "Букеты"),
    "plant": ("🌱", "Горшечные растения"),
}
SKU_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,23}$")
PHOTO_EXTS = (".jpg", ".jpeg", ".png", ".webp")
MAX_LINES = 20
MAX_QTY = 20


@dataclass(frozen=True)
class Product:
    sku: str
    category: str
    name: str
    short: str
    description: str
    composition: tuple[str, ...]
    care: str
    price: int
    badge: str
    note_prompt: str
    season_from: int | None
    season_to: int | None
    sort: int
    active: bool
    in_stock: bool
    photo: str | None
    photo_file_id: str | None

    @classmethod
    def from_row(cls, row: Row) -> "Product":
        return cls(
            sku=row["sku"], category=row["category"], name=row["name"], short=row["short"],
            description=row["description"], composition=tuple(json.loads(row["composition"] or "[]")),
            care=row["care"], price=row["price"], badge=row["badge"], note_prompt=row["note_prompt"],
            season_from=row["season_from"], season_to=row["season_to"], sort=row["sort"],
            active=bool(row["active"]), in_stock=bool(row["in_stock"]),
            photo=row["photo"], photo_file_id=row["photo_file_id"],
        )

    @property
    def on_request(self) -> bool:
        return self.price <= 0

    @property
    def season_note(self) -> str:
        return season_text(self.season_from, self.season_to)

    def in_season(self, month: int) -> bool:
        if not self.season_from or not self.season_to:
            return True
        if self.season_from <= self.season_to:
            return self.season_from <= month <= self.season_to
        return month >= self.season_from or month <= self.season_to  # сезон через Новый год

    def unavailable_reason(self, month: int) -> str:
        """Почему товар нельзя положить в корзину (пусто — можно)."""
        if not self.active:
            return "этого товара больше нет в каталоге"
        if self.on_request:
            return "цену и наличие уточняйте у менеджера"
        if not self.in_season(month):
            return f"сезонный товар, бывает {self.season_note}"
        if not self.in_stock:
            return "сейчас нет в наличии"
        return ""

    def orderable(self, month: int) -> bool:
        return not self.unavailable_reason(month)


@dataclass(frozen=True)
class CartLine:
    product: Product
    qty: int

    @property
    def total(self) -> int:
        return self.product.price * self.qty


# ───────────────────────── загрузка из catalog.toml ─────────────────────────

def parse_catalog(data: dict) -> list[dict]:
    items = data.get("product", [])
    if not isinstance(items, list) or not items:
        raise ConfigError("В catalog.toml нет ни одного товара. Каждый товар начинается со строки [[product]].")
    seen: set[str] = set()
    result: list[dict] = []
    for index, raw in enumerate(items, 1):
        label = f"товар №{index} ({raw.get('name') or raw.get('sku') or '?'})"
        sku = str(raw.get("sku", "")).strip()
        if not SKU_RE.match(sku):
            raise ConfigError(f"В catalog.toml у {label}: sku должен быть латиницей без пробелов, до 24 знаков, например \"classic\".")
        if sku in seen:
            raise ConfigError(f"В catalog.toml два товара с одинаковым sku = \"{sku}\". Сделайте их разными.")
        seen.add(sku)
        category = raw.get("category")
        if category not in CATEGORIES:
            raise ConfigError(f"В catalog.toml у {label}: category должна быть bouquet (букет) или plant (растение).")
        name = str(raw.get("name", "")).strip()
        if not name or len(name) > 60:
            raise ConfigError(f"В catalog.toml у {label}: название обязательно и не длиннее 60 знаков.")
        price = raw.get("price", 0)
        if isinstance(price, bool) or not isinstance(price, int) or price < 0:
            raise ConfigError(f"В catalog.toml у {label}: price — целое число в рублях (без кавычек, без пробелов).")
        season = raw.get("season")
        if season is not None and not (isinstance(season, list) and len(season) == 2
                                       and all(isinstance(m, int) and 1 <= m <= 12 for m in season)):
            raise ConfigError(f"В catalog.toml у {label}: season — два номера месяцев, например [2, 5].")
        composition = raw.get("composition", [])
        if not isinstance(composition, list):
            raise ConfigError(f"В catalog.toml у {label}: composition — список в квадратных скобках.")
        text_fields = {k: str(raw.get(k, "")).strip() for k in ("short", "description", "care", "badge", "note_prompt")}
        if len(text_fields["short"]) > 100 or len(text_fields["description"]) > 700 or len(text_fields["care"]) > 500:
            raise ConfigError(f"В catalog.toml у {label}: слишком длинный текст "
                              f"(короткое описание до 100, описание до 700, уход до 500 знаков).")
        if len(text_fields["note_prompt"]) > 160:
            raise ConfigError(f"В catalog.toml у {label}: подсказка note_prompt длиннее 160 знаков.")
        card_size = sum(len(text_fields[k]) for k in ("short", "description", "care", "badge")) + len(name) \
            + sum(len(str(c)) + 3 for c in composition)
        if card_size > 900:  # подпись к фото в Telegram не бывает длиннее 1024 знаков
            raise ConfigError(f"В catalog.toml у {label}: все тексты вместе ({card_size} знаков) не помещаются в карточку "
                              f"с фото. Сократите описание, состав или уход — вместе до 900 знаков.")
        result.append({
            "sku": sku, "category": category, "name": name, "price": price, **text_fields,
            "composition": [str(c).strip() for c in composition if str(c).strip()],
            "season_from": season[0] if season else None, "season_to": season[1] if season else None,
            "sort": index,
        })
    return result


def load_catalog_file(path: Path) -> list[dict]:
    return parse_catalog(read_toml(path))


def find_photo(photos_dir: Path, sku: str) -> tuple[str | None, str | None]:
    """Ищет photos/<sku>.jpg (.jpeg/.png/.webp). Возвращает (имя файла, подпись «размер-время»)."""
    for ext in PHOTO_EXTS:
        path = photos_dir / f"{sku}{ext}"
        if path.is_file():
            stat = path.stat()
            return path.name, f"{stat.st_size}-{int(stat.st_mtime)}"
    return None, None


def sync_catalog(db: Database, items: list[dict], photos_dir: Path) -> int:
    """Приводит базу в соответствие с catalog.toml. Наличие («нет сегодня») не трогаем."""
    with db.tx():
        for item in items:
            photo, sig = find_photo(photos_dir, item["sku"])
            db.run(
                """INSERT INTO products (sku, category, name, short, description, composition, care, price, badge,
                                         note_prompt, season_from, season_to, sort, active, photo, photo_sig)
                   VALUES (:sku, :category, :name, :short, :description, :composition, :care, :price, :badge,
                           :note_prompt, :season_from, :season_to, :sort, 1, :photo, :sig)
                   ON CONFLICT(sku) DO UPDATE SET
                       category = excluded.category, name = excluded.name, short = excluded.short,
                       description = excluded.description, composition = excluded.composition, care = excluded.care,
                       price = excluded.price, badge = excluded.badge, note_prompt = excluded.note_prompt,
                       season_from = excluded.season_from,
                       season_to = excluded.season_to, sort = excluded.sort, active = 1,
                       photo_file_id = CASE WHEN products.photo_sig IS excluded.photo_sig THEN products.photo_file_id ELSE NULL END,
                       photo = excluded.photo, photo_sig = excluded.photo_sig""",
                {**item, "composition": json.dumps(item["composition"], ensure_ascii=False), "photo": photo, "sig": sig},
            )
        skus = [i["sku"] for i in items]
        marks = ",".join("?" * len(skus))
        db.run(f"UPDATE products SET active = 0 WHERE sku NOT IN ({marks})", skus)
    return len(items)


def refresh_photo(db: Database, photos_dir: Path, sku: str) -> None:
    photo, sig = find_photo(photos_dir, sku)
    db.run("UPDATE products SET photo = ?, photo_sig = ?, photo_file_id = NULL WHERE sku = ?", (photo, sig, sku))


# ───────────────────────── запросы ─────────────────────────

def get_product(db: Database, sku: str) -> Product | None:
    row = db.one("SELECT * FROM products WHERE sku = ?", (sku,))
    return Product.from_row(row) if row else None


def list_products(db: Database, category: str | None = None, only_active: bool = True) -> list[Product]:
    sql, params = "SELECT * FROM products WHERE 1=1", []
    if only_active:
        sql += " AND active = 1"
    if category:
        sql += " AND category = ?"
        params.append(category)
    return [Product.from_row(r) for r in db.all(sql + " ORDER BY sort, sku", params)]


def neighbors(db: Database, sku: str) -> tuple[Product, Product, int, int] | None:
    """Предыдущий и следующий товар раздела (по кругу), номер этого товара и сколько всего. Для кнопок ◀ ▶."""
    product = get_product(db, sku)
    if product is None or not product.active:
        return None
    items = list_products(db, product.category)
    index = next((i for i, p in enumerate(items) if p.sku == sku), None)
    if index is None:
        return None
    return items[index - 1], items[(index + 1) % len(items)], index + 1, len(items)


def set_in_stock(db: Database, sku: str, flag: bool) -> bool:
    return db.run("UPDATE products SET in_stock = ? WHERE sku = ?", (int(flag), sku)).rowcount == 1


def set_photo_file_id(db: Database, sku: str, file_id: str | None) -> None:
    db.run("UPDATE products SET photo_file_id = ? WHERE sku = ?", (file_id, sku))


# ───────────────────────── корзина ─────────────────────────

def cart_lines(db: Database, user_id: int) -> list[CartLine]:
    rows = db.all(
        "SELECT p.*, c.qty AS cart_qty FROM cart c JOIN products p ON p.sku = c.sku "
        "WHERE c.user_id = ? ORDER BY c.rowid", (user_id,))
    return [CartLine(Product.from_row(r), r["cart_qty"]) for r in rows]


def cart_count(db: Database, user_id: int) -> int:
    return db.scalar("SELECT SUM(qty) FROM cart WHERE user_id = ?", (user_id,), 0)


def cart_add(db: Database, user_id: int, sku: str, qty: int = 1) -> str | None:
    """Кладёт товар в корзину. Возвращает текст ошибки или None, если всё хорошо."""
    with db.tx():
        if db.scalar("SELECT 1 FROM products WHERE sku = ? AND active = 1", (sku,)) is None:
            return "Такого товара нет в каталоге."
        current = db.scalar("SELECT qty FROM cart WHERE user_id = ? AND sku = ?", (user_id, sku), 0)
        if current == 0 and db.scalar("SELECT COUNT(*) FROM cart WHERE user_id = ?", (user_id,), 0) >= MAX_LINES:
            return f"В корзине уже {MAX_LINES} разных товаров — это максимум. Оформите заказ или уберите лишнее."
        if current + qty > MAX_QTY:
            return f"Больше {MAX_QTY} штук одного товара в корзину не положить. Для больших заказов напишите менеджеру."
        db.run("INSERT INTO cart (user_id, sku, qty) VALUES (?, ?, ?) "
               "ON CONFLICT(user_id, sku) DO UPDATE SET qty = qty + excluded.qty", (user_id, sku, qty))
    return None


def cart_change(db: Database, user_id: int, sku: str, delta: int) -> None:
    """Меняет количество на delta; если стало 0 или меньше — убирает позицию."""
    with db.tx():
        current = db.scalar("SELECT qty FROM cart WHERE user_id = ? AND sku = ?", (user_id, sku), 0)
        if not current:
            return
        new = min(MAX_QTY, current + delta)
        if new <= 0:
            db.run("DELETE FROM cart WHERE user_id = ? AND sku = ?", (user_id, sku))
        else:
            db.run("UPDATE cart SET qty = ? WHERE user_id = ? AND sku = ?", (new, user_id, sku))


def cart_remove(db: Database, user_id: int, sku: str) -> None:
    db.run("DELETE FROM cart WHERE user_id = ? AND sku = ?", (user_id, sku))


def cart_clear(db: Database, user_id: int) -> None:
    db.run("DELETE FROM cart WHERE user_id = ?", (user_id,))
