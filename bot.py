import asyncio
import logging
from aiogram import Bot, Dispatcher
from user_handlers import router as user_router, auto_cleanup_daily_products, check_pending_payments
from database import *
from config import BOT_TOKEN, ADMINS
from simple_payments import *
from certificates import create_certificate_payment
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def check_pending_payments_task(bot):
    """Периодически проверяем pending платежи в БД и обновляем статус"""
    while True:
        try:
            pendings = get_pending_payments()
            for p in pendings:
                payment_id = p['payment_id']
                status = await payment_manager.check_payment_status(payment_id)
                if status and status != p['status']:
                    update_payment_status(payment_id, status)
                    # можно уведомить юзера по user_id
            await asyncio.sleep(30)  # каждые 30 сек
        except Exception as e:
            logger.exception("Error checking pending payments: %s", e)
            await asyncio.sleep(10)


async def main():
    logger.info("Инициализация базы данных...")
    init_db()
    logger.info("База данных инициализирована")

    # Проверяем доступность YooKassa несколькими способами
    logger.info("Проверка доступности YooKassa...")

    # Способ 1: Асинхронная проверка
    yookassa_available = await payment_manager.check_yookassa_availability()
    bot = Bot(token=BOT_TOKEN)
    if not yookassa_available:
        # Способ 2: Синхронная проверка
        logger.warning("Асинхронная проверка failed, пробуем синхронную...")
        try:
            yookassa_available = check_yookassa_sync()
        except:
            yookassa_available = False

    if not yookassa_available:
        logger.error("YooKassa недоступен! Проверьте настройки и интернет-соединение.")

        # Отправляем детальное сообщение админам
        error_msg = (
            "⚠️ <b>YooKassa недоступен!</b>\n\n"
            "Возможные причины:\n"
            "• Неправильные учетные данные\n"
            "• Проблемы с интернет-соединением\n"
            "• Блокировка firewall\n"
            "• Проблемы с SSL сертификатами\n\n"
            "Проверьте:\n"
            "• SHOP_ID и SECRET_KEY в config.py\n"
            "• Интернет-подключение сервера\n"
            "• Доступность api.yookassa.ru"
        )

        for admin_id in ADMINS:
            try:
                await bot.send_message(admin_id, error_msg, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Не удалось отправить сообщение админу {admin_id}: {e}")
    else:
        logger.info("✅ YooKassa доступен")

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
