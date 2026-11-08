"""Общий «портфель» бота: настройки, база, магазин, оплата. Передаётся во все обработчики."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from . import catalog, schedule
from .config import Settings, Shop, load_shop
from .db import Database
from .yookassa import YooKassaClient

log = logging.getLogger(__name__)


@dataclass
class Ctx:
    settings: Settings
    db: Database
    shop: Shop
    yookassa: YooKassaClient | None = None
    bot_username: str = ""

    def now(self) -> datetime:
        """Текущее время (UTC). Единый источник времени: в тестах его подменяют."""
        return self.db.clock()

    def local_now(self) -> datetime:
        return schedule.local_now(self.shop, self.now())

    def is_admin(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self.settings.admin_ids

    @property
    def staff_ready(self) -> bool:
        """Есть ли кому присылать заказы. Без этого заказы принимать нельзя — они бы потерялись."""
        return bool(self.settings.admin_ids or self.settings.orders_chat_id)

    @property
    def online_enabled(self) -> bool:
        return self.yookassa is not None

    @property
    def paused(self) -> bool:
        return self.shop.paused or self.db.get_meta("paused") == "1"

    def reload(self) -> int:
        """Перечитывает shop.toml и catalog.toml без перезапуска. Возвращает число товаров."""
        self.shop = load_shop(self.settings.shop_path)
        items = catalog.load_catalog_file(self.settings.catalog_path)
        return catalog.sync_catalog(self.db, items, self.settings.photos_dir)
