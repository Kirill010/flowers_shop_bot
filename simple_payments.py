from yookassa import Payment, Configuration
import uuid
import asyncio
from typing import Optional
import logging
import aiohttp
from config import YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY

# Настройка логирования
logger = logging.getLogger(__name__)

# Настройка ЮKassa ОДИН РАЗ в начале файла
Configuration.account_id = YOOKASSA_SHOP_ID
Configuration.secret_key = YOOKASSA_SECRET_KEY
Configuration.timeout = 30  # Увеличиваем таймаут
Configuration.max_attempts = 3  # 3 попытки


class SimplePaymentManager:
    def __init__(self):
        self.retry_attempts = 3
        self.retry_delay = 2

    async def check_yookassa_availability(self) -> bool:
        """Проверяет, доступен ли сервер YooKassa"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                        'https://api.yookassa.ru/v3/',
                        timeout=10,
                        auth=aiohttp.BasicAuth(YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY)
                ) as response:
                    return response.status == 200
        except Exception as e:
            logger.error(f"❌ YooKassa недоступен: {e}")
            return False

    async def create_payment(self, amount: int, description: str, metadata: dict) -> dict:
        """Создает платеж в ЮKassa"""
        for attempt in range(self.retry_attempts):
            try:
                logger.info(f"🔄 Попытка {attempt + 1} создать платеж на {amount} руб.")

                # Создаем уникальный ID для платежа
                idempotency_key = str(uuid.uuid4())

                # Данные для платежа
                payment_data = {
                    "amount": {
                        "value": f"{amount:.2f}",
                        "currency": "RUB"
                    },
                    "confirmation": {
                        "type": "redirect",
                        "return_url": "https://t.me/Therry_Voyager"
                    },
                    "capture": True,
                    "description": description,
                    "metadata": metadata
                }

                # СОЗДАЕМ ПЛАТЕЖ
                payment = Payment.create(payment_data, idempotency_key)

                if hasattr(payment, 'confirmation') and hasattr(payment.confirmation, 'confirmation_url'):
                    logger.info(f"✅ Платеж создан! ID: {payment.id}")

                    return {
                        "id": payment.id,
                        "status": payment.status,
                        "confirmation_url": payment.confirmation.confirmation_url,
                        "amount": amount
                    }
                else:
                    logger.error("❌ Не удалось получить confirmation_url")
                    return None

            except Exception as e:
                logger.error(f"❌ Ошибка при создании платежа (попытка {attempt + 1}): {e}")
                if attempt < self.retry_attempts - 1:
                    await asyncio.sleep(self.retry_delay)
                else:
                    logger.error("❌ Все попытки создать платеж провалились")
                    return None

    async def check_payment_status(self, payment_id: str) -> Optional[str]:
        """Проверяет статус платежа в ЮKassa"""
        for attempt in range(self.retry_attempts):
            try:
                logger.info(f"🔄 Проверка статуса платежа {payment_id}")

                # Ищем информацию о платеже
                payment = Payment.find_one(payment_id)

                logger.info(f"✅ Статус платежа {payment_id}: {payment.status}")
                return payment.status

            except Exception as e:
                logger.error(f"❌ Ошибка проверки статуса (попытка {attempt + 1}): {e}")
                if attempt < self.retry_attempts - 1:
                    await asyncio.sleep(self.retry_delay)

        logger.error(f"❌ Не удалось проверить статус платежа {payment_id}")
        return None

    async def capture_payment(self, payment_id: str) -> bool:
        """Подтверждает платеж"""
        try:
            payment = Payment.capture(payment_id)
            return payment.status == 'succeeded'
        except Exception as e:
            logger.error(f"❌ Ошибка подтверждения платежа {payment_id}: {e}")
            return False


# Создаем глобальный объект для работы с платежами
payment_manager = SimplePaymentManager()
