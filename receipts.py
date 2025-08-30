import asyncio
import logging
from yookassa import Receipt
from config import YOOKASSA_TAX_RATE, YOOKASSA_TAX_SYSTEM
from database import get_payment
import json

logger = logging.getLogger(__name__)

class ReceiptManager:
    def __init__(self):
        self.default_email = "client@example.com"

    async def create_receipt(self, payment_id: str, user_email: str = None) -> bool:
        try:
            payment_info = get_payment(payment_id)
            if not payment_info:
                logger.error("Payment not found for receipt")
                return False

            metadata = payment_info.get("metadata")
            if isinstance(metadata, str):
                metadata = json.loads(metadata) if metadata else {}

            # Prepare items
            items = []
            if metadata.get("type") == "order":
                cart_items = metadata.get("cart_items", [])
                for i in cart_items:
                    items.append({
                        "description": i.get("name", "")[:128],
                        "quantity": f"{int(i.get('quantity', 1)):.2f}",
                        "amount": {"value": f"{float(i.get('price', 0)):.2f}", "currency": "RUB"},
                        "vat_code": YOOKASSA_TAX_RATE,
                        "payment_mode": "full_payment",
                        "payment_subject": "commodity"
                    })
                delivery_cost = float(metadata.get("delivery_cost", 0))
                if delivery_cost > 0:
                    items.append({
                        "description": "Доставка",
                        "quantity": "1.00",
                        "amount": {"value": f"{delivery_cost:.2f}", "currency": "RUB"},
                        "vat_code": YOOKASSA_TAX_RATE,
                        "payment_mode": "full_payment",
                        "payment_subject": "service"
                    })
            elif metadata.get("type") == "certificate":
                items.append({
                    "description": f"Подарочный сертификат {metadata.get('cert_code','')}"[:128],
                    "quantity": "1.00",
                    "amount": {"value": f"{float(payment_info['amount']):.2f}", "currency": "RUB"},
                    "vat_code": YOOKASSA_TAX_RATE,
                    "payment_mode": "full_payment",
                    "payment_subject": "service"
                })

            receipt_data = {
                "type": "payment",
                "payment_id": payment_id,
                "customer": {"email": user_email or metadata.get("email") or self.default_email},
                "items": items,
                "tax_system_code": YOOKASSA_TAX_SYSTEM,
                "send": True
            }

            # blocking -> thread
            def create_call():
                return Receipt.create(receipt_data)
            receipt = await asyncio.to_thread(create_call)
            logger.info(f"Receipt created {receipt.id} for payment {payment_id}")
            return True
        except Exception as e:
            logger.exception(f"Error creating receipt: {e}")
            return False

receipt_manager = ReceiptManager()
