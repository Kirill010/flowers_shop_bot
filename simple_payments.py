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
                # Получаем телефон из metadata
                user_phone = metadata.get("phone") or metadata.get("user_phone", "9999999999")

                payment_data = {
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
                        "customer": {"phone": user_phone},
                        "items": [
                            {
                                "description": description[:128],
                                "quantity": "1.00",
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
                }

                # Убираем receipt для очень маленьких сумм (менее 1 рубля)
                if amount < 1:
                    payment_data.pop("receipt", None)

                payment = Payment.create(payment_data)

                logger.info(f"Платеж создан: {payment.id}, статус: {payment.status}")

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


payment_manager = SimplePaymentManager()
