"""Резервные копии базы: раз в сутки, хранятся 14 последних. Клиенты, заказы и сертификаты не должны пропасть
из-за сбоя диска или случайно удалённого файла.

Копии лежат в data\\backups\\shop-ГГГГ-ММ-ДД.db. Чтобы вернуть базу: закройте бота и скопируйте нужный файл
поверх data\\shop.db (а файлы shop.db-wal и shop.db-shm рядом удалите)."""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path

from .ctx import Ctx

log = logging.getLogger(__name__)
KEEP = 14  # сколько последних копий хранить
CHECK_SECONDS = 3600  # как часто проверять, не пора ли сделать копию на новый день


def backup_path(folder: Path, day) -> Path:
    return folder / f"shop-{day:%Y-%m-%d}.db"


def prune(folder: Path, keep: int = KEEP) -> list[Path]:
    """Удаляет самые старые копии, оставляя `keep` последних. Возвращает удалённые."""
    copies = sorted(folder.glob("shop-????-??-??.db"))
    old = copies[:-keep] if keep > 0 else copies
    for path in old:
        path.unlink(missing_ok=True)
    return old


def make_backup(ctx: Ctx, folder: Path, keep: int = KEEP) -> Path | None:
    """Делает копию базы на сегодня, если её ещё нет. Возвращает путь к новой копии или None (копия уже есть).
    Копия снимается средствами SQLite, поэтому она целая, даже если бот в этот момент работает."""
    target = backup_path(folder, ctx.local_now().date())
    if target.exists():
        return None
    folder.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    try:
        copy = sqlite3.connect(partial)
        try:
            ctx.db.conn.backup(copy)
            if copy.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("копия не прошла проверку целостности")
        finally:
            copy.close()
        partial.replace(target)  # в папке появляется только целая копия
    finally:
        partial.unlink(missing_ok=True)
    prune(folder, keep)
    return target


async def backup_loop(ctx: Ctx, interval: float = CHECK_SECONDS) -> None:
    """Фоновая задача: копия при запуске (если на сегодня ещё нет) и затем раз в сутки. Ошибка копирования
    только пишется в журнал — боту она не мешает."""
    folder = ctx.settings.data_dir / "backups"
    while True:
        try:
            created = make_backup(ctx, folder)
            if created:
                log.info("Сделана резервная копия базы: %s", created.name)
        except Exception:
            log.exception("Не удалось сделать резервную копию базы")
        await asyncio.sleep(interval)
