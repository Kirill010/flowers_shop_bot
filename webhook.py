# webhook_launcher.py
import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from user_handlers import router as user_router
from database import init_db
from config import *
import subprocess
import time

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

def start_ngrok_tunnel(port: int):
    """Запускает ngrok туннель"""
    try:
        # Запускаем ngrok в фоновом режиме
        ngrok_process = subprocess.Popen([
            'ngrok', 'http', str(port),
            '--log=stdout'
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        # Даем ngrok время на запуск
        time.sleep(3)

        # Получаем URL туннеля
        import requests
        response = requests.get('http://localhost:4040/api/tunnels')
        tunnels = response.json()['tunnels']

        public_url = None
        for tunnel in tunnels:
            if tunnel['proto'] == 'https':
                public_url = tunnel['public_url']
                break

        if public_url:
            logger.info(f"Ngrok туннель запущен: {public_url}")
            return public_url, ngrok_process
        else:
            raise Exception("Не удалось получить публичный URL от ngrok")

    except Exception as e:
        logger.error(f"Ошибка запуска ngrok: {e}")
        return None, None

def main():
    # Инициализация базы данных
    init_db()

    # Запускаем ngrok туннель ПЕРВЫМ ДЕЛОМ
    public_url, ngrok_process = start_ngrok_tunnel(WEBHOOK_PORT)

    if not public_url:
        logger.error("Не удалось запустить ngrok. Запускаем в режиме polling.")
        # Запускаем в обычном режиме, если ngrok не работает
        bot = Bot(token=BOT_TOKEN)
        dp = Dispatcher()
        dp.include_router(user_router)
        asyncio.run(dp.start_polling(bot))
        return

    # Создаем экземпляры бота и диспетчера
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    # Регистрируем роутеры
    dp.include_router(user_router)

    # Регистрируем обработчики startup/shutdown
    dp.startup.register(lambda: on_startup(bot, public_url))
    dp.shutdown.register(lambda: on_shutdown(bot))

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

    try:
        # Запускаем приложение
        logger.info(f"Запуск сервера на порту {WEBHOOK_PORT}")
        web.run_app(
            app,
            host='0.0.0.0',
            port=WEBHOOK_PORT,
            print=lambda *args: logger.info("Сервер запущен")
        )
    except KeyboardInterrupt:
        logger.info("Остановка сервера...")
    finally:
        # Останавливаем ngrok при завершении
        if ngrok_process:
            ngrok_process.terminate()

if __name__ == "__main__":
    main()