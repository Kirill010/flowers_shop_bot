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
    """Генерация PDF сертификата с поддержкой кириллицы"""
    pdf = FPDF()
    pdf.add_page()

    # 🔽 ВАЖНО: Подключаем шрифт ДО того, как использовать set_font()
    pdf.add_font("DejaVu", "", "DejaVuSans.ttf", uni=True)
    pdf.add_font("DejaVu", "B", "DejaVuSans-Bold.ttf", uni=True)

    # Теперь используем "DejaVu", а не "DejaVuSans"
    pdf.set_font("DejaVu", "B", 16)
    pdf.cell(0, 10, "ПОДАРОЧНЫЙ СЕРТИФИКАТ", ln=True, align="C")
    pdf.ln(5)

    pdf.set_font("DejaVu", "", 12)
    pdf.cell(0, 8, f"Сумма: {amount} RUB", ln=True, align="C")
    pdf.cell(0, 8, f"Код: {cert_code}", ln=True, align="C")
    pdf.ln(5)

    pdf.multi_cell(0, 6, "Действует 1 год с момента покупки. Может быть использован для любых товаров в магазине.")

    # Создаём папку
    os.makedirs(os.path.dirname(filename), exist_ok=True)

    # Сохраняем PDF
    pdf.output(filename)
    return filename
