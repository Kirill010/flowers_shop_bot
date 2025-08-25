import asyncio
import logging
from aiogram import Bot, Dispatcher
from user_handlers import router as user_router, auto_cleanup_daily_products
# from aiogram.client.session.aiohttp import AiohttpSession
from config import BOT_TOKEN
from database import init_db, add_product

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    logger.info("Инициализация базы данных...")
    init_db()
    logger.info("База данных инициализирована")
    # session = AiohttpSession(
    #     proxy="http://proxy.server:3128"
    # )
    # bot = Bot(token=BOT_TOKEN, session=session)
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    # Регистрируем роутеры
    dp.include_router(user_router)

    # Запускаем фоновую задачу очистки
    asyncio.create_task(auto_cleanup_daily_products())

    # Запускаем бота
    logger.info("Starting bot...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
