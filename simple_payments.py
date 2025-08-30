import asyncio
import uuid
import logging
import json
from typing import Optional, Dict
from yookassa import Configuration, Payment
from config import YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY, YOOKASSA_TAX_RATE, YOOKASSA_TAX_SYSTEM
from database import save_payment, update_payment_status, get_payment

logger = logging.getLogger(__name__)

# configure yookassa
Configuration.account_id = YOOKASSA_SHOP_ID
Configuration.secret_key = YOOKASSA_SECRET_KEY


class PaymentManager:
    def __init__(self):
        self.retry_attempts = 3
        self.retry_delay = 2

    async def create_payment(self, user_id: int, amount: float, description: str,
                             metadata: dict = None, customer_email: str = None) -> Dict:
        """
        Создает платёж в YooKassa. Возвращает dict с id, status, confirmation_url, amount
        """
        metadata = metadata or {}
        amount_str = f"{float(amount):.2f}"

        # Подготовка данных для чека
        receipt_items = self._prepare_receipt_items(metadata, amount_str)

        # Генерируем idempotence key
        idempotence_key = str(uuid.uuid4())

        payment_data = {
            "amount": {"value": amount_str, "currency": "RUB"},
            "confirmation": {
                "type": "redirect",
                "return_url": metadata.get("return_url", "https://t.me/flowersstories_bot")
            },
            "capture": True,  # Автоматическое подтверждение платежа
            "description": description,
            "metadata": metadata
        }

        # Добавляем чек если есть email и товары
        if receipt_items and (customer_email or metadata.get("email")):
            payment_data["receipt"] = {
                "customer": {"email": customer_email or metadata.get("email")},
                "items": receipt_items,
                "tax_system_code": YOOKASSA_TAX_SYSTEM
            }

        for attempt in range(self.retry_attempts):
            try:
                # Создаем платеж с idempotence key
                payment = await self._create_payment_thread(payment_data, idempotence_key)

                # Сохраняем в базу данных
                save_payment(
                    payment.id, user_id, float(amount_str),
                    payment.status, description, metadata
                )

                return {
                    "id": payment.id,
                    "status": payment.status,
                    "confirmation_url": getattr(getattr(payment, 'confirmation', None), 'confirmation_url', None),
                    "amount": float(amount_str)
                }

            except Exception as e:
                logger.error(f"Payment create attempt {attempt + 1} failed: {e}")
                if attempt < self.retry_attempts - 1:
                    await asyncio.sleep(self.retry_delay)

        raise RuntimeError("Не удалось создать платёж в YooKassa")

    def _prepare_receipt_items(self, metadata: dict, amount_str: str) -> list:
        """Подготавливает items для чека"""
        receipt_items = []

        # Для заказов с товарами
        if metadata.get("type") == "order":
            cart_items = metadata.get("cart_items", [])
            if isinstance(cart_items, str):
                try:
                    cart_items = json.loads(cart_items)
                except:
                    cart_items = []

            for item in cart_items:
                receipt_items.append({
                    "description": str(item.get("name", ""))[:128],
                    "quantity": f"{float(item.get('quantity', 1)):.2f}",
                    "amount": {"value": f"{float(item.get('price', 0)):.2f}", "currency": "RUB"},
                    "vat_code": YOOKASSA_TAX_RATE,
                    "payment_mode": "full_payment",
                    "payment_subject": "commodity"
                })

            # Добавляем доставку если есть
            delivery_cost = float(metadata.get("delivery_cost", 0))
            if delivery_cost > 0:
                receipt_items.append({
                    "description": "Доставка",
                    "quantity": "1.00",
                    "amount": {"value": f"{delivery_cost:.2f}", "currency": "RUB"},
                    "vat_code": YOOKASSA_TAX_RATE,
                    "payment_mode": "full_payment",
                    "payment_subject": "service"
                })

        # Для сертификатов
        elif metadata.get("type") == "certificate":
            receipt_items.append({
                "description": f"Подарочный сертификат {metadata.get('cert_code', '')}"[:128],
                "quantity": "1.00",
                "amount": {"value": amount_str, "currency": "RUB"},
                "vat_code": YOOKASSA_TAX_RATE,
                "payment_mode": "full_payment",
                "payment_subject": "service"
            })

        return receipt_items

    async def _create_payment_thread(self, payment_data: dict, idempotence_key: str):
        """Создает платеж в отдельном потоке с idempotence key"""

        def create_call():
            # Правильный способ передачи idempotence key
            # В библиотеке yookassa idempotence_key передается как отдельный параметр
            try:
                # Попробуем сначала новый способ
                return Payment.create(payment_data, idempotence_key)
            except TypeError as e:
                # Если не поддерживается, попробуем старый способ
                logger.warning(f"New method failed, trying old method: {e}")
                try:
                    # Для старых версий библиотеки
                    import requests
                    from requests.auth import HTTPBasicAuth

                    headers = {
                        'Idempotence-Key': idempotence_key,
                        'Content-Type': 'application/json'
                    }

                    auth = HTTPBasicAuth(YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY)

                    response = requests.post(
                        'https://api.yookassa.ru/v3/payments',
                        auth=auth,
                        headers=headers,
                        json=payment_data,
                        timeout=30
                    )

                    if response.status_code == 200:
                        return response.json()
                    else:
                        raise Exception(f"YooKassa API error: {response.status_code} - {response.text}")

                except Exception as inner_e:
                    logger.error(f"Direct API call failed: {inner_e}")
                    raise

        return await asyncio.to_thread(create_call)

    async def check_payment_status(self, payment_id: str) -> Optional[str]:
        """
        Проверяет статус платежа (только для ручной проверки)
        """
        for attempt in range(self.retry_attempts):
            try:
                def find_call():
                    return Payment.find_one(payment_id)

                payment = await asyncio.to_thread(find_call)
                if payment:
                    update_payment_status(payment.id, payment.status)
                    return payment.status
                break

            except Exception as e:
                logger.error(f"Payment check attempt {attempt + 1} failed: {e}")
                if attempt < self.retry_attempts - 1:
                    await asyncio.sleep(self.retry_delay)
        return None


# Глобальный экземпляр менеджера платежей
payment_manager = PaymentManager()
