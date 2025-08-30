import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from user_handlers import router as user_router
from config import BOT_TOKEN, DEBUG, WEBAPP_HOST, WEBAPP_PORT
from database import init_db
from webhook_manager import webhook_manager
from webhook_server import start_webhook_server, stop_webhook_server
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

    # Настройка вебхуков в YooKassa
    logger.info("🌐 Setting up YooKassa webhooks...")
    await webhook_manager.setup_webhooks()

    # Проверка подключения к YooKassa
    logger.info("🔍 Checking YooKassa connection...")
    try:
        # Простая проверка через создание тестового платежа
        test_payment = await payment_manager.create_payment(
            user_id=1,
            amount=1.00,
            description="Test connection",
            metadata={"type": "test"}
        )
        logger.info(f"✅ YooKassa connection successful. Test payment: {test_payment['id']}")
    except Exception as e:
        logger.error(f"❌ YooKassa connection failed: {e}")
        # Не прерываем работу, так как вебхуки могут работать


async def on_shutdown():
    """Действия при остановке приложения"""
    logger.info("🛑 Shutting down application...")


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

        # Запускаем сервер вебхуков
        webhook_runner = await start_webhook_server()

        # Запускаем бота
        logger.info("🤖 Starting bot...")

        try:
            await dp.start_polling(bot)
        except Exception as e:
            logger.error(f"❌ Bot polling error: {e}")
        finally:
            # Корректное завершение
            await bot.session.close()
            await stop_webhook_server(webhook_runner)
            await on_shutdown()

    except KeyboardInterrupt:
        logger.info("⏹️ Application stopped by user")
    except Exception as e:
        logger.error(f"💥 Critical error: {e}")
        raise


if __name__ == "__main__":
    # Запускаем главный цикл
    asyncio.run(main())
