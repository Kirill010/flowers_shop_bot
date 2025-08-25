from yookassa import Payment, Configuration
from config import YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY
import uuid
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from database import add_certificate_purchase
import os
from fpdf import FPDF


class CertificateState(StatesGroup):
    waiting_payment = State()


def generate_certificate(amount: str, cert_code: str, filename: str):
    """Генерация PDF сертификата (упрощенная версия)"""
    pdf = FPDF()
    pdf.add_page()

    pdf.set_font("Arial", "B", 16)
    pdf.cell(200, 10, txt="GIFT CERTIFICATE", ln=True, align="C")

    pdf.set_font("Arial", "B", 14)
    pdf.cell(200, 10, txt=f"Amount: {amount} RUB", ln=True, align="C")

    pdf.set_font("Arial", "", 12)
    pdf.cell(200, 10, txt=f"Code: {cert_code}", ln=True, align="C")

    pdf.multi_cell(0, 8, txt="Valid for 1 year from purchase date. "
                             "Can be used for any products in the store.")

    pdf.output(filename)
    return filename


async def create_certificate_payment(user_id: int, amount: int, callback: CallbackQuery, state: FSMContext):
    """Создание платежа для сертификата"""
    cert_code = f"CERT-{uuid.uuid4().hex[:8].upper()}"

    try:
        # Настройка ЮKassa
        Configuration.account_id = YOOKASSA_SHOP_ID
        Configuration.secret_key = YOOKASSA_SECRET_KEY

        # Создаем реальный платеж
        payment_id = str(uuid.uuid4())
        payment = Payment.create({
            "amount": {"value": str(amount), "currency": "RUB"},
            "confirmation": {
                "type": "redirect",
                "return_url": "https://t.me/mgk71_bot"  # URL вашего бота
            },
            "capture": True,
            "description": f"Подарочный сертификат на {amount}₽",
            "metadata": {
                "user_id": user_id,
                "cert_code": cert_code,
                "type": "certificate"
            }
        }, idempotency_key=payment_id)

        await state.update_data(
            payment_id=payment.id,
            cert_amount=amount,
            cert_code=cert_code,
            payment_url=payment.confirmation.confirmation_url
        )
        await state.set_state(CertificateState.waiting_payment)

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить сертификат", url=payment.confirmation.confirmation_url)],
            [InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"check_cert_payment_{payment.id}")]
        ])

        await callback.message.answer(
            f"🎁 <b>Сертификат на {amount} ₽</b>\n\n"
            f"💳 Сумма к оплате: {amount} ₽\n"
            f"🔗 Перейдите по ссылке для оплаты\n\n"
            f"После оплаты нажмите «✅ Проверить оплату»",
            reply_markup=kb,
            parse_mode="HTML"
        )

    except Exception as e:
        print(f"Payment creation error: {e}")
        await callback.message.answer(
            f"🎁 <b>Сертификат на {amount} ₽</b>\n\n"
            "⚠️ Платежная система временно недоступна.\n"
            "📞 Для покупки сертификата свяжитесь с менеджером: @mgk71\n\n"
            f"Код сертификата: <code>{cert_code}</code>\n"
            "Сообщите этот код менеджеру для активации.",
            parse_mode="HTML"
        )
