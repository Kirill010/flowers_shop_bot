from yookassa import Payment, Configuration
from config import YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY
import uuid
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from database import add_certificate_purchase
import os
from fpdf import FPDF


# --- ВНЕШНЯЯ ФУНКЦИЯ: generate_certificate ---
def generate_certificate(amount: str, cert_code: str, filename: str):
    """Генерация PDF сертификата (упрощенная версия)"""
    pdf = FPDF()
    pdf.add_page()

    # Устанавливаем шрифт
    pdf.add_font("DejaVu", "", "DejaVuSans.ttf", uni=True)
    pdf.add_font("DejaVu", "B", "DejaVuSans-Bold.ttf", uni=True)
    pdf.set_font("DejaVu", "B", 16)

    # Заголовок
    pdf.cell(0, 10, "ПОДАРОЧНЫЙ СЕРТИФИКАТ", ln=True, align="C")
    pdf.ln(5)

    # Сумма
    pdf.set_font("DejaVu", "", 12)
    pdf.cell(0, 8, f"Сумма: {amount} ₽", ln=True, align="C")

    # Код
    pdf.cell(0, 8, f"Код: {cert_code}", ln=True, align="C")
    pdf.ln(5)

    # Текст
    pdf.multi_cell(0, 6, "Действует 1 год с момента покупки. Может быть использован для любых товаров в магазине.")

    # Сохраняем
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    pdf.output(filename)
    return filename


# --- FSM СОСТОЯНИЯ ---
class CertificateState(StatesGroup):
    waiting_payment = State()


# --- ОСНОВНАЯ ФУНКЦИЯ: create_certificate_payment ---
async def create_certificate_payment(user_id: int, amount: int, callback: CallbackQuery, state: FSMContext):
    """Создание платежа для сертификата"""
    cert_code = f"CERT-{uuid.uuid4().hex[:8].upper()}"

    try:
        # Настройка ЮKassa
        Configuration.account_id = YOOKASSA_SHOP_ID
        Configuration.secret_key = YOOKASSA_SECRET_KEY

        # Данные платежа
        payment_data = {
            "amount": {"value": f"{amount:.2f}", "currency": "RUB"},
            "confirmation": {
                "type": "redirect",
                "return_url": "https://t.me/Therry_Voyager"  # ✅ Без пробелов
            },
            "capture": True,
            "description": f"Подарочный сертификат на {amount}₽",
            "metadata": {
                "user_id": user_id,
                "cert_code": cert_code,
                "type": "certificate",
                "email": "flowers@example.com"
            },
            "receipt": {
                "customer": {
                    "email": "flowers@example.com",
                    "full_name": "Клиент"
                },
                "items": [
                    {
                        "description": f"Подарочный сертификат {cert_code}",
                        "quantity": "1.00",
                        "amount": {"value": f"{amount:.2f}", "currency": "RUB"},
                        "vat_code": "1",
                        "payment_mode": "full_payment",
                        "payment_subject": "service"
                    }
                ],
                "tax_system_code": 1
            }
        }

        # Создаём платёж
        payment = Payment.create(payment_data, idempotency_key=str(uuid.uuid4()))

        if not payment.confirmation or not payment.confirmation.confirmation_url:
            raise Exception("Нет ссылки для оплаты")

        # Сохраняем данные в FSM
        await state.update_data(
            payment_id=payment.id,
            cert_amount=amount,
            cert_code=cert_code,
            payment_url=payment.confirmation.confirmation_url
        )
        await state.set_state(CertificateState.waiting_payment)

        # Кнопки
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить", url=payment.confirmation.confirmation_url)],
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
            "📞 Для покупки свяжитесь с менеджером: @Therry_Voyager\n\n"
            f"Код сертификата: <code>{cert_code}</code>\n"
            "Сообщите этот код для активации.",
            parse_mode="HTML"
        )
