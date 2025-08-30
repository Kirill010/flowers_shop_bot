import logging
import hmac
import hashlib
import json
from typing import Dict, Optional
from yookassa import Webhook
from config import YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY, WEBHOOK_URL, WEBHOOK_SECRET
from database import register_yookassa_webhook, get_active_webhooks, update_payment_status

logger = logging.getLogger(__name__)


class WebhookManager:
    def __init__(self):
        self.secret_key = WEBHOOK_SECRET.encode('utf-8')

    async def setup_webhooks(self):
        """Настройка вебхуков в YooKassa"""
        try:
            events = ['payment.succeeded', 'payment.waiting_for_capture', 'payment.canceled']

            for event in events:
                try:
                    webhook = Webhook.add({
                        "event": event,
                        "url": f"{WEBHOOK_URL}/webhook/yookassa"
                    })

                    register_yookassa_webhook(
                        webhook.id,
                        webhook.event,
                        webhook.url
                    )

                    logger.info(f"✅ Webhook настроен для события: {event}")

                except Exception as e:
                    logger.error(f"❌ Ошибка настройки webhook для {event}: {e}")

        except Exception as e:
            logger.error(f"❌ Критическая ошибка настройки webhooks: {e}")

    def verify_webhook_signature(self, body: bytes, signature: str) -> bool:
        """Проверяет подпись вебхука от YooKassa"""
        try:
            expected_signature = hmac.new(
                self.secret_key,
                body,
                hashlib.sha256
            ).hexdigest()

            return hmac.compare_digest(expected_signature, signature)

        except Exception as e:
            logger.error(f"❌ Ошибка проверки подписи: {e}")
            return False

    async def process_webhook(self, payload: Dict) -> bool:
        """Обрабатывает входящий вебхук от YooKassa"""
        try:
            event_type = payload.get('event')
            payment_data = payload.get('object', {})
            payment_id = payment_data.get('id')

            logger.info(f"📨 Входящий webhook: {event_type} для платежа {payment_id}")

            # Обновляем статус платежа в базе данных
            if event_type in ['payment.succeeded', 'payment.waiting_for_capture', 'payment.canceled']:
                status_map = {
                    'payment.succeeded': 'succeeded',
                    'payment.waiting_for_capture': 'waiting_for_capture',
                    'payment.canceled': 'canceled'
                }
                update_payment_status(payment_id, status_map[event_type])
                logger.info(f"✅ Статус платежа {payment_id} обновлен на: {status_map[event_type]}")
                return True

            return False

        except Exception as e:
            logger.error(f"❌ Ошибка обработки webhook: {e}")
            return False


# Глобальный экземпляр менеджера вебхуков
webhook_manager = WebhookManager()
