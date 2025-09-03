import asyncio
import logging
from aiogram import Bot, Dispatcher
from user_handlers import router as user_router, auto_cleanup_daily_products, check_pending_payments
from config import BOT_TOKEN
from database import init_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    logger.info("Инициализация базы данных...")
    init_db()
    logger.info("База данных инициализирована")

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    # Регистрируем роутеры
    dp.include_router(user_router)

    # Запускаем фоновые задачи
    asyncio.create_task(auto_cleanup_daily_products())
    asyncio.create_task(check_pending_payments())

    # Запускаем бота
    logger.info("Starting bot...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

# /add
# /update_catalog
# /debug
# /mark_delivered
# /reviews_debug
# /start
# /admin
# /myid
# /edit_price
# /pending_prices
# deactivate
# rm -rf venv
# python -m venv venv
# source venv/bin/activate
# pip install aiohttp_socks
# ps aux | grep "python bot.py"
# pgrep -f "python bot.py"
# pkill -f "python bot.py"
# nohup python bot.py > bot.log &
# nohup python bot.py > bot.log 2>&1 &
# nano bot_manager.sh
# ./bot_manager.sh stop
# ./bot_manager.sh start
# ./bot_manager.sh status
