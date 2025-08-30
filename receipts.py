from yookassa import Receipt
from config import YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY, YOOKASSA_TAX_RATE, YOOKASSA_TAX_SYSTEM
from database import get_payment
import logging

logger = logging.getLogger(__name__)


class ReceiptManager:
    def __init__(self):
        self.customer_email = "client@example.com"  # Будет запрашиваться у пользователя

    async def create_receipt(self, payment_id: str, user_email: str = None):
        """Создание чека для платежа"""
        try:
            # Получаем информацию о платеже
            payment_info = get_payment(payment_id)
            if not payment_info:
                logger.error(f"Payment {payment_id} not found")
                return False

            # Парсим metadata
            metadata = payment_info.get('metadata', {})
            if isinstance(metadata, str):
                import json
                metadata = json.loads(metadata)

            # Формируем позиции чека
            items = self._prepare_receipt_items(metadata)

            # Email клиента
            customer_email = user_email or metadata.get('email', self.customer_email)

            receipt_data = {
                "type": "payment",  # Чек прихода
                "payment_id": payment_id,
                "customer": {
                    "email": customer_email
                },
                "items": items,
                "tax_system_code": YOOKASSA_TAX_SYSTEM,
                "send": True
            }

            # Создаем чек
            receipt = Receipt.create(receipt_data)
            logger.info(f"Receipt created for payment {payment_id}: {receipt.id}")
            return True

        except Exception as e:
            logger.error(f"Error creating receipt: {e}")
            return False

    def _prepare_receipt_items(self, metadata):
        """Подготовка позиций для чека"""
        items = []

        # Для заказов
        if metadata.get('type') == 'order':
            cart_items = metadata.get('cart_items', [])
            for item in cart_items:
                items.append({
                    "description": item['name'][:128],  # Ограничение длины
                    "quantity": str(item['quantity']),
                    "amount": {
                        "value": str(item['price']),
                        "currency": "RUB"
                    },
                    "vat_code": YOOKASSA_TAX_RATE,
                    "payment_mode": "full_payment",
                    "payment_subject": "commodity"
                })

            # Добавляем доставку как отдельную позицию
            delivery_cost = metadata.get('delivery_cost', 0)
            if delivery_cost > 0:
                items.append({
                    "description": "Доставка",
                    "quantity": "1",
                    "amount": {
                        "value": str(delivery_cost),
                        "currency": "RUB"
                    },
                    "vat_code": YOOKASSA_TAX_RATE,
                    "payment_mode": "full_payment",
                    "payment_subject": "service"
                })

        # Для сертификатов
        elif metadata.get('type') == 'certificate':
            items.append({
                "description": f"Подарочный сертификат {metadata.get('cert_code', '')}",
                "quantity": "1",
                "amount": {
                    "value": str(metadata.get('amount', 0)),
                    "currency": "RUB"
                },
                "vat_code": YOOKASSA_TAX_RATE,
                "payment_mode": "full_payment",
                "payment_subject": "service"
            })

        return items

    async def create_refund_receipt(self, payment_id: str, amount: float, user_email: str = None):
        """Создание чека возврата"""
        try:
            payment_info = get_payment(payment_id)
            if not payment_info:
                return False

            metadata = payment_info.get('metadata', {})
            if isinstance(metadata, str):
                import json
                metadata = json.loads(metadata)

            items = self._prepare_receipt_items(metadata)

            # Для возврата корректируем суммы
            for item in items:
                item_amount = float(item['amount']['value'])
                refund_amount = min(amount, item_amount)
                item['amount']['value'] = str(refund_amount)
                amount -= refund_amount
                if amount <= 0:
                    break

            receipt_data = {
                "type": "refund",  # Чек возврата
                "payment_id": payment_id,
                "customer": {
                    "email": user_email or metadata.get('email', self.customer_email)
                },
                "items": items,
                "tax_system_code": YOOKASSA_TAX_SYSTEM,
                "send": True
            }

            receipt = Receipt.create(receipt_data)
            logger.info(f"Refund receipt created: {receipt.id}")
            return True

        except Exception as e:
            logger.error(f"Error creating refund receipt: {e}")
            return False


receipt_manager = ReceiptManager()
