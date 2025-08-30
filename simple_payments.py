# simple_payments.py
import asyncio
import uuid
import logging
from typing import Optional, Dict
from yookassa import Configuration, Payment
from config import YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY, YOOKASSA_TAX_RATE, YOOKASSA_TAX_SYSTEM
from database import save_payment, update_payment_status, get_payment
import ssl
import requests
from requests.auth import HTTPBasicAuth
import json

logger = logging.getLogger(__name__)

# configure yookassa (synchronous library) — safe to set once
Configuration.account_id = YOOKASSA_SHOP_ID
Configuration.secret_key = YOOKASSA_SECRET_KEY


def check_yookassa_sync():
    """Синхронная проверка доступности YooKassa"""
    try:
        response = requests.get(
            'https://api.yookassa.ru/v3/payments',
            auth=HTTPBasicAuth(YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY),
            timeout=10,
            params={'limit': 1}
        )
        return response.status_code == 200
    except Exception as e:
        print(f"Синхронная проверка failed: {e}")
        return False

# Добавьте этот метод в класс
async def check_yookassa_sync_wrapper(self):
    """Обертка для синхронной проверки"""
    return await asyncio.to_thread(self.check_yookassa_sync)

class SimplePaymentManager:
    def __init__(self):
        self.retry_attempts = 3
        self.retry_delay = 2

    async def check_yookassa_availability(self) -> bool:
        import aiohttp
        try:
            # Создаем кастомный SSL контекст
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            auth = aiohttp.BasicAuth(YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY)

            async with aiohttp.ClientSession(auth=auth) as session:
                async with session.get(
                        'https://api.yookassa.ru/v3/payments',
                        ssl=ssl_context,
                        timeout=30,
                        params={'limit': 1}
                ) as response:
                    # YooKassa возвращает 200 даже если нет платежей
                    return response.status in [200, 201, 204]

        except aiohttp.ClientError as e:
            logger.error(f"YooKassa network error: {e}")
            return False
        except asyncio.TimeoutError:
            logger.error("YooKassa timeout - сервер не отвечает")
            return False
        except Exception as e:
            logger.error(f"YooKassa availability check failed: {e}")
            return False

    async def create_payment(self, user_id: int, amount: float, description: str, metadata: dict = None,
                             customer_email: str = None) -> Dict:
        """
        Создает платёж в YooKassa. Возвращает dict с id, status, confirmation_url, amount
        """
        metadata = metadata or {}
        # format amount to 2 decimals string for API
        amount_str = f"{float(amount):.2f}"

        # prepare receipt items if present in metadata
        receipt_items = []
        cart_items = metadata.get("cart_items", [])

        # Убедитесь, что cart_items является списком
        if isinstance(cart_items, str):
            try:
                cart_items = json.loads(cart_items)
            except:
                cart_items = []

        for item in cart_items:
            receipt_items.append({
                "description": item.get("name", "")[:128],
                "quantity": f"{float(item.get('quantity', 1)):.2f}",  # Используем float вместо int
                "amount": {"value": f"{float(item.get('price', 0)):.2f}", "currency": "RUB"},
                "vat_code": YOOKASSA_TAX_RATE,
                "payment_mode": "full_payment",
                "payment_subject": "commodity"
            })

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

        # for certificate: single service item
        if metadata.get("type") == "certificate":
            receipt_items = [{
                "description": f"Подарочный сертификат {metadata.get('cert_code', '')}"[:128],
                "quantity": "1.00",
                "amount": {"value": amount_str, "currency": "RUB"},
                "vat_code": YOOKASSA_TAX_RATE,
                "payment_mode": "full_payment",
                "payment_subject": "service"
            }]

        payment_data = {
            "amount": {"value": amount_str, "currency": "RUB"},
            "confirmation": {"type": "redirect",
                             "return_url": metadata.get("return_url", "https://t.me/flowersstories_bot")},
            "capture": True,
            "description": description,
            "metadata": metadata
        }

        if receipt_items and (customer_email or metadata.get("email")):
            payment_data["receipt"] = {
                "customer": {"email": customer_email or metadata.get("email")},
                "items": receipt_items,
                "tax_system_code": YOOKASSA_TAX_SYSTEM
            }

        for attempt in range(self.retry_attempts):
            try:
                # yookassa is blocking -> run in thread
                def create_call():
                    return Payment.create(payment_data, idempotence_key=str(uuid.uuid4()))

                payment = await asyncio.to_thread(create_call)

                # save to DB as pending
                save_payment(payment.id, user_id, float(amount_str), payment.status, description, metadata)

                if hasattr(payment, "confirmation") and hasattr(payment.confirmation, "confirmation_url"):
                    return {
                        "id": payment.id,
                        "status": payment.status,
                        "confirmation_url": payment.confirmation.confirmation_url,
                        "amount": float(amount_str)
                    }
                else:
                    # If successful but no confirmation url - return minimal
                    return {
                        "id": payment.id,
                        "status": payment.status,
                        "confirmation_url": None,
                        "amount": float(amount_str)
                    }

            except Exception as e:
                logger.error(f"Payment create attempt {attempt + 1} failed: {e}")
                if attempt < self.retry_attempts - 1:
                    await asyncio.sleep(self.retry_delay)

        raise RuntimeError("Не удалось создать платёж в YooKassa")

    async def check_payment_status(self, payment_id: str) -> Optional[str]:
        """
        Возвращает статус платежа ('pending', 'succeeded', 'canceled', etc.)
        """
        for attempt in range(self.retry_attempts):
            try:
                def find_call():
                    return Payment.find_one(payment_id)

                payment = await asyncio.to_thread(find_call)
                if payment:
                    # update DB record
                    update_payment_status(payment.id, payment.status)
                    return payment.status
                break
            except Exception as e:
                logger.error(f"Payment check attempt {attempt + 1} failed: {e}")
                if attempt < self.retry_attempts - 1:
                    await asyncio.sleep(self.retry_delay)
        return None


payment_manager = SimplePaymentManager()
