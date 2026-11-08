"""Общие заготовки для тестов: настоящие shop.toml и catalog.toml + база в памяти + «замороженные» часы."""
from __future__ import annotations

import dataclasses
from datetime import date, datetime, timezone
from pathlib import Path

from flowerbot import catalog
from flowerbot.config import ROOT, Settings, Shop, load_settings, load_shop
from flowerbot.db import Database

VALID_TOKEN = "123456789:AAEexampleExampleExampleExampleExample1"
ADMIN = 1000000001
CUSTOMER = 2000000002
OTHER = 3000000003

# Понедельник, 9 ноября 2026, 12:00 по Москве
NOW = datetime(2026, 11, 9, 9, 0, tzinfo=timezone.utc)
TODAY = date(2026, 11, 9)


def detach_routers() -> None:
    """Роутеры бота — общие объекты на весь процесс. В бою диспетчер один; в тестах их много, и каждый
    должен получить «свободные» роутеры."""
    from flowerbot import handlers
    for router in handlers.all_routers():
        router._parent_router = None


def real_shop(**changes) -> Shop:
    shop = load_shop(ROOT / "shop.toml")
    return dataclasses.replace(shop, **changes) if changes else shop


def make_settings(root: Path = ROOT, **env) -> Settings:
    values = {"BOT_TOKEN": VALID_TOKEN, "ADMIN_IDS": str(ADMIN)}
    values.update(env)
    return load_settings(root, environ=values)


def make_db(now: datetime = NOW) -> Database:
    db = Database(":memory:", clock=lambda: now)
    catalog.sync_catalog(db, catalog.load_catalog_file(ROOT / "catalog.toml"), ROOT / "photos")
    return db
