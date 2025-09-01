import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from user_handlers import router as user_router
from database import init_db
from config import *

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def on_startup(bot: Bot, base_url: str):
    """Действия при запуске бота"""
    logger.info("Бот запускается...")

    # Устанавливаем вебхук
    await bot.set_webhook(
        f"{base_url}{WEBHOOK_PATH}",
        secret_token=WEBHOOK_SECRET
    )
    logger.info(f"Вебхук установлен: {base_url}{WEBHOOK_PATH}")


async def on_shutdown(bot: Bot):
    """Действия при остановке бота"""
    logger.info("Бот останавливается...")
    await bot.delete_webhook()
    logger.info("Вебхук удален")


def main():
    # Инициализация базы данных
    init_db()

    # Создаем экземпляры бота и диспетчера
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    # Регистрируем роутеры
    dp.include_router(user_router)

    # Регистрируем обработчики startup/shutdown
    dp.startup.register(lambda: on_startup(bot, WEBHOOK_HOST))
    dp.shutdown.register(on_shutdown)

    # Создаем aiohttp приложение
    app = web.Application()

    # Создаем обработчик вебхуков
    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=WEBHOOK_SECRET,
    )

    # Регистрируем обработчик
    webhook_requests_handler.register(app, path=WEBHOOK_PATH)

    # Запускаем приложение
    logger.info(f"Запуск сервера на порту {WEBHOOK_PORT}")
    web.run_app(app, host='0.0.0.0', port=WEBHOOK_PORT)


if __name__ == "__main__":
    main()
