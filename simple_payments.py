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

                # Возвращаем правильную структуру
                return {
                    "id": payment.id,
                    "status": payment.status,
                    "confirmation_url": payment.confirmation.confirmation_url if payment.confirmation else None,
                    "amount": amount
                }
            except Exception as e:
                logger.error(f"Attempt {attempt + 1} failed: {e}")
                if attempt < self.retry_attempts - 1:
                    await asyncio.sleep(self.retry_delay)
        return None

    async def check_payment_status(self, payment_id: str) -> Optional[str]:
        """Проверка статуса платежа в ЮKassa"""
        for attempt in range(self.retry_attempts):
            try:
                payment = Payment.find_one(payment_id)
                return payment.status
            except Exception as e:
                logger.error(f"Payment status check attempt {attempt + 1} failed: {e}")
                if attempt < self.retry_attempts - 1:
                    await asyncio.sleep(self.retry_delay)
        return None


payment_manager = SimplePaymentManager()
