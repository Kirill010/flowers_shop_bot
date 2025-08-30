import uuid
import asyncio
from fpdf import FPDF
from config import YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY, YOOKASSA_TAX_RATE, YOOKASSA_TAX_SYSTEM
from yookassa import Configuration, Payment
from database import add_certificate_purchase, save_payment
import os

Configuration.account_id = YOOKASSA_SHOP_ID
Configuration.secret_key = YOOKASSA_SECRET_KEY


def generate_certificate_pdf(amount: int, cert_code: str, filename: str):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, "ПОДАРОЧНЫЙ СЕРТИФИКАТ", ln=True, align="C")
    pdf.ln(8)
    pdf.set_font("Arial", "", 12)
    pdf.cell(0, 8, f"Сумма: {amount} ₽", ln=True, align="C")
    pdf.cell(0, 8, f"Код сертификата: {cert_code}", ln=True, align="C")
    pdf.ln(6)
    pdf.multi_cell(0, 8, "Действует 1 год с момента покупки. Для активации сообщите код менеджеру.")
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    pdf.output(filename)
    return filename


async def create_certificate_payment(user_id: int, amount: int, return_url: str = "https://t.me/flowersstories_bot",
                                     email: str = None):
    cert_code = f"CERT-{uuid.uuid4().hex[:8].upper()}"
    amount_str = f"{float(amount):.2f}"
    payment_data = {
        "amount": {"value": amount_str, "currency": "RUB"},
        "confirmation": {"type": "redirect", "return_url": return_url},
        "capture": True,
        "description": f"Подарочный сертификат {cert_code}",
        "metadata": {"user_id": user_id, "cert_code": cert_code, "type": "certificate", "email": email}
    }

    # receipt
    payment_data["receipt"] = {
        "customer": {"email": email},
        "items": [{
            "description": f"Подарочный сертификат {cert_code}"[:128],
            "quantity": "1.00",
            "amount": {"value": amount_str, "currency": "RUB"},
            "vat_code": YOOKASSA_TAX_RATE,
            "payment_mode": "full_payment",
            "payment_subject": "service"
        }],
        "tax_system_code": YOOKASSA_TAX_SYSTEM
    }

    def create_call():
        return Payment.create(payment_data, idempotence_key=str(uuid.uuid4()))

    payment = await asyncio.to_thread(create_call)

    # store minimal data in DB (pending)
    save_payment(payment.id, user_id, float(amount_str), payment.status, payment.description,
                 payment.metadata if hasattr(payment, "metadata") else payment_data["metadata"])

    # generate pdf (non-blocking)
    filename = os.path.join("certificates", f"{cert_code}.pdf")
    await asyncio.to_thread(generate_certificate_pdf, amount, cert_code, filename)

    # save certificate record in DB (we will link after success; store placeholder now)
    add_certificate_purchase(user_id, amount, cert_code, payment.id)

    return {
        "payment_id": payment.id,
        "confirmation_url": payment.confirmation.confirmation_url if getattr(payment, "confirmation", None) else None,
        "cert_code": cert_code,
        "pdf_path": filename
    }
