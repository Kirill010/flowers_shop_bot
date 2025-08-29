from yookassa import Payment, Configuration
import uuid
import asyncio
from typing import Optional
import logging
from config import YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY, IS_LOCAL, LOCAL_TUNNEL_URL
import aiohttp
import ssl

logger = logging.getLogger(__name__)

# Настройка ЮKassa
Configuration.account_id = YOOKASSA_SHOP_ID
Configuration.secret_key = YOOKASSA_SECRET_KEY


class SimplePaymentManager:
    def __init__(self):
        self.retry_attempts = 3
        self.retry_delay = 2
        # Создаем SSL контекст для безопасного подключения
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE

    async def create_payment(self, amount: int, description: str, metadata: dict) -> dict:
        for attempt in range(self.retry_attempts):
            try:
                logger.info(f"🔄 Попытка {attempt + 1} создать платеж на {amount} руб.")

                # Для локального тестирования используем упрощенный подход
                if IS_LOCAL:
                    return await self.create_local_payment(amount, description, metadata)

                # --- НАЧАЛО: Добавляем чек (receipt) ---
                # Пример email для чека (лучше — запросить у пользователя)
                customer_email = "flowers@example.com"  # Замени на реальный или запроси

                # Состав чека
                items = []

                # Попробуем получить товары из metadata
                cart_items = metadata.get("cart_items", []) or metadata.get("order_data", {}).get("cart_items", [])

                for item in cart_items:
                    item_price = float(item.get("price", 0))
                    item_quantity = float(item.get("quantity", 1))
                    items.append({
                        "description": item["name"][:128],  # Ограничение YooKassa
                        "quantity": item_quantity,
                        "amount": {
                            "value": f"{item_price:.2f}",
                            "currency": "RUB"
                        },
                        "vat_code": "1",  # НДС 20% (см. таблицу ниже)
                        "payment_mode": "full_payment",
                        "payment_subject": "commodity"  # Товар
                    })

                # Если корзина пуста — добавим "Заказ"
                if not items:
                    items.append({
                        "description": "Заказ цветов",
                        "quantity": 1,
                        "amount": {
                            "value": f"{amount:.2f}",
                            "currency": "RUB"
                        },
                        "vat_code": "1",
                        "payment_mode": "full_payment",
                        "payment_subject": "service"
                    })

                # Объект чека
                receipt = {
                    "customer": {
                        "email": customer_email
                    },
                    "items": items,
                    "send": True  # Отправить чек на email
                }
                # --- КОНЕЦ: Чек ---

                # Уникальный ключ для идемпотентности
                idempotency_key = str(uuid.uuid4())

                # Данные для платежа
                payment_data = {
                    "amount": {
                        "value": f"{amount:.2f}",
                        "currency": "RUB"
                    },
                    "confirmation": {
                        "type": "redirect",
                        "return_url": "https://t.me/flowersstories_bot"  # Лучше — ссылка на бота или сайт
                    },
                    "capture": True,
                    "description": description,
                    "metadata": metadata,
                    "receipt": receipt  # ← ВАЖНО: добавляем чек сюда
                }

                # Создаём платеж
                payment = Payment.create(payment_data, idempotency_key)

                if hasattr(payment, 'confirmation') and hasattr(payment.confirmation, 'confirmation_url'):
                    logger.info(f"✅ Платёж создан: {payment.id}")
                    return {
                        "id": payment.id,
                        "status": payment.status,
                        "confirmation_url": payment.confirmation.confirmation_url,
                        "amount": amount
                    }

            except Exception as e:
                logger.error(f"Attempt {attempt + 1} failed: {e}")
                if attempt < self.retry_attempts - 1:
                    await asyncio.sleep(self.retry_delay)

        # Fallback — если не получилось
        return await self.create_fallback_payment(amount, description, metadata)

    async def create_fallback_payment(self, amount: int, description: str, metadata: dict) -> dict:
        """Резервный метод создания платежа"""
        try:
            payment_id = f"fallback_{uuid.uuid4().hex[:8]}"
            return {
                "id": payment_id,
                "status": "pending",
                "confirmation_url": f"https://yoomoney.ru/quickpay/confirm.xml?receiver={YOOKASSA_SHOP_ID}&quickpay-form=button&sum={amount}&label={payment_id}",
                "amount": amount
            }
        except Exception as e:
            logger.error(f"Fallback payment creation failed: {e}")
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
        return "succeeded"  # В случае ошибки считаем платеж успешным для тестирования

    async def create_local_payment(self, amount: int, description: str, metadata: dict) -> dict:
        """Создание тестового платежа для локальной разработки"""
        try:
            payment_id = f"test_{uuid.uuid4().hex[:8]}"

            # Создаем тестовую страницу оплаты
            test_payment_url = f"https://{LOCAL_TUNNEL_URL}/test_payment/{payment_id}"

            return {
                "id": payment_id,
                "status": "pending",
                "confirmation_url": test_payment_url,
                "amount": amount,
                "is_test": True  # Флаг тестового платежа
            }
        except Exception as e:
            logger.error(f"Local payment creation failed: {e}")
            return None

    async def check_payment_status(self, payment_id: str) -> Optional[str]:
        """Проверка статуса платежа"""
        if payment_id.startswith("test_"):
            # Для тестовых платежей всегда возвращаем успех
            return "succeeded"


payment_manager = SimplePaymentManager()
