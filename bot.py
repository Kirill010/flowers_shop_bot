import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from user_handlers import router as user_router
from config import BOT_TOKEN, DEBUG
from database import init_db
from simple_payments import payment_manager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def on_startup():
    """Действия при запуске приложения"""
    logger.info("🔧 Starting application...")

    # Инициализация базы данных
    logger.info("📀 Initializing database...")
    init_db()

    logger.info("🤖 Starting bot in polling mode...")


async def main():
    """Основная функция приложения"""
    try:
        # Инициализация
        await on_startup()

        # Создаем бота и диспетчер
        bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
        storage = MemoryStorage()
        dp = Dispatcher(storage=storage)

        # Регистрируем роутеры
        dp.include_router(user_router)

        # Запускаем бота в режиме поллинга
        logger.info("🤖 Starting bot in polling mode...")
        await dp.start_polling(bot)

    except KeyboardInterrupt:
        logger.info("⏹️ Application stopped by user")
    except Exception as e:
        logger.error(f"💥 Critical error: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
