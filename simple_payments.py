import traceback

from yookassa import Payment, Configuration
import uuid
import asyncio
from typing import Optional
import logging
from config import YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY

logger = logging.getLogger(__name__)

# Настройка ЮKassa
Configuration.account_id = YOOKASSA_SHOP_ID
Configuration.secret_key = YOOKASSA_SECRET_KEY


class SimplePaymentManager:
    def __init__(self):
        self.retry_attempts = 3
        self.retry_delay = 2

    async def create_payment(self, amount: int, description: str, metadata: dict) -> dict:
        logger.info(f"Создание платежа: {amount} RUB, {description}")

        for attempt in range(self.retry_attempts):
            try:
                payment = Payment.create({
                    "amount": {
                        "value": str(amount),
                        "currency": "RUB"
                    },
                    "confirmation": {
                        "type": "redirect",
                        "return_url": "https://t.me/flowersstories_bot"
                    },
                    "capture": True,
                    "description": description,
                    "metadata": metadata,
                    "receipt": {
                        "customer": {"phone": metadata.get("phone", "9999999999")},
                        "items": [
                            {
                                "description": description,
                                "quantity": 1,
                                "amount": {
                                    "value": str(amount),
                                    "currency": "RUB"
                                },
                                "vat_code": 1,
                                "payment_mode": "full_payment",
                                "payment_subject": "commodity"
                            }
                        ]
                    }
                })

                logger.info(f"Платеж создан: {payment.id}, статус: {payment.status}")

                # Возвращаем правильную структуру
                return {
                    "id": payment.id,
                    "status": payment.status,
                    "confirmation_url": payment.confirmation.confirmation_url if payment.confirmation else None,
                    "amount": amount
                }
            except Exception as e:
                logger.error(f"Attempt {attempt + 1} failed: {e}")
                logger.error(traceback.format_exc())
                if attempt < self.retry_attempts - 1:
                    await asyncio.sleep(self.retry_delay)
        return None

    async def check_payment_status(self, payment_id: str) -> Optional[str]:
        """Проверка статуса платежа в ЮKassa"""
        logger.info(f"Проверка статуса платежа: {payment_id}")

        try:
            loop = asyncio.get_event_loop()
            payment = await loop.run_in_executor(None, lambda: Payment.find_one(payment_id))
            logger.info(f"Статус платежа {payment_id}: {payment.status}")
            return payment.status
        except Exception as e:
            logger.error(f"Ошибка проверки статуса: {e}")
            return None


payment_manager = SimplePaymentManager()
