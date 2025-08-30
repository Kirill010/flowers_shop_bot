import logging
import hmac
import hashlib
import json
from typing import Dict, Optional
from yookassa import Webhook
from config import YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY, WEBHOOK_URL, WEBHOOK_SECRET
from database import register_yookassa_webhook, get_active_webhooks

logger = logging.getLogger(__name__)


class WebhookManager:
    def __init__(self):
        self.secret_key = WEBHOOK_SECRET.encode('utf-8')

    async def setup_webhooks(self):
        """Настройка вебхуков в YooKassa"""
        try:
            # События, которые мы хотим получать
            events = ['payment.succeeded', 'payment.waiting_for_capture', 'payment.canceled']

            for event in events:
                try:
                    # Создаем или обновляем вебхук
                    webhook = Webhook.add({
                        "event": event,
                        "url": WEBHOOK_URL
                    })

                    # Регистрируем в базе данных
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
        """
        Проверяет подпись вебхука от YooKassa
        """
        try:
            # Создаем HMAC подпись
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
        """
        Обрабатывает входящий вебхук от YooKassa
        """
        try:
            event_type = payload.get('event')
            payment_data = payload.get('object', {})
            payment_id = payment_data.get('id')

            logger.info(f"📨 Входящий webhook: {event_type} для платежа {payment_id}")

            # Обрабатываем разные типы событий
            if event_type == 'payment.succeeded':
                return await self._handle_payment_succeeded(payment_data)
            elif event_type == 'payment.waiting_for_capture':
                return await self._handle_payment_waiting(payment_data)
            elif event_type == 'payment.canceled':
                return await self._handle_payment_canceled(payment_data)
            else:
                logger.warning(f"⚠️ Неизвестный тип события: {event_type}")
                return False

        except Exception as e:
            logger.error(f"❌ Ошибка обработки webhook: {e}")
            return False

    async def _handle_payment_succeeded(self, payment_data: Dict) -> bool:
        """Обрабатывает успешный платеж"""
        try:
            payment_id = payment_data['id']
            status = payment_data['status']
            amount = payment_data['amount']['value']
            metadata = payment_data.get('metadata', {})

            logger.info(f"✅ Платеж успешен: {payment_id}, сумма: {amount}")

            # Обновляем статус в базе данных
            from database import update_payment_status, get_payment
            update_payment_status(payment_id, status)

            # Получаем дополнительную информацию о платеже
            payment_info = get_payment(payment_id)
            if payment_info:
                user_id = payment_info['user_id']

                # Создаем заказ если это нужно
                if metadata.get('type') == 'order':
                    await self._create_order_from_payment(payment_info, payment_data)

                # Создаем чек если нужно
                if metadata.get('email'):
                    from receipts import receipt_manager
                    await receipt_manager.create_receipt(payment_id, metadata['email'])

            return True

        except Exception as e:
            logger.error(f"❌ Ошибка обработки успешного платежа: {e}")
            return False

    async def _handle_payment_waiting(self, payment_data: Dict) -> bool:
        """Обрабатывает платеж, ожидающий подтверждения"""
        payment_id = payment_data['id']
        logger.info(f"⏳ Платеж ожидает подтверждения: {payment_id}")

        from database import update_payment_status
        update_payment_status(payment_id, 'waiting_for_capture')

        return True

    async def _handle_payment_canceled(self, payment_data: Dict) -> bool:
        """Обрабатывает отмененный платеж"""
        payment_id = payment_data['id']
        logger.info(f"❌ Платеж отменен: {payment_id}")

        from database import update_payment_status
        update_payment_status(payment_id, 'canceled')

        return True

    async def _create_order_from_payment(self, payment_info: Dict, payment_data: Dict):
        """Создает заказ из данных платежа"""
        try:
            metadata = payment_info.get('metadata', {})
            if isinstance(metadata, str):
                metadata = json.loads(metadata)

            from database import create_order
            order_id = create_order(
                user_id=payment_info['user_id'],
                name=metadata.get('name', ''),
                phone=metadata.get('phone', ''),
                address=metadata.get('address', ''),
                delivery_date=metadata.get('delivery_date', ''),
                delivery_time=metadata.get('delivery_time', ''),
                payment=metadata.get('payment_method', 'online'),
                delivery_cost=float(metadata.get('delivery_cost', 0)),
                delivery_type=metadata.get('delivery_type', 'delivery'),
                email=metadata.get('email'),
                bonus_used=int(metadata.get('bonus_used', 0))
            )

            if order_id != -1:
                logger.info(f"📦 Заказ создан: #{order_id} из платежа {payment_info['payment_id']}")

                # Очищаем корзину
                from database import clear_cart
                clear_cart(payment_info['user_id'])

        except Exception as e:
            logger.error(f"❌ Ошибка создания заказа из платежа: {e}")


# Глобальный экземпляр менеджера вебхуков
webhook_manager = WebhookManager()
