import os
import asyncio
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from user_handlers import router, auto_cleanup_daily_products, check_pending_payments
from config import BOT_TOKEN
from database import init_db

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Бот и диспетчер
bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher()
dp.include_router(router)

# Webhook настройки
WEBHOOK_HOST = "https://flowersstories.ru"  # 👉 твой домен с SSL
WEBHOOK_PATH = f"/bot/{BOT_TOKEN}"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"


async def on_startup(app: web.Application):
    logger.info("Инициализация базы данных...")
    init_db()
    logger.info("База данных инициализирована")

    # Запускаем фоновые задачи
    asyncio.create_task(auto_cleanup_daily_products())
    asyncio.create_task(check_pending_payments())

    await bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)
    logger.info(f"Webhook установлен: {WEBHOOK_URL}")


async def on_shutdown(app: web.Application):
    await bot.delete_webhook()
    logger.info("Webhook удалён")


def main():
    app = web.Application()

    # Подключаем aiogram к aiohttp
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, on_startup=on_startup, on_shutdown=on_shutdown)

    # Запускаем веб-сервер (порт 443 для HTTPS)
    web.run_app(app, host="0.0.0.0", port=int(os.getenv("PORT", 443)))


if __name__ == "__main__":
    main()
