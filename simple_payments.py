import logging
import asyncio
import uuid
from typing import Optional
from config import *
from yookassa import Configuration, Payment
import aiohttp

logger = logging.getLogger(__name__)

# Настройка YooKassa
Configuration.account_id = YOOKASSA_SHOP_ID
Configuration.secret_key = YOOKASSA_SECRET_KEY


class SimplePaymentManager:
    def __init__(self):
        self.retry_attempts = 3
        self.retry_delay = 2

    async def check_yookassa_availability(self) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                        'https://api.yookassa.ru/v3/',
                        timeout=10,
                        auth=aiohttp.BasicAuth(YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY)
                ) as response:
                    return response.status == 200
        except Exception as e:
            logger.error(f"YooKassa недоступен: {e}")
            return False

    async def create_payment(self, amount: int, description: str, metadata: dict, customer_email: str) -> dict:
        for attempt in range(self.retry_attempts):
            try:
                logger.info(f"🔄 Попытка {attempt + 1} создать платеж на {amount} руб.")

                # Подготовка чека
                receipt_items = []

                # Используем переданный email, а не перезаписываем
                if not customer_email or customer_email == "flowers@example.com":
                    customer_email = metadata.get('email', "flowers@example.com")

                cart_items = metadata.get("cart_items", [])
                delivery_cost = metadata.get("delivery_cost", 0)

                # Формируем позиции чека для товаров
                for item in cart_items:
                    receipt_items.append({
                        "description": item["name"][:128],
                        "quantity": str(item["quantity"]),  # Должно быть строкой
                        "amount": {"value": f"{float(item['price']):.2f}", "currency": "RUB"},
                        "vat_code": YOOKASSA_TAX_RATE,
                        "payment_mode": "full_payment",
                        "payment_subject": "commodity"
                    })

                # Добавляем доставку как отдельную позицию
                if delivery_cost > 0:
                    receipt_items.append({
                        "description": "Доставка",
                        "quantity": "1.00",  # Должно быть строкой с двумя знаками после запятой
                        "amount": {"value": f"{float(delivery_cost):.2f}", "currency": "RUB"},
                        "vat_code": YOOKASSA_TAX_RATE,
                        "payment_mode": "full_payment",
                        "payment_subject": "service"
                    })

                # Для сертификатов создаем отдельную позицию
                if metadata.get('type') == 'certificate':
                    receipt_items.append({
                        "description": f"Подарочный сертификат {metadata.get('cert_code', '')}"[:128],
                        "quantity": "1.00",
                        "amount": {"value": f"{float(amount):.2f}", "currency": "RUB"},
                        "vat_code": YOOKASSA_TAX_RATE,
                        "payment_mode": "full_payment",
                        "payment_subject": "service"
                    })

                # Если нет позиций для чека, не включаем receipt в запрос
                payment_data = {
                    "amount": {"value": f"{amount:.2f}", "currency": "RUB"},
                    "confirmation": {"type": "redirect", "return_url": "https://t.me/Therry_Voyager"},
                    "capture": True,
                    "description": description,
                    "metadata": metadata,
                }

                # Добавляем чек только если есть позиции
                if receipt_items:
                    payment_data["receipt"] = {
                        "customer": {"email": customer_email},
                        "items": receipt_items,
                        "tax_system_code": YOOKASSA_TAX_SYSTEM
                    }

                payment = Payment.create(payment_data)

                if payment.confirmation and payment.confirmation.confirmation_url:
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

        return await self.create_fallback_payment(amount, description, metadata)

    async def check_payment_status(self, payment_id: str) -> Optional[str]:
        for attempt in range(self.retry_attempts):
            try:
                payment = Payment.find_one(payment_id)
                return payment.status
            except Exception as e:
                logger.error(f"Check attempt {attempt + 1} failed: {e}")
                if attempt < self.retry_attempts - 1:
                    await asyncio.sleep(self.retry_delay)
        return None

    async def create_fallback_payment(self, amount: int, description: str, metadata: dict) -> dict:
        try:
            payment_id = f"fallback_{uuid.uuid4().hex[:8]}"
            return {
                "id": payment_id,
                "status": "pending",
                "confirmation_url": f"https://yoomoney.ru/quickpay/confirm.xml?receiver={YOOKASSA_SHOP_ID}&sum={amount}&label={payment_id}",
                "amount": amount
            }
        except Exception as e:
            logger.error(f"Fallback failed: {e}")
            return None


payment_manager = SimplePaymentManager()
