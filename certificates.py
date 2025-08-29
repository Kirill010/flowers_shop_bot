from yookassa import Payment
import uuid
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from database import add_certificate_purchase
import os
from fpdf import FPDF
import asyncio
import logging
from config import YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY
from simple_payments import payment_manager

# Настройка логирования
logger = logging.getLogger(__name__)


class CertificateState(StatesGroup):
    waiting_payment = State()


def generate_certificate(amount: str, cert_code: str, filename: str):
    """Создает PDF сертификат"""
    pdf = FPDF()
    pdf.add_page()

    pdf.set_font("Arial", "B", 16)
    pdf.cell(200, 10, txt="GIFT CERTIFICATE", ln=True, align="C")

    pdf.set_font("Arial", "B", 14)
    pdf.cell(200, 10, txt=f"Amount: {amount} RUB", ln=True, align="C")

    pdf.set_font("Arial", "", 12)
    pdf.cell(200, 10, txt=f"Code: {cert_code}", ln=True, align="C")

    pdf.multi_cell(0, 8, txt="Valid for 1 year from purchase date. Can be used for any products in the store.")

    pdf.output(filename)
    return filename


async def create_certificate_payment(user_id: int, amount: int, callback: CallbackQuery, state: FSMContext):
    """Создает платеж для сертификата"""
    cert_code = f"CERT-{uuid.uuid4().hex[:8].upper()}"

    try:
        logger.info(f"🔄 Создание платежа для сертификата на {amount} руб.")

        # Упрощенные метаданные
        simplified_metadata = {
            "user_id": user_id,
            "cert_code": cert_code,
            "phone": "9999999999",
            "type": "certificate"
        }

        # СОЗДАЕМ ПЛАТЕЖ через наш менеджер
        payment = await payment_manager.create_payment(
            amount=amount,
            description=f"Подарочный сертификат на {amount}₽",
            metadata=simplified_metadata
        )

        if payment and payment.get("confirmation_url"):
            await state.update_data(
                payment_id=payment["id"],
                cert_amount=amount,
                cert_code=cert_code,
                payment_url=payment["confirmation_url"]
            )
            await state.set_state(CertificateState.waiting_payment)

            # Создаем кнопки для пользователя
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 Оплатить сертификат", url=payment["confirmation_url"])],
                [InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"check_cert_payment_{payment['id']}")]
            ])

            await callback.message.answer(
                f"🎁 <b>Сертификат на {amount} ₽</b>\n\n"
                f"💳 Сумма к оплате: {amount} ₽\n"
                f"🔗 Перейдите по ссылке для оплаты\n\n"
                f"После оплаты нажмите «✅ Проверить оплату»",
                reply_markup=kb,
                parse_mode="HTML"
            )
        else:
            # Если не удалось создать платеж
            await callback.message.answer(
                f"🎁 <b>Сертификат на {amount} ₽</b>\n\n"
                "⚠️ Платежная система временно недоступна.\n"
                "📞 Для покупки сертификата свяжитесь с менеджером: @Therry_Voyager\n\n"
                f"Код сертификата: <code>{cert_code}</code>\n"
                "Сообщите этот код менеджеру для активации.",
                parse_mode="HTML"
            )

    except Exception as e:
        logger.error(f"❌ Ошибка создания платежа для сертификата: {e}")
        await callback.message.answer(
            f"❌ Произошла ошибка при создании платежа. Попробуйте позже или свяжитесь с менеджером."
        )


async def handle_certificate_selection(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора сертификата"""
    amount_str = callback.data.split("_")[1]
    try:
        amount = int(amount_str)
        await create_certificate_payment(callback.from_user.id, amount, callback, state)
    except ValueError:
        await callback.answer("❌ Неверный номинал сертификата")
    await callback.answer()


async def check_certificate_payment(payment_id: str, callback: CallbackQuery, state: FSMContext):
    """Проверяет статус платежа сертификата"""
    try:
        status = await payment_manager.check_payment_status(payment_id)

        if status == "succeeded":
            data = await state.get_data()
            amount = data.get("cert_amount")
            cert_code = data.get("cert_code")

            # Генерируем PDF сертификат
            os.makedirs("certificates", exist_ok=True)
            pdf_path = f"certificates/cert_{callback.from_user.id}_{amount}.pdf"
            generate_certificate(str(amount), cert_code, pdf_path)

            # Отправляем PDF
            pdf = FSInputFile(pdf_path)
            await callback.message.answer_document(
                document=pdf,
                caption=f"🎉 Поздравляем! Вы купили сертификат на {amount} ₽\nКод: `{cert_code}`",
                parse_mode="Markdown"
            )

            # Сохраняем в БД
            add_certificate_purchase(
                user_id=callback.from_user.id,
                amount=amount,
                cert_code=cert_code,
                payment_id=payment_id
            )

            # Удаляем временный файл
            if os.path.exists(pdf_path):
                os.remove(pdf_path)

            await state.clear()
            await callback.answer("✅ Сертификат успешно создан и отправлен!")

        elif status == "pending":
            await callback.answer("⏳ Платеж еще обрабатывается. Попробуйте через минуту.")
        else:
            await callback.answer("❌ Платёж не прошёл или отменен")

    except Exception as e:
        logger.error(f"❌ Ошибка проверки платежа сертификата: {e}")
        await callback.answer("❌ Ошибка при проверке платежа")
