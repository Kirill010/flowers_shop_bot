from aiohttp import web
import json
import asyncio
import logging
from typing import Dict, Any
from config import *
import datetime
from webhook_manager import webhook_manager
from database import save_webhook_log

logger = logging.getLogger(__name__)


async def yookassa_webhook_handler(request: web.Request) -> web.Response:
    """
    Обработчик входящих вебхуков от YooKassa
    """
    try:
        # Читаем тело запроса
        body = await request.read()

        # Проверяем подпись
        signature = request.headers.get('X-Webhook-Signature', '')
        if not webhook_manager.verify_webhook_signature(body, signature):
            logger.warning("❌ Неверная подпись вебхука")
            return web.json_response({"error": "Invalid signature"}, status=403)

        # Парсим JSON
        try:
            payload = json.loads(body.decode('utf-8'))
        except json.JSONDecodeError:
            logger.error("❌ Невалидный JSON в вебхуке")
            return web.json_response({"error": "Invalid JSON"}, status=400)

        # Сохраняем лог
        save_webhook_log(
            webhook_id=payload.get('event', 'unknown'),
            event_type=payload.get('event', 'unknown'),
            payment_id=payload.get('object', {}).get('id', 'unknown'),
            status_code=200,
            request_body=body.decode('utf-8')[:1000],  # Ограничиваем размер
            response_text="Processing"
        )

        # Обрабатываем вебхук асинхронно
        asyncio.create_task(process_webhook_async(payload))

        # Немедленно отвечаем YooKassa
        return web.json_response({"status": "accepted"})

    except Exception as e:
        logger.error(f"❌ Ошибка обработки вебхука: {e}")
        return web.json_response({"error": "Internal server error"}, status=500)


async def process_webhook_async(payload: Dict[str, Any]):
    """Асинхронная обработка вебхука"""
    try:
        success = await webhook_manager.process_webhook(payload)
        if success:
            logger.info("✅ Webhook успешно обработан")
        else:
            logger.warning("⚠️ Webhook обработан с ошибками")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка обработки webhook: {e}")


async def health_check(request: web.Request) -> web.Response:
    """Проверка здоровья сервера"""
    return web.json_response({
        "status": "ok",
        "service": "yookassa-webhook",
        "timestamp": datetime.now().isoformat()
    })


def create_webhook_app() -> web.Application:
    """Создает приложение для вебхуков"""
    app = web.Application()

    # Маршруты
    app.router.add_post('/webhook/yookassa', yookassa_webhook_handler)
    app.router.add_get('/health', health_check)
    app.router.add_get('/webhook/yookassa', health_check)

    return app


async def start_webhook_server():
    """Запускает сервер вебхуков"""
    app = create_webhook_app()

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, WEBAPP_HOST, WEBAPP_PORT)
    await site.start()

    logger.info(f"🚀 Webhook server started on {WEBAPP_HOST}:{WEBAPP_PORT}")
    logger.info(f"📨 Webhook URL: https://{WEBHOOK_HOST}/webhook/yookassa")

    return runner


async def stop_webhook_server(runner: web.AppRunner):
    """Останавливает сервер вебхуков"""
    await runner.cleanup()
    logger.info("🛑 Webhook server stopped")
