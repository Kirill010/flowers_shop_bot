from yookassa import Payment
import uuid
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from database import *
import os
from fpdf import FPDF
import asyncio
import logging
from config import YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY
from simple_payments import payment_manager
import json


# Настройка логирования
logger = logging.getLogger(__name__)


class CertificateState(StatesGroup):
    waiting_payment = State()


def generate_certificate(amount: str, cert_code: str, filename: str):
    """Создает PDF сертификат"""
    pdf = FPDF()
    pdf.add_page()

    pdf.set_font("Arial", "B", 16)
    pdf.cell(200, 10, txt="ПОДАРОЧНЫЙ СЕРТИФИКАТ", ln=True, align="C")

    pdf.set_font("Arial", "B", 14)
    pdf.cell(200, 10, txt=f"Сумма: {amount} RUB", ln=True, align="C")

    pdf.set_font("Arial", "", 12)
    pdf.cell(200, 10, txt=f"Код: {cert_code}", ln=True, align="C")

    pdf.multi_cell(0, 8, txt="Действует в течение 1 года с даты покупки. Может быть использован для любых товаров в магазине.")

    pdf.output(filename)
    return filename


async def create_certificate_payment(user_id: int, amount: int, callback: CallbackQuery, state: FSMContext):
    cert_code = f"CERT-{uuid.uuid4().hex[:8].upper()}"
    description = f"Покупка сертификата на {amount} ₽"

    try:
        # Попытка создать платеж
        payment = await payment_manager.create_payment(
            amount=amount,
            description=description,
            metadata={
                "user_id": user_id,
                "cert_code": cert_code,
                "type": "certificate"
            }
        )

        if not payment or not payment.get("confirmation_url"):
            logger.error(f"❌ Платеж не создан: {payment}")
            await callback.message.answer(
                f"🎁 <b>Сертификат на {amount} ₽</b>\n"
                "⚠️ Платежная система временно недоступна.\n"
                "📞 Свяжитесь с менеджером: @Therry_Voyager\n"
                f"Код сертификата: <code>{cert_code}</code>\n"
                "Сообщите этот код для активации.",
                parse_mode="HTML"
            )
            return

        # Успешно создан
        confirmation_url = payment["confirmation_url"]
        payment_id = payment["id"]

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить", url=confirmation_url)],
            [InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"check_cert_payment_{payment_id}")]
        ])

        await callback.message.answer(
            f"🎁 <b>Сертификат на {amount} ₽</b>\n"
            f"💳 Сумма к оплате: {amount} ₽\n"
            f"🔗 Перейдите по ссылке для оплаты\n"
            f"После оплаты нажмите «✅ Проверить оплату»",
            reply_markup=kb,
            parse_mode="HTML"
        )

        # Сохраняем в БД
        save_payment(
            payment_id=payment_id,
            user_id=user_id,
            amount=amount,
            status="pending",
            metadata=json.dumps({"cert_code": cert_code, "type": "certificate"})
        )

    except Exception as e:
        logger.error(f"❌ Ошибка создания платежа для сертификата: {e}", exc_info=True)
        await callback.message.answer(
            f"🎁 <b>Сертификат на {amount} ₽</b>\n"
            "⚠️ Платежная система недоступна.\n"
            "📞 Свяжитесь с менеджером: @Therry_Voyager\n"
            f"Код сертификата: <code>{cert_code}</code>\n"
            "Сообщите этот код для активации.",
            parse_mode="HTML"
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
