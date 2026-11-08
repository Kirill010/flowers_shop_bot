"""Сборка и запуск бота."""
from __future__ import annotations

import asyncio
import logging
import logging.handlers
import sys
import time

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import PRODUCTION, TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError, TelegramUnauthorizedError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, ErrorEvent

from . import __version__, billing, catalog, handlers, notify
from .config import ConfigError, Settings, load_settings, load_shop
from .ctx import Ctx
from .db import Database
from .middlewares import MenuInterrupt, Throttle, UserMiddleware
from .yookassa import YooKassaClient

log = logging.getLogger("flowerbot")

COMMANDS = [BotCommand(command="menu", description="Главное меню"), BotCommand(command="cart", description="Корзина"),
            BotCommand(command="orders", description="Мои заказы"), BotCommand(command="help", description="Помощь")]


class RedactSecrets(logging.Filter):
    """Вычёркивает ключи из журнала: даже в случае ошибки токен не попадёт в файл или скриншот."""

    def __init__(self, secrets: list[str]):
        super().__init__()
        self.secrets = [s for s in secrets if s]

    def filter(self, record: logging.LogRecord) -> bool:
        if self.secrets:
            text = record.getMessage()
            for secret in self.secrets:
                text = text.replace(secret, "***")
            record.msg, record.args = text, None
        return True


def setup_logging(settings: Settings) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s %(name)s: %(message)s", "%d.%m %H:%M:%S")
    console = logging.StreamHandler(sys.stdout)
    file = logging.handlers.RotatingFileHandler(settings.data_dir / "bot.log", maxBytes=1_000_000, backupCount=3,
                                                encoding="utf-8")
    redact = RedactSecrets([settings.bot_token, settings.yookassa_secret_key])
    root = logging.getLogger()
    root.handlers.clear()
    for handler in (console, file):
        handler.setFormatter(fmt)
        handler.addFilter(redact)
        root.addHandler(handler)
    root.setLevel(getattr(logging, settings.log_level))
    logging.getLogger("aiogram").setLevel(logging.INFO if settings.log_level == "DEBUG" else logging.WARNING)


def build_dispatcher(ctx: Ctx, throttle: Throttle | None = None) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp["ctx"] = ctx
    throttle = throttle or Throttle()
    for observer in (dp.message, dp.callback_query):
        observer.outer_middleware(throttle)
        observer.outer_middleware(UserMiddleware())
    dp.message.outer_middleware(MenuInterrupt())
    for router in handlers.all_routers():
        dp.include_router(router)

    last_alert = {"at": -1e9}

    @dp.errors()
    async def on_error(event: ErrorEvent, bot: Bot) -> bool:
        log.error("Ошибка при обработке сообщения: %r", event.exception, exc_info=event.exception)
        update = event.update
        try:
            if update.message:
                await update.message.answer(f"Что-то пошло не так 😔 Попробуйте ещё раз или позвоните нам: {ctx.shop.phone}")
            elif update.callback_query:
                await update.callback_query.answer("Что-то пошло не так. Попробуйте ещё раз.", show_alert=True)
        except TelegramAPIError:
            pass
        if time.monotonic() - last_alert["at"] > 600:  # сотрудников предупреждаем не чаще раза в 10 минут
            last_alert["at"] = time.monotonic()
            await notify.to_staff(bot, ctx, f"⚠️ В боте произошла ошибка ({event.exception.__class__.__name__}). "
                                            "Подробности — в файле data/bot.log. Клиенту мы извинились.")
        return True

    return dp


def make_session(settings: Settings) -> AiohttpSession:
    api = TelegramAPIServer.from_base(settings.api_url.rstrip("/")) if settings.api_url else PRODUCTION
    return AiohttpSession(api=api, proxy=settings.proxy_url or None)


async def set_default_descriptions(bot: Bot, shop_name: str) -> None:
    """Текст, который видит клиент в пустом чате до нажатия «Старт». Только если владелец сам ничего не задавал в @BotFather."""
    try:
        if not (await bot.get_my_description()).description:
            await bot.set_my_description(f"{shop_name}: каталог букетов и растений, доставка, самовывоз и подарочные "
                                         "сертификаты. Нажмите «Начать» 🌸")
        if not (await bot.get_my_short_description()).short_description:
            await bot.set_my_short_description(f"{shop_name} — цветы с доставкой и самовывозом")
    except TelegramAPIError as exc:
        log.debug("Описание бота не обновлено: %s", exc)


async def run(settings: Settings) -> None:
    shop = load_shop(settings.shop_path)
    items = catalog.load_catalog_file(settings.catalog_path)
    settings.photos_dir.mkdir(parents=True, exist_ok=True)  # сюда кладут фото товаров: photos\classic.jpg
    db = Database(settings.db_path)
    catalog.sync_catalog(db, items, settings.photos_dir)
    yookassa = (YooKassaClient(settings.yookassa_shop_id, settings.yookassa_secret_key,
                               receipt=settings.yookassa_receipt, vat_code=settings.yookassa_vat_code)
                if settings.yookassa_enabled else None)
    ctx = Ctx(settings, db, shop, yookassa)
    bot = Bot(settings.bot_token, session=make_session(settings),
              default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
    payments_task: asyncio.Task | None = None
    try:
        try:
            me = await bot.get_me()
        except TelegramUnauthorizedError:
            raise ConfigError(
                "Telegram не принял BOT_TOKEN из файла .env: ключ неверный или его отозвали.\n"
                "Откройте @BotFather → /mybots → ваш бот → API Token, скопируйте ключ заново и вставьте в .env.") from None
        except (TelegramNetworkError, asyncio.TimeoutError, OSError):
            raise ConfigError(
                "Не получается подключиться к Telegram. Проверьте интернет.\n"
                "Если Telegram у вас открывается только через VPN или прокси — впишите адрес прокси в .env "
                "в строку PROXY_URL (например socks5://127.0.0.1:1080).") from None
        ctx.bot_username = me.username or ""
        await bot.delete_webhook(drop_pending_updates=False)  # на случай, если на бота чужой веб-хук
        await bot.set_my_commands(COMMANDS)
        await set_default_descriptions(bot, shop.name)
        dp = build_dispatcher(ctx)

        log.info("Бот @%s запущен (версия %s). Магазин: %s", ctx.bot_username, __version__, shop.name)
        log.info("Заказы получают: %s. Онлайн-оплата: %s. Каталог: %d позиций.",
                 f"общий чат {settings.orders_chat_id}" if settings.orders_chat_id else
                 (f"{len(settings.admin_ids)} адм." if settings.admin_ids else "НИКТО (не заполнен ADMIN_IDS)"),
                 "включена" if yookassa else "выключена", len(items))
        if not ctx.staff_ready:
            log.warning("Режим настройки: ADMIN_IDS пуст, поэтому заказы не принимаются. Напишите боту /myid, "
                        "впишите номер в .env (ADMIN_IDS=...) и перезапустите бота.")
        else:
            await notify.to_staff(bot, ctx, f"✅ Бот запущен (версия {__version__}). Панель: /admin")

        payments_task = asyncio.create_task(billing.payments_loop(ctx, bot))
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        if payments_task:
            payments_task.cancel()
        if yookassa:
            await yookassa.close()
        await bot.session.close()
        db.close()


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"\n❌ {exc}\n")
        return 2
    setup_logging(settings)
    try:
        asyncio.run(run(settings))
    except ConfigError as exc:
        print(f"\n❌ {exc}\n")
        return 2
    except KeyboardInterrupt:
        print("\nБот остановлен.")
    return 0
