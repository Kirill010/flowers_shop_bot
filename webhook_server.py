from aiohttp import web
import json
import logging
from config import WEBAPP_HOST, WEBAPP_PORT
from webhook_manager import webhook_manager

logger = logging.getLogger(__name__)


async def yookassa_webhook_handler(request: web.Request) -> web.Response:
    """Упрощенный обработчик вебхуков"""
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

        # Обрабатываем вебхук
        success = await webhook_manager.process_webhook(payload)

        if success:
            return web.json_response({"status": "processed"})
        else:
            return web.json_response({"status": "failed"}, status=400)

    except Exception as e:
        logger.error(f"❌ Ошибка обработки вебхука: {e}")
        return web.json_response({"error": "Internal server error"}, status=500)


async def health_check(request: web.Request) -> web.Response:
    """Проверка здоровья сервера"""
    return web.json_response({"status": "ok", "service": "yookassa-webhook"})


def create_webhook_app() -> web.Application:
    """Создает приложение для вебхуков"""
    app = web.Application()
    app.router.add_post('/webhook/yookassa', yookassa_webhook_handler)
    app.router.add_get('/health', health_check)
    return app


async def start_webhook_server():
    """Запускает сервер вебхуков"""
    app = create_webhook_app()
    runner = web.AppRunner(app)
    await runner.setup()

    try:
        site = web.TCPSite(runner, WEBAPP_HOST, WEBAPP_PORT)
        await site.start()
        logger.info(f"🚀 Webhook server started on {WEBAPP_HOST}:{WEBAPP_PORT}")
        return runner
    except Exception as e:
        logger.error(f"❌ Failed to start webhook server: {e}")
        # Fallback - используем поллинг вместо вебхуков
        return None
