from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from typing import Union, Optional, Dict, List
import sqlite3
from keyboards import *
from database import *
from certificates import *
from config import *
from yookassa import Configuration
import os
import json
import uuid
import sqlite3
from aiogram.filters.state import StateFilter
from certificates import CertificateState, generate_certificate
from simple_payments import payment_manager
from database import save_payment, update_payment_status, get_payment
import asyncio
import logging
import random
from datetime import datetime, timedelta

MAX_BONUS_PERCENTAGE = 0.3  # 30%
BONUS_EARN_PERCENTAGE = 0.05  # 10%
FIRST_ORDER_DISCOUNT = 0.1  # 10% скидка на первый заказ

# Настройка логирования
logging.basicConfig(level=logging.DEBUG)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)

# Список всех текстовых команд из меню
MENU_COMMANDS = {
    "🌸 Каталог", "🚚 Доставка", "📞 Менеджер", "📍 На карте",
    "🎁 Сертификат", "⭐ Отзывы", "🛒 Корзина", "🧾 Мои заказы",
    "⬅️ Назад в меню", "🏠 В меню", "💎 Мои баллы"
}

router = Router()


# --- FSM для оформления заказа ---
class OrderState(StatesGroup):
    name = State()
    phone = State()
    delivery_type = State()
    address = State()
    delivery_date = State()
    delivery_time = State()
    payment = State()
    waiting_payment = State()
    certificate_code = State()
    use_bonus = State()  # Новый state для использования бонусов
    bonus_amount = State()


# --- FSM для связи с менеджером ---
class ManagerRequestState(StatesGroup):
    contact_and_question = State()


# --- FSM для администратора (добавление товаров) ---
class AdminState(StatesGroup):
    name = State()
    description = State()
    full_description = State()
    price = State()
    category = State()
    photo = State()
    budget = State()


class BudgetRequestState(StatesGroup):
    budget = State()
    phone = State()
    preferences = State()


class AdminEditPrice(StatesGroup):
    waiting_for_price = State()


try:
    from yookassa import Payment
except ImportError:
    Payment = None
    print("⚠️ YooKassa не установлен. Установите: pip install yookassa")


# --- Вспомогательные функции ---
def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь администратором"""
    return user_id in ADMINS


async def notify_admins(message_text: str, parse_mode: str = "HTML"):
    """Отправляет уведомление всем администраторам"""
    for admin_id in ADMINS:
        try:
            await bot.send_message(admin_id, message_text, parse_mode=parse_mode)
        except Exception as e:
            print(f"Не удалось отправить сообщение админу {admin_id}: {e}")


def get_payment_method_name(method_code):
    """Возвращает читаемое название способа оплаты"""
    methods = {
        "online": "💳 Онлайн картой",
        "cash": "💵 Наличными при получении",
        "sbp": "🔄 СБП",
        "cert": "🎁 Сертификат",
        "manager": "💬 Через менеджера"
    }
    return methods.get(method_code, "Неизвестно")


async def debug_cart(user_id: int):
    """Отладка корзины — выводит содержимое в консоль"""
    logger.debug(f"=== DEBUG CART FOR USER {user_id} ===")
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT name, quantity, price FROM cart c JOIN products p ON c.product_id = p.id WHERE c.user_id=?",
                (user_id,))
            items = cur.fetchall()
            for item in items:
                logger.debug(f" - {item[0]} ×{item[1]} = {item[2] * item[1]} ₽")
    except Exception as e:
        logger.error(f"Debug error: {e}")


def simplify_order_data(data: dict) -> dict:
    """Упрощает данные заказа для сохранения в metadata YooKassa"""
    return {
        "user_id": data.get('user_id', ''),
        "user_name": data.get('name', '')[:50],
        "phone": data.get('phone', '')[:20],
        "address_short": data.get('address', '')[:100],
        "delivery_date": data.get('delivery_date', ''),
        "delivery_time": data.get('delivery_time', ''),
        "payment_method": data.get('payment_method', ''),
        "cart_items_count": len(data.get('cart_items', [])),
        "type": "order"
    }


async def calculate_order_total_with_bonuses(user_id: int, delivery_cost: int = 0, bonus_to_use: int = 0) -> dict:
    """Рассчитывает итоговую сумму заказа с учетом бонусов и скидок"""
    cart_items = get_cart(user_id)
    original_products_total = sum(item['price'] * item['quantity'] for item in cart_items)

    # Проверяем первый ли это заказ
    is_first = is_first_order(user_id)
    discount = 0
    if is_first:
        discount = int(original_products_total * FIRST_ORDER_DISCOUNT)
        # Убедитесь, что скидка не превышает сумму товаров
        discount = min(discount, original_products_total)

    products_total_after_discount = max(0, original_products_total - discount)

    # Получаем информацию о бонусах пользователя
    bonus_info = get_bonus_info(user_id)
    available_bonus = bonus_info['current_bonus']

    # Максимально можно использовать бонусов - 30% от суммы товаров после скидки
    max_bonus_allowed = int(products_total_after_discount * MAX_BONUS_PERCENTAGE)

    # Если bonus_to_use не указан, используем доступный максимум
    if bonus_to_use == 0:
        actual_bonus_used = min(available_bonus, max_bonus_allowed)
    else:
        actual_bonus_used = min(bonus_to_use, available_bonus, max_bonus_allowed)

    # Итоговая сумма: товары(со скидкой) - бонусы + доставка
    final_total = max(0, products_total_after_discount - actual_bonus_used + delivery_cost)

    return {
        'original_products_total': original_products_total,  # Исходная сумма товаров
        'products_total_after_discount': products_total_after_discount,  # Сумма после скидки
        'delivery_cost': delivery_cost,
        'available_bonus': available_bonus,
        'max_bonus_allowed': max_bonus_allowed,
        'bonus_used': actual_bonus_used,
        'discount': discount,  # Сумма скидки
        'is_first_order': is_first,  # Флаг первого заказа
        'final_total': final_total  # Итоговая сумма к оплате
    }


async def apply_bonus_to_order(user_id: int, order_id: int, bonus_used: int, order_total: float):
    """Применяет бонусы к заказу и обновляет баланс"""
    if bonus_used <= 0:
        return False

    # Списываем бонусы
    success = spend_bonus_points(user_id, order_id, bonus_used, order_total)

    if success:
        # Начисляем новые бонусы (10% от итоговой суммы после применения скидки)
        bonus_earned = int((order_total - bonus_used) * BONUS_EARN_PERCENTAGE)
        if bonus_earned > 0:
            add_bonus_points(user_id, order_id, bonus_earned)

        return True
    return False


async def send_bonus_notification(user_id: int, order_id: int, bonus_used: int, bonus_earned: int, discount: int = 0):
    """Отправляет уведомление о использовании и начислении бонусов"""
    try:
        text = (
            f"🎉 <b>Заказ #{order_id} оформлен!</b>\n\n"
        )

        if discount > 0:
            text += f"🎉 <b>Скидка на первый заказ:</b> -{discount} ₽\n"

        if bonus_used > 0:
            text += f"💎 <b>Использовано бонусов:</b> {bonus_used} ₽\n"

        if bonus_earned > 0:
            text += f"💎 <b>Начислено бонусов:</b> {bonus_earned} ₽ (5% от суммы заказа)\n"

        # Получаем информацию о заказе для отображения итоговой суммы
        orders = get_user_orders(user_id)
        current_order = next((order for order in orders if order['id'] == order_id), None)

        if current_order:
            text += f"💰 <b>Итого к оплате:</b> {current_order['total']} ₽\n\n"

        text += "💡 Бонусами можно оплатить до 30% стоимости следующего заказа!"

        await bot.send_message(user_id, text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Не удалось отправить уведомление о бонусах: {e}")


# --- START ---
@router.message(Command("start"))
async def start_cmd(message: Message):
    """Обработчик команды /start - приветствие и главное меню"""
    await message.answer(
        f"Добро пожаловать в <b>{SHOP_INFO['name']}</b>! 🌸\n"
        "Каждый день новые букеты от наших флористов!\n"
        "Выберите действие:\n\n"
        "Для справочника напишите команду /help",
        reply_markup=main_menu,
        parse_mode="HTML"
    )


# --- КАТАЛОГ ---
@router.message(F.text == "🌸 Каталог")
async def show_catalog(message: Message):
    """Показывает категории товаров"""
    await message.answer(
        "🌸 <b>Наш каталог</b>\n\n"
        "Выберите категорию для просмотра товаров:",
        reply_markup=catalog_menu,
        parse_mode="HTML"
    )


@router.message(F.text == "💐 Букеты")
async def show_bouquets(message: Message):
    """Показывает букеты на сегодня"""
    try:
        cleanup_old_daily_products()
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("""
                SELECT id, name, description, full_description, price, photo, category, is_daily, on_request, in_stock 
                FROM products 
                WHERE category = 'bouquet' AND is_daily = TRUE 
                AND created_date = DATE('now') 
                ORDER BY id DESC
            """)
            bouquets = [dict(row) for row in cur.fetchall()]

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💬 Спросить у менеджера", url="https://t.me/Therry_Voyager")],
            [InlineKeyboardButton(text="💰 Подбор под бюджет", callback_data="budget_selection")]
        ])

        if not bouquets:
            await message.answer(
                "🌺 <b>На сегодня букеты еще готовятся!</b>\n\n"
                "Наши флористы создают новые композиции. "
                "Пожалуйста, зайдите позже или свяжитесь с менеджером для индивидуального заказа.\n\n"
                "💡 <i>Не нашли подходящий букет? Свяжитесь с менеджером, "
                "и мы подберем букет под ваш запрос и бюджет!</i>",  # Добавляем текст
                reply_markup=kb,
                parse_mode="HTML"
            )
            return

        today = datetime.now().strftime("%d.%m.%Y")
        await message.answer(
            f"🌸 <b>Букеты на сегодня</b>\n📅 <i>{today}</i>",
            parse_mode="HTML"
        )

        for bouquet in bouquets:
            text = f"<b>{bouquet['name']}</b>\n{bouquet['description']}\n"
            if bouquet['on_request'] or bouquet['price'] == 0:
                text += "💰 <b>Цена: по запросу</b>"
            else:
                text += f"💰 <b>Цена: {bouquet['price']} ₽</b>"
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📖 Подробнее", callback_data=f"details_{bouquet['id']}")],
                [InlineKeyboardButton(text="🛒 В корзину", callback_data=f"add_{bouquet['id']}")]
            ])

            if bouquet.get('photo') and os.path.exists(bouquet['photo']):
                photo = FSInputFile(bouquet['photo'])
                await message.answer_photo(photo=photo, caption=text, reply_markup=kb, parse_mode="HTML")
            else:
                await message.answer(text, reply_markup=kb, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Error showing bouquets: {e}")
        await message.answer(
            "❌ Произошла ошибка при загрузке каталога растений. "
            "Пожалуйста, попробуйте позже или свяжитесь с менеджером."
        )


@router.message(F.text == "🌱 Горшечные растения")
async def show_plants(message: Message):
    """Показывает горшечные растения"""
    try:
        cleanup_old_daily_products()
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("""
                SELECT id, name, description, full_description, price, photo, category, is_daily, on_request, in_stock 
                FROM products 
                WHERE category = 'plant' AND is_daily = TRUE 
                AND created_date = DATE('now') 
                ORDER BY id DESC
            """)
            plants = [dict(row) for row in cur.fetchall()]

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💬 Спросить у менеджера", url="https://t.me/Therry_Voyager")],
        ])
        if not plants:
            await message.answer(
                "🌿 <b>Горшечные растения временно отсутствуют!</b>\n\n"
                "Свяжитесь с менеджером для уточнения наличия.", reply_markup=kb,
                parse_mode="HTML"
            )
            return

        await message.answer(
            "🌱 <b>Наши горшечные растения</b>\n\n"
            "Постоянные жители нашего магазина:",
            parse_mode="HTML"
        )

        for plant in plants:
            text = f"<b>{plant['name']}</b>\n{plant['description']}\n"
            if plant['on_request'] or plant['price'] == 0:
                text += "💰 <b>Цена: по запросу</b>"
            else:
                text += f"💰 <b>Цена: {plant['price']} ₽</b>"
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📖 Подробнее", callback_data=f"details_{plant['id']}")],
                [InlineKeyboardButton(text="💬 Уточнить цену", url="https://t.me/Therry_Voyager")],
                [InlineKeyboardButton(text="🛒 В корзину", callback_data=f"add_{plant['id']}")]
            ])

            if plant.get('photo') and os.path.exists(plant['photo']):
                photo = FSInputFile(plant['photo'])
                await message.answer_photo(photo=photo, caption=text, reply_markup=kb, parse_mode="HTML")
            else:
                await message.answer(text, reply_markup=kb, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Error showing plants: {e}")
        await message.answer(
            "❌ Произошла ошибка при загрузке каталога растений. "
            "Пожалуйста, попробуйте позже или свяжитесь с менеджером."
        )


# --- ПОДРОБНОЕ ОПИСАНИЕ ---
@router.callback_query(F.data.startswith("details_"))
async def show_details(callback: CallbackQuery):
    """Показывает подробную информацию о товаре"""
    product_id = int(callback.data.split("_")[1])
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM products WHERE id=?", (product_id,))
        product = cur.fetchone()

    if product:
        text = (
            f"<b>{product['name']}</b>\n\n"
            f"📄 <i>{product['full_description']}</i>\n\n"
            f"💰 <b>Цена: {product['price']} ₽</b>"
        )

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛒 Добавить в корзину", callback_data=f"add_{product['id']}")],
            [InlineKeyboardButton(text="💬 Спросить у менеджера", url="https://t.me/Therry_Voyager")]
        ])

        if product['photo'] and os.path.exists(product['photo']):
            photo = FSInputFile(product['photo'])
            await callback.message.answer_photo(photo=photo, caption=text, reply_markup=kb, parse_mode="HTML")
        else:
            await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")

    await callback.answer()


# --- НАЗАД ---
@router.message(F.text == "⬅️ Назад в меню")
async def back_to_main_menu(message: Message):
    await message.answer("Главное меню:", reply_markup=main_menu)


@router.callback_query(F.data == "main_menu")
async def back_to_main(callback: CallbackQuery):
    await callback.message.answer("Главное меню:", reply_markup=main_menu)
    await callback.answer()


# --- ДОСТАВКА И ОПЛАТА ---
@router.message(F.text == "🚚 Доставка")
async def delivery_info(message: Message):
    # Меню раздела "Доставка и оплата"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚗 Условия доставки", callback_data="delivery_conditions")],
        [InlineKeyboardButton(text="💳 Способы оплаты", callback_data="payment_methods")],
        [InlineKeyboardButton(text="📦 Самовывоз", callback_data="pickup_info")],
        [InlineKeyboardButton(text="💬 Спросить у менеджера", url="https://t.me/Therry_Voyager")]
    ])
    # Сообщение можно сделать более соответствующим ТЗ
    await message.answer(
        "<b>🚚 ДОСТАВКА И ОПЛАТА</b>\n\n"
        "Здесь вы найдёте всю информацию о способах оплаты.\n"
        "Условия доставки и индивидуальный подбор букета — уточняйте у менеджера 👇",
        reply_markup=kb,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "delivery_conditions")
async def show_delivery_info(callback: CallbackQuery):
    # Текст полностью соответствует ТЗ и добавляет ясности
    text = (
        "<b>Условия доставки</b>\n\n"
        "<b>– По городу:</b> 300 ₽\n"
        "<b>– За МКАД: индивидуальный расчет</b>\n\n"
        "<b>Сроки:</b>\n"
        "– В день заказа (при оформлении до 15:00)\n"
        "– На следующую дату (при оформлении после 15:00)\n"
        "– На конкретную дату по предзаказу\n\n"
        "💬 <b>Для точного расчета стоимости доставки:</b>\n"
        "Свяжитесь с менеджером 👇"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Спросить у менеджера", url="https://t.me/Therry_Voyager")]
    ])

    await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "payment_methods")
async def show_payment_info(callback: CallbackQuery):
    text = (
        "<b>Способы оплаты</b>\n\n"
        "💳 <b>Онлайн оплата:</b>\n"
        "• Банковской картой\n"
        "• ЮMoney\n"
        "• СБП\n\n"
        "💵 <b>При получении:</b>\n"
        "• Наличными\n"
        "• Картой курьеру\n\n"
        "🎁 <b>Подарочные сертификаты</b>\n\n"
        "💬 <b>Подробности:</b>\n"
        "Спросить у менеджера 👇"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Спросить у менеджера", url="https://t.me/Therry_Voyager")]
    ])

    await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "pickup_info")
async def show_pickup_info(callback: CallbackQuery):
    # Информация о самовывозе
    text = (
        "<b>Самовывоз</b>\n\n"
        "Вы можете забрать заказ самостоятельно по адресу:\n"
        f"📍 <b>{SHOP_INFO['address']}</b>\n\n"
        f"🕒 <b>Часы работы:</b> {SHOP_INFO['work_hours']}\n"
        f"📞 <b>Телефон для связи:</b> {SHOP_INFO['phone']}"
    )
    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()


# --- СВЯЗЬ С МЕНЕДЖЕРОМ ---
@router.message(F.text == "📞 Менеджер")
async def manager(message: Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❓ Задать вопрос", callback_data="contact_and_question")],
        [InlineKeyboardButton(text="⚡ Срочный заказ", callback_data="urgent_order")]
    ])

    await message.answer(
        "👋 <b>Свяжитесь с менеджером</b>\n\n"
        "• Ответим на все вопросы\n"
        "• Поможем с выбором букета\n"
        "• Уточним наличие и сроки\n"
        "• Примем срочный заказ\n\n"
        "<i>Выберите способ связи:</i>",
        reply_markup=kb,
        parse_mode="HTML"
    )


# Сбор данных (номер телефона и вопрос)
@router.callback_query(F.data == "contact_and_question")
async def collect_contact_and_question(callback: CallbackQuery, state: FSMContext):
    await state.update_data(request_type="question")
    await callback.message.answer(
        "📞 <b>Оставьте ваш номер телефона и вопрос</b>\n\n"
        "<b>Важно:</b>\n"
        "Не пишите всё в одну строчку\n"
        "Пишите на новой строке — так нам будет проще всё организовать.\n"
        "Отвечайте четко на каждый поставленный вопрос и не забудьте номер телефона — иначе не сможем с вами связаться.\n\n"
        "Введите номер телефона в формате <code>+7 XXX XXX XX XX</code> и далее напишите ваш вопрос.\n"
        "Пример:\n"
        "<code>+7 900 123 45 67</code>\n"
        "Почему задерживается доставка моего заказа?",
        parse_mode="HTML"
    )
    await state.set_state(ManagerRequestState.contact_and_question)
    await callback.answer()


# Обработка полученных данных (контакты и вопрос)
@router.message(ManagerRequestState.contact_and_question)
async def process_contact_and_question(message: Message, state: FSMContext):
    # Проверяем, не является ли текст системной командой
    if message.text in MENU_COMMANDS:
        await state.clear()  # Прерываем ввод

        # Вызываем нужный обработчик в зависимости от команды
        if message.text == "🌸 Каталог":
            await show_catalog(message)
        elif message.text == "🚚 Доставка":
            await delivery_info(message)
        elif message.text == "📞 Менеджер":
            await manager(message, state)
        elif message.text == "📍 На карте":
            await map_handler(message)
        elif message.text == "🎁 Сертификат":
            await cert_menu(message)
        elif message.text == "⭐ Отзывы":
            await reviews_menu(message)
        elif message.text == "🛒 Корзина":
            await show_cart(message)
        elif message.text == "🧾 Мои заказы":
            await my_orders(message)
        elif message.text == "⬅️ Назад в меню":
            await back_to_main_menu(message)
        elif message.text == "🏠 В меню":
            await back_to_main(message)
        return  # Важно: выходим, чтобы не продолжать обработку вопроса

    # Если это не команда — продолжаем обработку номера и вопроса
    data = await state.get_data()
    request_type = data.get("request_type", "question")

    parts = message.text.strip().split("\n", 1)
    if len(parts) < 2:
        await message.answer("❌ Пожалуйста, введите номер и вопрос на разных строках.")
        return

    phone = parts[0].strip()
    question = parts[1].strip()

    if not phone.replace('+', '').replace(' ', '').replace('-', '').replace('(', '').replace(')', '').isdigit():
        await message.answer("❌ Неправильный формат телефона. Повторите ввод.")
        return

    if request_type == "urgent":
        admin_msg = (
            "🚨🚨🚨 <b>СРОЧНЫЙ ЗАКАЗ! ВЫСОКИЙ ПРИОРИТЕТ!</b> 🚨🚨🚨\n\n"
            f"👤 <b>Имя:</b> {message.from_user.full_name}\n"
            f"🆔 <b>ID пользователя:</b> {message.from_user.id}\n"
            f"📞 <b>Телефон:</b> {phone}\n"
            f"💬 <b>Запрос:</b> {question}\n\n"
            "⚠️ <b>ТРЕБУЕТСЯ НЕМЕДЛЕННАЯ ОБРАБОТКА!</b>"
        )
    else:
        admin_msg = (
            "📞 <b>Запрос от клиента</b>\n\n"
            f"👤 <b>Имя:</b> {message.from_user.full_name}\n"
            f"🆔 <b>ID пользователя:</b> {message.from_user.id}\n"
            f"📞 <b>Телефон:</b> {phone}\n"
            f"💬 <b>Вопрос:</b> {question}"
        )

    try:
        await notify_admins(admin_msg)
        await message.answer(
            "✅ Ваше обращение принято и передано менеджеру.\n"
            "Скоро с вами свяжутся для решения вашего вопроса.",
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"Не удалось отправить сообщение менеджеру: {e}")
        await message.answer(
            "❌ Произошла ошибка при отправке сообщения. "
            "Пожалуйста, попробуйте еще раз или свяжитесь с менеджером напрямую: @mgk71"
        )

    await state.clear()


# Быстрая подача срочного заказа
@router.callback_query(F.data == "urgent_order")
async def urgent_order_handler(callback: CallbackQuery, state: FSMContext):
    await state.update_data(request_type="urgent")
    await callback.message.answer(
        "⚡ <b>Срочный заказ</b>\n\n"
        "Опишите вашу ситуацию и потребности, и мы постараемся оперативно обработать ваш запрос.\n\n"
        "<b>Важно:</b>\n"
        "Не пишите всё в одну строчку\n"
        "Пишите на новой строке — так нам будет проще всё организовать.\n"
        "Отвечайте четко на каждый поставленный вопрос и не забудьте номер телефона — иначе не сможем с вами связаться.\n\n"
        "<b>Введите в формате:</b>\n"
        "<code>+7 XXX XXX XX XX</code>\n"
        "Нужен букет на сегодняшний вечер\n"
        "Адрес доставки: ул. Цветочная, д. 1\n"
        "Желательно к 18:00",
        parse_mode="HTML"
    )
    await state.set_state(ManagerRequestState.contact_and_question)
    await callback.answer()


# --- НА КАРТЕ ---
@router.message(F.text == "📍 На карте")
async def map_handler(message: Message):
    await message.answer(
        f"📍 <b>Адрес:</b> {SHOP_INFO['address']}\n"
        f"📞 <b>Телефон:</b> {SHOP_INFO['phone']}\n"
        f"🕒 <b>Часы работы:</b> {SHOP_INFO['work_hours']}\n\n"
        "🔗 [Открыть в Яндекс.Картах](https://yandex.ru/maps/-/CHtdIO3I)",
        parse_mode="HTML"
    )


@router.message(F.text == "🎁 Сертификат")
async def cert_menu(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1000 ₽", callback_data="cert_1000"),
         InlineKeyboardButton(text="3000 ₽", callback_data="cert_3000"),
         InlineKeyboardButton(text="5000 ₽", callback_data="cert_5000")],
        [InlineKeyboardButton(text="🏠 В меню", callback_data="main_menu")]
    ])
    await message.answer(
        "🎁 <b>Выберите номинал подарочного сертификата:</b>\n\n"
        "• 💳 Оплата картой или СБП\n"
        "• 📄 Мгновенная выдача после оплаты\n"
        "• 🎯 Действует 1 год\n"
        "• 🌸 На любой товар в магазине",
        reply_markup=kb,
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("cert_"))
async def handle_certificate_purchase(callback: CallbackQuery, state: FSMContext):
    """Обработка покупки сертификата"""
    amount_str = callback.data.split("_")[1]
    try:
        amount = int(amount_str)

        # Проверка минимальной суммы для ЮKassa (минимум 1 рубль)
        if amount < 1:
            await callback.answer("❌ Минимальная сумма - 1 рубль")
            return

        # Создаем платеж для сертификата
        cert_code = f"CERT-{uuid.uuid4().hex[:8].upper()}"

        # Упрощаем metadata для сертификата
        simplified_metadata = {
            "user_id": callback.from_user.id,
            "cert_code": cert_code,
            "phone": "9999999999",
            "type": "certificate"
        }

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
            await callback.message.answer(
                f"🎁 <b>Сертификат на {amount} ₽</b>\n\n"
                "⚠️ Платежная система временно недоступна.\n"
                "📞 Для покупки сертификата свяжитесь с менеджером.",
                parse_mode="HTML"
            )

    except ValueError:
        await callback.answer("❌ Неверный номинал сертификата")

    await callback.answer()


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
                "return_url": "https://t.me/flowersstories_bot"  # URL вашего бота
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
            "📞 Для покупки сертификата свяжитесь с менеджером: @Therry_Voyager\n\n"
            f"Код сертификата: <code>{cert_code}</code>\n"
            "Сообщите этот код менеджеру для активации.",
            parse_mode="HTML"
        )


@router.callback_query(F.data.startswith("cert_"))
async def handle_certificate_purchase(callback: CallbackQuery, state: FSMContext):
    """Обработка покупки сертификата (единый поток через payment_manager)"""
    amount_str = callback.data.split("_")[1]
    try:
        amount = int(amount_str)

        # Проверка минимальной суммы для ЮKassa (минимум 1 рубль)
        if amount < 1:
            await callback.answer("❌ Минимальная сумма - 1 рубль")
            return

        # Генерируем код сертификата
        cert_code = f"CERT-{uuid.uuid4().hex[:8].upper()}"

        # Упрощенные метаданные
        metadata = {
            "user_id": callback.from_user.id,
            "cert_code": cert_code,
            "phone": "9999999999",  # Можно заменить на реальный телефон позже
            "type": "certificate"
        }

        # Создаём платеж через единый менеджер
        payment = await payment_manager.create_payment(
            amount=amount,
            description=f"Подарочный сертификат на {amount}₽",
            metadata=metadata
        )

        if payment and payment.get("confirmation_url"):
            # Сохраняем данные в FSM
            await state.update_data(
                payment_id=payment["id"],
                cert_amount=amount,
                cert_code=cert_code,
                payment_url=payment["confirmation_url"]
            )
            await state.set_state(CertificateState.waiting_payment)

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
            # Ошибка — показываем fallback
            await callback.message.answer(
                f"🎁 <b>Сертификат на {amount} ₽</b>\n\n"
                "⚠️ Платёжная система временно недоступна.\n"
                "📞 Для покупки сертификата свяжитесь с менеджером: @Therry_Voyager\n\n"
                f"Код сертификата: <code>{cert_code}</code>",
                parse_mode="HTML"
            )

    except ValueError:
        await callback.answer("❌ Неверный номинал сертификата")

    await callback.answer()


@router.callback_query(F.data.startswith("check_cert_payment_"))
async def check_cert_payment(callback: CallbackQuery, state: FSMContext):
    payment_id = callback.data.split("_")[-1]

    try:
        payment = Payment.find_one(payment_id)
        if payment.status == "succeeded":
            data = await state.get_data()
            amount = data.get("cert_amount")
            cert_code = data.get("cert_code")

            # Генерируем PDF
            pdf_path = f"certificates/cert_{callback.from_user.id}_{amount}.pdf"  # Измененный путь
            generate_certificate(str(amount), cert_code, pdf_path)

            # Отправляем PDF
            if os.path.exists(pdf_path):
                pdf = FSInputFile(pdf_path)
                await callback.message.answer_document(
                    document=pdf,
                    caption=f"🎉 Поздравляем! Вы купили сертификат на {amount} ₽\nКод: `{cert_code}`",
                    parse_mode="HTML"
                )
            else:
                await callback.message.answer(
                    f"🎉 Поздравляем! Вы купили сертификат на {amount} ₽\nКод: `{cert_code}`\n\n"
                    "⚠️ PDF сертификат временно недоступен, но код действителен.",
                    parse_mode="HTML"
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
        else:
            await callback.message.answer("❌ Платёж не прошёл")
    except Exception as e:
        logger.error(f"Error processing certificate payment: {e}")
        await callback.message.answer("❌ Ошибка при обработке платежа. Попробуйте позже.")

    await callback.answer()


# --- ОТЗЫВЫ ---
class ReviewState(StatesGroup):
    order_id = State()
    text = State()
    rating = State()


@router.message(F.text == "⭐ Отзывы")
async def reviews_menu(message: Message):
    # Проверяем есть ли доставленные заказы
    delivered_orders = get_delivered_orders(message.from_user.id)
    has_delivered_orders = len(delivered_orders) > 0

    kb = InlineKeyboardMarkup(inline_keyboard=[])

    if has_delivered_orders:
        kb.inline_keyboard.append([InlineKeyboardButton(text="🌟 Оценить заказ", callback_data="rate_order")])

    kb.inline_keyboard.extend([
        [InlineKeyboardButton(text="✍ Общий отзыв", callback_data="leave_general_review")],
        [InlineKeyboardButton(text="📖 Прочитать отзывы", callback_data="read_reviews")]
    ])

    if has_delivered_orders:
        await message.answer(
            "✅ У вас есть доставленные заказы! Вы можете оставить отзыв о конкретном заказе.",
            reply_markup=kb
        )
    else:
        await message.answer(
            "📝 Вы можете оставить общий отзыв о нашем магазине или посмотреть отзывы других клиентов.",
            reply_markup=kb
        )


@router.callback_query(F.data == "read_reviews")
async def read_reviews(callback: CallbackQuery):
    reviews = get_reviews()
    if not reviews:
        await callback.message.answer("📝 Пока нет отзывов. Будьте первым!")
        await callback.answer()
        return

    text = "⭐ <b>Последние отзывы:</b>\n\n"
    for i, review in enumerate(reviews, 1):
        stars = "⭐" * min(5, max(1, review.get('rating', 5)))
        created_at = review['created_at'].split(".")[0] if isinstance(review['created_at'], str) else \
            str(review['created_at']).split(".")[0]
        created_at = created_at.replace("T", " ")

        username = review.get('user_name', 'Аноним')
        order_info = f" (Заказ #{review.get('order_id', '')})" if review.get('order_id') else ""

        text += f"{stars}\n"
        text += f"<i>\"{review['text']}\"</i>\n"
        text += f"<b>— {username}{order_info}</b>\n"
        text += f"<code>📅 {created_at}</code>\n\n"

    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "leave_general_review")
async def start_general_review(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "📝 <b>Напишите ваш общий отзыв о нашем магазине</b>\n\n"
        "Поделитесь вашими впечатлениями о качестве цветов, обслуживании или работе сайта:",
        parse_mode="HTML"
    )
    await state.set_state(ReviewState.text)
    await state.update_data(order_id=None)  # Общий отзыв без привязки к заказу
    await callback.answer()


@router.callback_query(F.data == "rate_order")
async def select_order_for_review(callback: CallbackQuery, state: FSMContext):
    delivered_orders = get_delivered_orders(callback.from_user.id)

    if not delivered_orders:
        await callback.message.answer("❌ У вас нет доставленных заказов для оценки.")
        await callback.answer()
        return

    # Создаем клавиатуру с заказами
    kb = InlineKeyboardMarkup(inline_keyboard=[])

    for order in delivered_orders[:5]:  # Показываем последние 5 заказов
        order_date = order['delivery_date'] or order['created_at'].split(' ')[0]
        kb.inline_keyboard.append([
            InlineKeyboardButton(
                text=f"Заказ #{order['id']} от {order_date} - {order['total']}₽",
                callback_data=f"review_order_{order['id']}"
            )
        ])

    kb.inline_keyboard.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_review")])

    await callback.message.answer(
        "📦 <b>Выберите заказ для оценки:</b>",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("review_order_"))
async def start_order_review(callback: CallbackQuery, state: FSMContext):
    order_id = int(callback.data.split("_")[2])

    # Проверяем, что заказ действительно доставлен и принадлежит пользователю
    delivered_orders = get_delivered_orders(callback.from_user.id)
    order_exists = any(order['id'] == order_id for order in delivered_orders)

    if not order_exists:
        await callback.message.answer("❌ Заказ не найден или еще не доставлен.")
        await state.clear()
        await callback.answer()
        return

    await state.update_data(order_id=order_id)
    await callback.message.answer(
        "📝 <b>Напишите отзыв о вашем заказе</b>\n\n"
        "Что вам понравилось в заказе? Что мы можем улучшить?",
        parse_mode="HTML"
    )
    await state.set_state(ReviewState.text)
    await callback.answer()


@router.callback_query(F.data == "cancel_review")
async def cancel_review(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("❌ Отмена оставления отзыва.")
    await callback.answer()


@router.message(ReviewState.text)
async def get_review_text(message: Message, state: FSMContext):
    if len(message.text) < 10:
        await message.answer("❌ Отзыв слишком короткий. Напишите хотя бы 10 символов.")
        return

    await state.update_data(text=message.text)

    rating_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐", callback_data="rating_1"),
         InlineKeyboardButton(text="⭐⭐", callback_data="rating_2"),
         InlineKeyboardButton(text="⭐⭐⭐", callback_data="rating_3"),
         InlineKeyboardButton(text="⭐⭐⭐⭐", callback_data="rating_4"),
         InlineKeyboardButton(text="⭐⭐⭐⭐⭐", callback_data="rating_5")]
    ])

    await message.answer("🌟 <b>Оцените от 1 до 5 звезд:</b>",
                         reply_markup=rating_kb,
                         parse_mode="HTML")
    await state.set_state(ReviewState.rating)


@router.callback_query(F.data.startswith("rating_"), ReviewState.rating)
async def save_review_with_rating(callback: CallbackQuery, state: FSMContext):
    rating = int(callback.data.split("_")[1])
    data = await state.get_data()
    order_id = data.get('order_id')

    # Сохраняем отзыв
    add_review(
        user_id=callback.from_user.id,
        user_name=callback.from_user.full_name,
        text=data['text'],
        rating=rating,
        order_id=order_id
    )

    stars = "⭐" * rating
    if order_id:
        message_text = f"✅ <b>Спасибо за отзыв о заказе #{order_id}!</b> {stars}\n\n"
    else:
        message_text = f"✅ <b>Спасибо за ваш отзыв!</b> {stars}\n\n"

    message_text += "Ваше мнение очень важно для нас и поможет стать лучше!"

    await callback.message.answer(message_text, parse_mode="HTML")

    # Отправляем уведомление админу
    try:
        order_info = f" (Заказ #{order_id})" if order_id else " (Общий отзыв)"
        admin_msg = (
            "📝 <b>Новый отзыв</b>\n"
            f"👤 {callback.from_user.full_name}\n"
            f"⭐ Оценка: {rating}/5{order_info}\n"
            f"💬 Отзыв: {data['text']}"
        )
        await notify_admins(admin_msg)
    except Exception as e:
        print(f"Не удалось отправить уведомление админу: {e}")

    await state.clear()
    await callback.answer()


async def ask_for_review_after_delivery(user_id: int, order_id: int):
    """Функция для запроса отзыва после доставки заказа"""
    try:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⭐ Оставить отзыв", callback_data=f"review_order_{order_id}")],
            [InlineKeyboardButton(text="📞 Написать менеджеру", callback_data="ask_question")]
        ])

        await bot.send_message(
            user_id,
            f"🎉 <b>Ваш заказ #{order_id} доставлен!</b>\n\n"
            "Понравился ли вам заказ? Поделитесь вашими впечатлениями!",
            reply_markup=kb,
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"Не удалось отправить запрос на отзыв: {e}")


# --- КОРЗИНА ---
@router.callback_query(F.data.startswith("add_"))
async def add_to_cart_handler(callback: CallbackQuery):
    product_id = int(callback.data.split("_")[1])

    # Проверяем наличие товара
    if not check_product_availability(product_id):
        await callback.answer("❌ Товара нет в наличии")
        return

    add_to_cart(callback.from_user.id, product_id)
    await callback.answer("✅ Добавлено в корзину")

    count = sum(item['quantity'] for item in get_cart(callback.from_user.id))
    await callback.message.answer(f"🛒 Товар добавлен в корзину!\nВсего теперь: {count} шт.")


@router.callback_query(F.data.startswith("check_avail_"))
async def check_availability_product(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Спросить у менеджера", url="https://t.me/Therry_Voyager")]])
    text = f"📞 Свяжитесь с менеджером, чтобы узнать о наличии товара"
    await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.message(F.text == "🛒 Корзина")
async def show_cart(message: Message):
    cart_items = get_cart(message.from_user.id)

    if not cart_items:
        await message.answer("Корзина пуста 🛒", reply_markup=cart_keyboard(cart_items))
        return

    total = sum(item['price'] * item['quantity'] for item in cart_items)
    text = "🛒 <b>Ваша корзина</b>\n\n"

    for item in cart_items:
        status = "✅" if item['in_stock'] else "❌"
        text += f"{status} {item['name']} - {item['price']} ₽ × {item['quantity']} = {item['quantity'] * item['price']} ₽\n"

    text += f"\n<b>Итого: {total} ₽</b>"

    # Проверяем наличие всех товаров
    all_in_stock = all(item['in_stock'] for item in cart_items)

    if all_in_stock:
        await message.answer(text, reply_markup=cart_keyboard(cart_items), parse_mode="HTML")
    else:
        await message.answer(
            text + "\n\n⚠️ Некоторые товары отсутствуют в наличии. "
                   "Удалите их из корзины или свяжитесь с менеджером для уточнения сроков поставки.",
            reply_markup=cart_keyboard(cart_items),
            parse_mode="HTML"
        )


@router.callback_query(F.data.startswith("remove_"))
async def remove_from_cart(callback: CallbackQuery):
    """Уменьшение количества товара в корзине"""
    product_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id

    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT quantity FROM cart WHERE user_id=? AND product_id=?", (user_id, product_id))
        row = cur.fetchone()

        if row and row[0] > 1:
            # Уменьшаем количество
            cur.execute("UPDATE cart SET quantity = quantity - 1 WHERE user_id=? AND product_id=?",
                        (user_id, product_id))
        else:
            # Удаляем товар из корзины
            cur.execute("DELETE FROM cart WHERE user_id=? AND product_id=?", (user_id, product_id))

        conn.commit()

    await callback.answer("✅ Количество уменьшено")
    await show_cart(callback.message)


async def update_cart_button(message: Message):
    cart = get_cart(message.from_user.id)
    count = sum(item['quantity'] for item in cart)
    text = f"🛒 Корзина ({count})" if count else "🛒 Корзина"
    # Обновите меню с динамическим текстом (нужно хранить сообщение)


async def update_main_menu(message: Message):
    cart = get_cart(message.from_user.id)
    count = sum(item['quantity'] for item in cart)
    text = f"🛒 Корзина ({count})" if count else "🛒 Корзина"
    # Но это сложно без хранения message_id


@router.callback_query(F.data == "clear_cart")
async def clear_cart_handler(callback: CallbackQuery):
    clear_cart(callback.from_user.id)
    await callback.answer("Корзина очищена")
    await callback.message.edit_text("Корзина пуста.")


@router.callback_query(F.data == "cancel_order")
async def cancel_order(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("❌ Заказ отменен.")
    await callback.answer()


# Обработчик для новой кнопки
@router.message(F.text == "💰 Подбор под бюджет")
async def budget_menu_handler(message: Message, state: FSMContext):
    await start_budget_selection_from_message(message, state)


@router.message(F.text.contains("бюджет"))
async def budget_keyword_handler(message: Message, state: FSMContext):
    """Обработчик ключевого слова "бюджет" """
    await start_budget_selection_from_message(message, state)


async def start_budget_selection_from_message(message: Message, state: FSMContext):
    """Запуск подбора по бюджету из текстового сообщения"""
    await message.answer(
        "💰 <b>Подбор букета под ваш бюджет</b>\n\n"
        "Не нашли подходящий букет? Мы поможем!\n\n"
        "💡 Наши флористы подберут идеальный вариант:\n"
        "• В рамках вашего бюджета\n"
        "• С учетом ваших предпочтений\n"
        "• Быстро и профессионально\n\n"
        "📝 Введите сумму вашего бюджета (в рублях):",
        parse_mode="HTML"
    )
    await state.set_state(BudgetRequestState.budget)


@router.callback_query(F.data == "budget_selection")
async def start_budget_selection(callback: CallbackQuery, state: FSMContext):
    """Начало процесса подбора по бюджету"""
    await callback.message.answer(
        "💰 <b>Подбор букета под ваш бюджет</b>\n\n"
        "Наши флористы подберут идеальный букет именно для вас!\n\n"
        "💡 Укажите ваш бюджет, и мы предложим:\n"
        "• Несколько вариантов букетов\n"
        "• Индивидуальные рекомендации\n"
        "• Быстрый ответ в течение 15 минут\n\n"
        "📝 Введите сумму вашего бюджета (в рублях):",
        parse_mode="HTML"
    )
    await state.set_state(BudgetRequestState.budget)
    await callback.answer()


@router.message(BudgetRequestState.budget)
async def get_budget_amount(message: Message, state: FSMContext):
    """Получаем бюджет от пользователя"""
    try:
        budget = int(message.text.strip())
        if budget < 500:  # Минимальный бюджет
            await message.answer("❌ Минимальный бюджет - 500 рублей. Введите сумму еще раз:")
            return

        await state.update_data(budget=budget)
        await message.answer(
            "📞 <b>Введите ваш номер телефона для связи:</b>\n\n"
            "Менеджер свяжется с вами для уточнения деталей.\n"
            "Формат: +7 XXX XXX XX XX",
            parse_mode="HTML"
        )
        await state.set_state(BudgetRequestState.phone)

    except ValueError:
        await message.answer("❌ Пожалуйста, введите число. Например: 2000")


@router.message(BudgetRequestState.phone)
async def get_budget_phone(message: Message, state: FSMContext):
    """Получаем телефон пользователя"""
    phone = message.text.strip()

    # Простая валидация номера
    if not phone.replace('+', '').replace(' ', '').replace('-', '').replace('(', '').replace(')', '').isdigit():
        await message.answer("❌ Неверный формат телефона. Введите номер в формате +7 XXX XXX XX XX:")
        return

    await state.update_data(phone=phone)
    await message.answer(
        "🎨 <b>Расскажите о ваших предпочтениях</b>\n\n"
        "Что бы вы хотели видеть в букете?\n\n"
        "Например:\n"
        "• Любимые цветы (розы, тюльпаны, хризантемы)\n"
        "• Цветовая гамма (красный, белый, пастельные тона)\n"
        "• Повод (день рождения, 8 марта, просто так)\n"
        "• Особые пожелания\n\n"
        "💬 Опишите кратко, что вам нравится:",
        parse_mode="HTML"
    )
    await state.set_state(BudgetRequestState.preferences)


@router.message(BudgetRequestState.preferences)
async def get_budget_preferences(message: Message, state: FSMContext):
    """Получаем предпочтения и отправляем менеджеру"""
    preferences = message.text
    data = await state.get_data()
    budget = data['budget']
    phone = data['phone']  # Получаем телефон из state

    # Формируем сообщение для менеджера с номером телефона
    admin_message = (
        "💰 <b>НОВАЯ ЗАЯВКА: ПОДБОР ПОД БЮДЖЕТ</b>\n\n"
        f"👤 <b>Клиент:</b> {message.from_user.full_name}\n"
        f"📞 <b>Телефон:</b> {phone}\n"  # Добавляем телефон
        f"🆔 <b>ID:</b> {message.from_user.id}\n"
        f"💵 <b>Бюджет:</b> {budget} ₽\n"
        f"🎨 <b>Предпочтения:</b>\n{preferences}\n\n"
        f"⚡ <b>СРОЧНО ОБРАБОТАТЬ!</b>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Написать клиенту",
                              url=f"https://t.me/{message.from_user.username}" if message.from_user.username else
                              f"tg://user?id={message.from_user.id}")]
    ])

    # Отправляем всем админам
    try:
        await notify_admins(admin_message)
        await message.answer(
            "✅ <b>Ваша заявка принята!</b>\n\n"
            f"💵 Бюджет: {budget} ₽\n"
            f"📞 Телефон: {phone}\n"
            f"🎨 Ваши пожелания: {preferences}\n\n"
            "📞 Менеджер свяжется с вами в течение 15 минут "
            "с вариантами букетов в рамках вашего бюджета!",
            parse_mode="HTML"
        )
    except Exception as e:
        await message.answer(
            "❌ Произошла ошибка при отправке заявки. "
            "Пожалуйста, напишите менеджеру напрямую: @mgk71"
        )
        logger.error(f"Budget request error: {e}")

    await state.clear()


# --- ОФОРМЛЕНИЕ ЗАКАЗА ---
@router.callback_query(F.data == "checkout")
async def start_checkout(callback: CallbackQuery, state: FSMContext):
    """Начинает процесс оформления заказа"""
    if not get_cart(callback.from_user.id):
        await callback.answer("Корзина пуста!")
        return

    cart_items = get_cart(callback.from_user.id)
    if not all(item['in_stock'] for item in cart_items):
        await callback.answer("❌ Некоторые товары отсутствуют в наличии.")
        return

    # Рассчитываем сумму с учетом скидок и бонусов
    try:
        calculation = await calculate_order_total_with_bonuses(callback.from_user.id)
    except Exception as e:
        logger.error(f"Ошибка в calculate_order_total_with_bonuses: {e}")
        await callback.message.answer("❌ Произошла ошибка при расчёте суммы заказа. Попробуйте позже.")
        return

    # Сохраняем ВСЕ данные расчета в state
    await state.update_data(
        original_products_total=calculation['original_products_total'],  # Исходная сумма товаров
        products_total_after_discount=calculation['products_total_after_discount'],  # Сумма после скидки
        discount=calculation['discount'],
        is_first_order=calculation['is_first_order'],
        available_bonus=calculation['available_bonus'],
        max_bonus_allowed=calculation['max_bonus_allowed'],
        bonus_used=0,  # Пока не использовали бонусы
        delivery_cost=calculation.get('delivery_cost', 0)  # Добавляем стоимость доставки
    )

    # Предлагаем использовать бонусы, если они есть
    if calculation['available_bonus'] > 0 and calculation['max_bonus_allowed'] > 0:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"💎 Использовать бонусы (до {calculation['max_bonus_allowed']}₽)",
                                  callback_data="use_bonus")],
            [InlineKeyboardButton(text="💳 Без бонусов", callback_data="skip_bonus")]
        ])

        await callback.message.answer(
            f"💎 <b>У вас есть {calculation['available_bonus']}₽ бонусов</b>\n"
            f"Можно использовать: до {calculation['max_bonus_allowed']}₽ (30% от заказа)\n\n"
            "Хотите использовать бонусы для оплаты?",
            reply_markup=kb,
            parse_mode="HTML"
        )
        await callback.answer()
        return

    # Если бонусов нет, сразу переходим к вводу имени
    await callback.message.answer("👤 Введите ваше имя:")
    await state.set_state(OrderState.name)
    await callback.answer()


@router.message(OrderState.name)
async def get_name(message: Message, state: FSMContext):
    if message.text in MENU_COMMANDS:
        await state.clear()  # Прерываем ввод

        # Вызываем нужный обработчик в зависимости от команды
        if message.text == "🌸 Каталог":
            await show_catalog(message)
        elif message.text == "🚚 Доставка":
            await delivery_info(message)
        elif message.text == "📞 Менеджер":
            await manager(message, state)
        elif message.text == "📍 На карте":
            await map_handler(message)
        elif message.text == "🎁 Сертификат":
            await cert_menu(message)
        elif message.text == "⭐ Отзывы":
            await reviews_menu(message)
        elif message.text == "🛒 Корзина":
            await show_cart(message)
        elif message.text == "🧾 Мои заказы":
            await my_orders(message)
        elif message.text == "⬅️ Назад в меню":
            await back_to_main_menu(message)
        elif message.text == "🏠 В меню":
            await back_to_main(message)
        return  # Важно: выходим, чтобы не продолжать обработку вопроса

    await state.update_data(name=message.text)
    await message.answer("📞 Введите ваш телефон (в формате +7 XXX XXX XX XX):")
    await state.set_state(OrderState.phone)


@router.message(OrderState.phone)
async def get_phone(message: Message, state: FSMContext):
    phone = message.text.strip()

    # Простая валидация номера
    if not phone.replace('+', '').replace(' ', '').replace('-', '').replace('(', '').replace(')', '').isdigit():
        await message.answer("❌ Неверный формат телефона. Введите номер в формате +7 XXX XXX XX XX:")
        return

    await state.update_data(phone=phone)

    # Предлагаем выбрать тип доставки
    delivery_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚚 Доставка", callback_data="delivery_type_delivery")],
        [InlineKeyboardButton(text="📦 Самовывоз", callback_data="delivery_type_pickup")]
    ])

    await message.answer(
        "📦 <b>Выберите способ получения заказа:</b>",
        reply_markup=delivery_kb,
        parse_mode="HTML"
    )
    await state.set_state(OrderState.delivery_type)


@router.callback_query(F.data.startswith("delivery_type_"))
async def get_delivery_type(callback: CallbackQuery, state: FSMContext):
    delivery_type = callback.data.split("_")[2]  # delivery или pickup
    await state.update_data(delivery_type=delivery_type)

    # РАССЧИТЫВАЕМ СТОИМОСТЬ ДОСТАВКИ ПРЯМО ЗДЕСЬ
    delivery_cost = 0 if delivery_type == "pickup" else 300
    await state.update_data(delivery_cost=delivery_cost)  # Сохраняем рассчитанную стоимость

    if delivery_type == "pickup":
        # Для самовывоза сразу переходим к выбору даты
        await state.update_data(address=SHOP_INFO['address'])

        available_dates = get_available_delivery_dates()
        dates_kb = InlineKeyboardMarkup(inline_keyboard=[])

        for date in available_dates:
            dates_kb.inline_keyboard.append([
                InlineKeyboardButton(text=date, callback_data=f"delivery_date_{date}")
            ])

        await callback.message.answer(
            "📅 Выберите дату получения заказа:",
            reply_markup=dates_kb
        )
        await state.set_state(OrderState.delivery_date)

    else:
        # Для доставки запрашиваем адрес
        await callback.message.answer("🏠 Введите адрес доставки:")
        await state.set_state(OrderState.address)

    await callback.answer()


@router.message(OrderState.address)
async def get_address(message: Message, state: FSMContext):
    await state.update_data(address=message.text)

    await state.update_data(delivery_cost=0)  # Временное значение

    available_dates = get_available_delivery_dates()
    dates_kb = InlineKeyboardMarkup(inline_keyboard=[])

    for date in available_dates:
        dates_kb.inline_keyboard.append([
            InlineKeyboardButton(text=date, callback_data=f"delivery_date_{date}")
        ])

    await message.answer("📅 Выберите дату доставки:", reply_markup=dates_kb)
    await state.set_state(OrderState.delivery_date)


@router.callback_query(F.data.startswith("delivery_date_"))
async def get_delivery_date(callback: CallbackQuery, state: FSMContext):
    delivery_date = callback.data.split("_")[2]
    await state.update_data(delivery_date=delivery_date)

    # Предлагаем выбрать время
    time_slots = get_delivery_time_slots()
    time_kb = InlineKeyboardMarkup(inline_keyboard=[])

    for time_slot in time_slots:
        time_kb.inline_keyboard.append([
            InlineKeyboardButton(text=time_slot, callback_data=f"delivery_time_{time_slot}")
        ])

    data = await state.get_data()
    delivery_type = data.get('delivery_type', 'delivery')

    if delivery_type == "pickup":
        await callback.message.answer("⏰ Выберите время получения:", reply_markup=time_kb)
    else:
        await callback.message.answer("⏰ Выберите время доставки:", reply_markup=time_kb)

    await state.set_state(OrderState.delivery_time)
    await callback.answer()


@router.callback_query(F.data.startswith("delivery_time_"))
async def get_delivery_time(callback: CallbackQuery, state: FSMContext):
    delivery_time = callback.data.split("_")[2]
    await state.update_data(delivery_time=delivery_time)

    # Предлагаем выбрать способ оплаты с добавлением "Через менеджера"
    payment_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Онлайн картой", callback_data="pay_online")],
        [InlineKeyboardButton(text="💵 Наличными при получении", callback_data="pay_cash")],
        [InlineKeyboardButton(text="🔄 СБП", callback_data="pay_sbp")],
        [InlineKeyboardButton(text="🎁 Сертификат", callback_data="pay_cert")],
        [InlineKeyboardButton(text="💬 Через менеджера", callback_data="pay_manager")]
    ])

    await callback.message.answer("💳 Выберите способ оплаты:", reply_markup=payment_kb)
    await state.set_state(OrderState.payment)
    await callback.answer()


# --- ОБРАБОТЧИКИ СПОСОБОВ ОПЛАТЫ ---
@router.callback_query(F.data.in_(["pay_online", "pay_sbp", "pay_cash", "pay_cert", "pay_manager"]))
async def handle_payment_selection(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора способа оплаты"""
    try:
        # Получаем выбранный метод оплаты
        payment_method = callback.data.split("_")[1] if "_" in callback.data else callback.data.replace("pay_", "")
        await state.update_data(payment_method=payment_method)

        # Получаем данные из state
        data = await state.get_data()
        user_id = callback.from_user.id

        # Рассчитываем итоговую сумму
        calculation = await calculate_order_total_with_bonuses(
            user_id,
            data.get('delivery_cost', 0),
            data.get('bonus_used', 0)
        )

        total_amount = calculation['final_total']

        # Сохраняем итоговую сумму
        await state.update_data(
            payment_amount=total_amount,
            original_products_total=calculation['original_products_total'],
            discount=calculation['discount']
        )

        # Обрабатываем разные способы оплаты
        if payment_method in ['online', 'sbp']:
            await process_online_payment_selection(callback, state)
        elif payment_method == 'cash':
            await process_cash_payment(callback, state)
        elif payment_method == 'cert':
            await process_certificate_payment(callback, state)
        elif payment_method == 'manager':
            await process_manager_payment(callback, state)

    except Exception as e:
        logger.error(f"Ошибка при выборе способа оплаты: {e}")
        await callback.message.answer(
            "❌ Произошла ошибка при обработке оплаты. Попробуйте еще раз или свяжитесь с менеджером."
        )
    finally:
        await callback.answer()


@router.callback_query(F.data == "pay_manager")
async def process_online_payment(message: Message, state: FSMContext):
    """Обработка онлайн-оплаты с учетом бонусов"""
    data = await state.get_data()
    user_id = message.from_user.id

    # Получаем актуальную корзину и пересчитываем с учетом бонусов
    calculation = await calculate_order_total_with_bonuses(user_id, data.get('delivery_cost', 0))
    bonus_used = data.get('bonus_used', 0)
    total = calculation['final_total']

    # Сохраняем итоговую сумму в state
    await state.update_data(payment_amount=total, bonus_used=bonus_used)


@router.callback_query(F.data.in_(["pay_online", "pay_sbp"]))
async def process_online_payment_selection(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора онлайн-оплаты (карта или СБП)"""
    try:
        data = await state.get_data()
        payment_method = data.get('payment_method', 'online')
        user_id = callback.from_user.id

        # Рассчитываем итоговую сумму
        calculation = await calculate_order_total_with_bonuses(
            user_id,
            data.get('delivery_cost', 0),
            data.get('bonus_used', 0)
        )

        total_amount = calculation['final_total']

        user_phone = data.get('phone', '')
        if not user_phone:
            user_phone = "9999999999"

        # Создаем платеж в ЮKassa
        cart_items = get_cart(user_id)

        # Формируем метаданные для платежа
        metadata = {
            "user_id": user_id,
            "name": data.get('name', ''),
            "phone": user_phone,
            "address": data.get('address', ''),
            "delivery_date": data.get('delivery_date', ''),
            "delivery_time": data.get('delivery_time', ''),
            "payment_method": payment_method,
            "delivery_type": data.get('delivery_type', 'delivery'),
            "delivery_cost": data.get('delivery_cost', 0),
            "bonus_used": data.get('bonus_used', 0),
            "cart_items": cart_items,
            "type": "order"
        }

        # Упрощаем метаданные для YooKassa
        simplified_metadata = simplify_order_data(metadata)

        # Создаем платеж
        payment_description = f"Заказ цветов на {total_amount}₽"

        payment = await payment_manager.create_payment(
            amount=total_amount,
            description=payment_description,
            metadata=simplified_metadata
        )

        if payment and payment.get("confirmation_url"):
            await state.update_data(
                payment_id=payment["id"],
                payment_url=payment["confirmation_url"]
            )
            await state.set_state(OrderState.waiting_payment)

            # Формируем клавиатуру с кнопкой оплаты
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 Перейти к оплате", url=payment["confirmation_url"])],
                [InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"check_payment_{payment['id']}")]
            ])

            payment_method_name = "банковской картой" if payment_method == "online" else "через СБП"

            await callback.message.answer(
                f"💳 <b>Оплата {payment_method_name}</b>\n\n"
                f"💰 Сумма к оплате: {total_amount} ₽\n"
                f"🔗 Перейдите по ссылке для завершения оплаты\n\n"
                f"После успешной оплаты нажмите «✅ Проверить оплату»",
                reply_markup=kb,
                parse_mode="HTML"
            )
        else:
            await callback.message.answer(
                "❌ Не удалось создать платеж. Пожалуйста, попробуйте другой способ оплаты или свяжитесь с менеджером."
            )

    except Exception as e:
        logger.error(f"Ошибка в process_online_payment_selection: {e}")
        await callback.message.answer(
            "❌ Произошла ошибка при создании платежа. Попробуйте другой способ оплаты."
        )


@router.callback_query(F.data.startswith("check_payment_"))
async def check_payment_status(callback: CallbackQuery, state: FSMContext):
    """Проверка статуса платежа"""
    try:
        payment_id = callback.data.split("_")[2]

        # Проверяем статус через payment_manager
        status = await payment_manager.check_payment_status(payment_id)

        if status == 'succeeded':
            # Платеж успешен - создаем заказ
            data = await state.get_data()
            user_id = callback.from_user.id

            # Создаем заказ
            order_id = create_order(
                user_id=user_id,
                name=data.get('name', ''),
                phone=data.get('phone', ''),
                address=data.get('address', ''),
                delivery_date=data.get('delivery_date', ''),
                delivery_time=data.get('delivery_time', ''),
                payment=data.get('payment_method', 'online'),
                delivery_cost=data.get('delivery_cost', 0),
                delivery_type=data.get('delivery_type', 'delivery'),
                bonus_used=data.get('bonus_used', 0)
            )

            if order_id != -1:
                await callback.message.answer(
                    f"✅ <b>Оплата принята!</b>\n\n"
                    f"Заказ #{order_id} успешно оформлен.\n"
                    f"Менеджер свяжется с вами для подтверждения.",
                    parse_mode="HTML"
                )

                # Отправляем уведомление администраторам
                await notify_admins_about_new_order(order_id, user_id, data)

                await state.clear()
            else:
                await callback.message.answer(
                    "❌ Ошибка при создании заказа. Пожалуйста, свяжитесь с менеджером."
                )

        elif status == 'pending':
            await callback.answer("⏳ Платеж еще обрабатывается. Попробуйте через минуту.")
        else:
            await callback.answer("❌ Платеж не прошел. Попробуйте еще раз или выберите другой способ оплаты.")

    except Exception as e:
        logger.error(f"Ошибка при проверке платежа: {e}")
        await callback.answer("❌ Ошибка при проверке статуса платежа.")


async def notify_admins_about_new_order(order_id: int, user_id: int, order_data: dict):
    """Уведомление администраторов о новом заказе"""
    # Получаем заказ из базы, чтобы взять список товаров
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT items FROM orders WHERE id = ?", (order_id,))
        result = cur.fetchone()

    if not result:
        logger.error(f"Заказ #{order_id} не найден при отправке уведомления")
        return

    try:
        cart_items = json.loads(result['items'])  # Это сохранённый список товаров
    except Exception as e:
        logger.error(f"Ошибка парсинга items для заказа #{order_id}: {e}")
        cart_items = []

    message = (f"🛒 <b>НОВЫЙ ЗАКАЗ #{order_id}</b>\n"
               f"👤 <b>Клиент:</b> {order_data.get('name', 'Не указано')}\n"
               f"📞 <b>Телефон:</b> {order_data.get('phone', 'Не указан')}\n"
               f"📍 <b>Адрес:</b> {order_data.get('address', 'Не указан')}\n"
               f"📅 <b>Дата доставки:</b> {order_data.get('delivery_date', 'Не указана')}\n"
               f"⏰ <b>Время:</b> {order_data.get('delivery_time', 'Не указано')}\n"
               f"💳 <b>Способ оплаты:</b> {get_payment_method_name(order_data.get('payment_method', ''))}\n"
               f"💰 <b>Сумма:</b> {order_data.get('payment_amount', 0)} ₽\n"
               f"🛒 <b>Товары:</b>\n")

    if cart_items:
        for item in cart_items:
            name = item.get('name', 'Неизвестный товар')
            price = item.get('price', 0)
            quantity = item.get('quantity', 1)
            total_item = price * quantity
            message += f"• {name} ×{quantity} — {total_item} ₽\n"
    else:
        message += "❌ Товары не найдены в заказе.\n"

    if order_data.get('bonus_used', 0) > 0:
        message += f"💎 <b>Использовано бонусов:</b> {order_data.get('bonus_used', 0)} ₽\n"

    await notify_admins(message)


@router.callback_query(F.data.in_(["pay_online", "pay_sbp", "pay_cash"]))
async def process_payment_with_bonus_option(callback: CallbackQuery, state: FSMContext):
    """Предлагаем использовать бонусы перед оплатой"""
    payment_method = callback.data.split("_")[1]
    await state.update_data(payment_method=payment_method)

    # Рассчитываем доступные бонусы
    user_id = callback.from_user.id
    calculation = await calculate_order_total_with_bonuses(user_id)

    if calculation['available_bonus'] > 0 and calculation['max_bonus_allowed'] > 0:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"💎 Использовать бонусы (до {calculation['max_bonus_allowed']}₽)",
                callback_data="use_bonus_yes"
            )],
            [InlineKeyboardButton(text="💳 Без бонусов", callback_data="use_bonus_no")]
        ])

        await callback.message.answer(
            f"💎 <b>У вас есть {calculation['available_bonus']}₽ бонусов</b>\n"
            f"Можно использовать: до {calculation['max_bonus_allowed']}₽ (30% от заказа)\n\n"
            "Хотите использовать бонусы для оплаты?",
            reply_markup=kb,
            parse_mode="HTML"
        )
        await state.set_state(OrderState.use_bonus)
    else:
        # Если бонусов нет, переходим к обычной оплате
        await process_payment_method(callback, state)

    await callback.answer()


@router.callback_query(F.data == "use_bonus_yes", OrderState.use_bonus)
async def use_bonus_yes_handler(callback: CallbackQuery, state: FSMContext):
    """Пользователь хочет использовать бонусы"""
    user_id = callback.from_user.id
    calculation = await calculate_order_total_with_bonuses(user_id)

    await callback.message.answer(
        f"💎 <b>Введите сумму бонусов для использования</b>\n\n"
        f"Доступно: {calculation['available_bonus']}₽\n"
        f"Максимум можно использовать: {calculation['max_bonus_allowed']}₽\n\n"
        f"Пример: 500 (для использования 500₽ бонусов)",
        parse_mode="HTML"
    )
    await state.set_state(OrderState.bonus_amount)
    await callback.answer()


@router.callback_query(F.data == "use_bonus_no")
async def use_bonus_no_handler(callback: CallbackQuery, state: FSMContext):
    """Пользователь не хочет использовать бонусы"""
    await state.update_data(bonus_used=0)
    await callback.message.answer("💳 Продолжаем оплату без использования бонусов")
    await process_payment_method(callback, state)
    await callback.answer()


@router.message(OrderState.bonus_amount)
async def process_bonus_amount(message: Message, state: FSMContext):
    """Обработка ввода суммы бонусов с правильным расчетом"""
    try:
        bonus_to_use = int(message.text)
        user_id = message.from_user.id

        # Получаем данные корзины
        cart_items = get_cart(user_id)
        products_total = sum(item['price'] * item['quantity'] for item in cart_items)

        # Проверяем лимиты
        max_bonus_allowed = int(products_total * 0.3)
        bonus_info = get_bonus_info(user_id)

        if bonus_to_use <= 0:
            await message.answer("❌ Сумма должна быть положительной. Введите еще раз:")
            return

        if bonus_to_use > bonus_info['current_bonus']:
            await message.answer(
                f"❌ Недостаточно бонусов. Доступно: {bonus_info['current_bonus']}₽\n"
                f"Введите сумму еще раз:"
            )
            return

        if bonus_to_use > max_bonus_allowed:
            await message.answer(
                f"❌ Можно использовать не более {max_bonus_allowed}₽ (30% от заказа)\n"
                f"Введите сумму еще раз:"
            )
            return

        # Сохраняем сумму бонусов и пересчитываем итоговую сумму
        data = await state.get_data()
        delivery_type = data.get('delivery_type', 'delivery')
        delivery_cost = data.get('delivery_cost', 0)  # Стандартная стоимость доставки
        final_total = products_total - bonus_to_use + delivery_cost

        await state.update_data(
            bonus_used=bonus_to_use,
            products_total=products_total,
            delivery_cost=delivery_cost,
            final_total=final_total
        )

        await message.answer(
            f"✅ Будет использовано {bonus_to_use}₽ бонусов\n"
            f"💰 Итоговая сумма к оплате: {final_total}₽\n\n"
            f"👤 Теперь введите ваше имя:"
        )
        await state.set_state(OrderState.name)

    except ValueError:
        await message.answer("❌ Пожалуйста, введите число. Например: 500")


@router.callback_query(F.data == "pay_cash")
async def process_cash_payment(callback: CallbackQuery, state: FSMContext):
    """Обработка оплаты наличными"""
    await state.update_data(payment_method='cash')

    data = await state.get_data()
    cart_items = get_cart(callback.from_user.id)
    products_total = sum(item['price'] * item['quantity'] for item in cart_items)

    # Для самовывоза доставка бесплатна
    delivery_type = data.get('delivery_type', 'delivery')
    delivery_cost = data.get('delivery_cost', 0)

    total = products_total + delivery_cost

    # Сохраняем стоимость доставки в state
    await state.update_data(delivery_cost=delivery_cost, payment_amount=total)

    await show_order_summary(callback, state, total)
    await callback.answer()


@router.callback_query(F.data == "pay_cert")
async def process_certificate_payment(callback: CallbackQuery, state: FSMContext):
    """Обработка оплаты сертификатом"""
    await callback.message.answer(
        "🎁 <b>Оплата подарочным сертификатом</b>\n\n"
        "Введите код вашего сертификата:",
        parse_mode="HTML"
    )
    await state.set_state(OrderState.certificate_code)
    await callback.answer()


@router.message(OrderState.certificate_code)
async def process_certificate_code(message: Message, state: FSMContext):
    """Проверка и применение сертификата с защитой от перебора"""
    user_id = message.from_user.id
    cert_code = message.text.strip().upper()

    # Проверяем, не заблокирован ли пользователь
    attempts_info = get_certificate_attempts(user_id)
    if attempts_info and attempts_info.get('blocked_until'):
        blocked_until = datetime.strptime(attempts_info['blocked_until'], "%Y-%m-%d %H:%M:%S")
        if datetime.now() < blocked_until:
            time_left = blocked_until - datetime.now()
            minutes_left = int(time_left.total_seconds() // 60)
            await message.answer(
                f"❌ Слишком много неверных попыток. "
                f"Попробуйте через {minutes_left} минут."
            )
            return

    # Проверяем валидность сертификата
    certificate = check_certificate_validity(cert_code)

    if certificate:
        # Сбрасываем счетчик попыток при успешном вводе
        reset_certificate_attempts(user_id)

        data = await state.get_data()
        cart_items = get_cart(user_id)
        total = sum(item['price'] * item['quantity'] for item in cart_items) + data.get('delivery_cost', 0)

        if certificate['amount'] >= total:
            # Сертификат покрывает всю сумму
            await state.update_data(
                payment_method='certificate',
                certificate_code=cert_code,
                certificate_amount=certificate['amount']
            )

            await message.answer(
                f"✅ Сертификат принят! Номинал: {certificate['amount']} ₽\n"
                f"Сумма заказа: {total} ₽\n"
                f"Остаток на сертификате: {certificate['amount'] - total} ₽"
            )

            # Переходим к подтверждению заказа
            await show_order_summary_from_message(message, state, total)

        else:
            await message.answer(
                f"❌ Недостаточно средств на сертификате.\n"
                f"Номинал: {certificate['amount']} ₽\n"
                f"Сумма заказа: {total} ₽\n\n"
                f"Выберите другой способ оплаты для разницы: {total - certificate['amount']} ₽"
            )
    else:
        # Неверный код - увеличиваем счетчик попыток
        add_certificate_attempt(user_id)
        attempts_info = get_certificate_attempts(user_id)

        if attempts_info['attempts'] >= 3:
            await message.answer(
                "❌ Слишком много неверных попыток. "
                "Вы заблокированы на 30 минут."
            )
        else:
            remaining_attempts = 3 - attempts_info['attempts']
            await message.answer(
                f"❌ Недействительный сертификат. "
                f"Осталось попыток: {remaining_attempts}\n"
                f"Проверьте код или свяжитесь с менеджером."
            )


@router.message(OrderState.certificate_code)
async def process_certificate_code(message: Message, state: FSMContext):
    """Проверка и применение сертификата"""
    cert_code = message.text.strip().upper()

    # Проверяем валидность сертификата
    certificate = check_certificate_validity(cert_code)

    if certificate:
        data = await state.get_data()
        cart_items = get_cart(message.from_user.id)
        total = sum(item['price'] * item['quantity'] for item in cart_items) + data.get('delivery_cost', 0)

        if certificate['amount'] >= total:
            # Сертификат покрывает всю сумму
            await state.update_data(
                payment_method='certificate',
                certificate_code=cert_code,
                certificate_amount=certificate['amount']
            )

            await message.answer(
                f"✅ Сертификат принят! Номинал: {certificate['amount']} ₽\n"
                f"Сумма заказа: {total} ₽\n"
                f"Остаток на сертификате: {certificate['amount'] - total} ₽"
            )

            # Переходим к подтверждению заказа
            await show_order_summary_from_message(message, state, total)

        else:
            await message.answer(
                f"❌ Недостаточно средств на сертификате.\n"
                f"Номинал: {certificate['amount']} ₽\n"
                f"Сумма заказа: {total} ₽\n\n"
                f"Выберите другой способ оплаты для разницы: {total - certificate['amount']} ₽"
            )
    else:
        await message.answer(
            "❌ Недействительный сертификат. Проверьте код или свяжитесь с менеджером."
        )


async def show_order_summary(callback: CallbackQuery, state: FSMContext, total: float):
    data = await state.get_data()
    bonus_used = data.get('bonus_used', 0)
    original_products_total = data.get('original_products_total', total + bonus_used)  # Восстанавливаем исходную сумму
    discount = data.get('discount', 0)
    is_first_order = data.get('is_first_order', False)

    # Добавляем информацию о скидке
    discount_text = f"🎉 <b>Скидка 10% на первый заказ:</b> -{discount} ₽\n" if discount > 0 else ""

    delivery_type = data.get('delivery_type', 'delivery')
    delivery_type_text = "Самовывоз" if delivery_type == "pickup" else "Доставка"

    address = data.get('address', '')
    address_text = f"🏠 <b>Адрес:</b> {address}\n" if delivery_type == "delivery" and address else ""

    # Добавляем информацию о бонусах
    bonus_text = f"💎 <b>Использовано бонусов:</b> {bonus_used} ₽\n" if bonus_used > 0 else ""

    order_summary = (
        "📋 <b>Сводка заказа</b>\n\n"
        f"👤 <b>Имя:</b> {data.get('name', 'Не указано')}\n"
        f"📞 <b>Телефон:</b> {data.get('phone', 'Не указан')}\n"
        f"📍 <b>Способ:</b> {delivery_type_text}\n"
        f"{address_text}"
        f"📅 <b>Дата:</b> {data.get('delivery_date', 'Не указана')}\n"
        f"⏰ <b>Время:</b> {data.get('delivery_time', 'Не указано')}\n"
        f"💳 <b>Оплата:</b> {get_payment_method_name(data.get('payment_method', ''))}\n"
        f"💰 <b>Сумма товаров:</b> {original_products_total} ₽\n"
        f"{discount_text}"
        f"{bonus_text}"
        f"💰 <b>Итого к оплате:</b> {total} ₽\n\n"
        "✅ Все верно? Подтвердите заказ:"
    )

    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить заказ", callback_data="confirm_order")],
        [InlineKeyboardButton(text="✏️ Изменить данные", callback_data="cancel_order")]
    ])

    await callback.message.answer(order_summary, reply_markup=confirm_kb, parse_mode="HTML")


@router.callback_query(F.data == "pay_manager")
async def process_manager_payment(callback: CallbackQuery, state: FSMContext):
    """Обработка оплаты через менеджера"""
    try:
        await state.update_data(payment_method='manager')
        data = await state.get_data()

        # Получаем корзину
        cart_items = get_cart(callback.from_user.id)
        if not cart_items:
            await callback.answer("❌ Корзина пуста")
            return

        # ПРАВИЛЬНЫЙ РАСЧЕТ С УЧЕТОМ БОНУСОВ
        products_total = sum(item['price'] * item['quantity'] for item in cart_items)
        delivery_type = data.get('delivery_type', 'delivery')
        delivery_cost = 0 if delivery_type == "pickup" else 300
        bonus_used = data.get('bonus_used', 0)  # ← ПОЛУЧАЕМ ИСПОЛЬЗОВАННЫЕ БОНУСЫ

        # Правильный расчет итоговой суммы
        total = max(0, products_total - bonus_used + delivery_cost)

        # Создаем заказ
        order_id = create_order(
            user_id=callback.from_user.id,
            name=data.get('name', ''),
            phone=data.get('phone', ''),
            address=data.get('address', ''),
            delivery_date=data.get('delivery_date', ''),
            delivery_time=data.get('delivery_time', ''),
            payment='manager',
            delivery_cost=delivery_cost,
            delivery_type=delivery_type,
            bonus_used=bonus_used  # ← ПЕРЕДАЕМ ИСПОЛЬЗОВАННЫЕ БОНУСЫ
        )

        # Отправляем уведомление менеджеру
        delivery_type_text = "Самовывоз" if delivery_type == "pickup" else "Доставка"
        admin_msg = (
            "👤 <b>НОВЫЙ ЗАКАЗ ЧЕРЕЗ МЕНЕДЖЕРА</b>\n\n"
            f"📦 Заказ #: {order_id}\n"
            f"👤 Имя: {data.get('name', 'Не указано')}\n"
            f"📞 Телефон: {data.get('phone', 'Не указан')}\n"
            f"📍 Способ: {delivery_type_text}\n"
        )

        if delivery_type == "delivery":
            admin_msg += f"🏠 Адрес: {data.get('address', 'Не указан')}\n"

        # ДОБАВЛЯЕМ ИНФОРМАЦИЮ О БОНУСАХ В УВЕДОМЛЕНИЕ
        if bonus_used > 0:
            admin_msg += f"💎 Использовано бонусов: {bonus_used} ₽\n"

        admin_msg += (
            f"📅 Дата: {data.get('delivery_date', 'Не указана')}\n"
            f"⏰ Время: {data.get('delivery_time', 'Не указано')}\n"
            f"💰 Сумма: {total} ₽\n\n"  # ← ТЕПЕРЬ ПРАВИЛЬНАЯ СУММА
            f"🛒 Товары:\n"
        )

        for item in cart_items:
            admin_msg += f"• {item['name']} ×{item['quantity']} - {item['price'] * item['quantity']} ₽\n"

        await notify_admins(admin_msg)

        await callback.message.answer(
            f"✅ <b>Заказ #{order_id} оформлен!</b>\n\n"
            f"📞 Менеджер свяжется с вами для подтверждения.\n"
            f"💰 Сумма: {total} ₽\n"
            f"📅 {'Получение' if delivery_type == 'pickup' else 'Доставка'}: "
            f"{data.get('delivery_date', '')} в {data.get('delivery_time', '')}",
            parse_mode="HTML"
        )

        await state.clear()

    except Exception as e:
        logger.error(f"Error in process_manager_payment: {e}")
        await callback.message.answer(
            "❌ Произошла ошибка. Пожалуйста, свяжитесь с менеджером."
        )

    await callback.answer()


@router.callback_query(F.data == "check_payment_status")
async def check_user_payment_status(callback: CallbackQuery, state: FSMContext):
    """Проверка статуса платежа пользователем"""
    data = await state.get_data()
    payment_id = data.get('payment_id')

    if not payment_id:
        await callback.answer("❌ Данные о платеже не найдены")
        return

    # Проверяем статус платежа
    status = await payment_manager.check_payment_status(payment_id)

    if status == 'succeeded':
        # Обновляем статус в базе
        update_payment_status(payment_id, status)

        # Получаем полные данные заказа из базы
        payment_info = get_payment(payment_id)
        if payment_info and payment_info.get('metadata'):
            try:
                metadata = json.loads(payment_info['metadata'])
                order_data = metadata.get('order_data', {})
            except:
                order_data = data
        else:
            order_data = data

        # Создаем заказ
        order_id = create_order(
            callback.from_user.id,
            order_data.get('name', data.get('name', '')),
            order_data.get('phone', data.get('phone', '')),
            order_data.get('address', data.get('address', '')),
            order_data.get('delivery_date', data.get('delivery_date', '')),
            order_data.get('delivery_time', data.get('delivery_time', '')),
            order_data.get('payment_method', data.get('payment_method', 'online')),
            order_data.get('delivery_cost', data.get('delivery_cost', 0))
        )

        await callback.message.answer(
            f"✅ <b>Оплата принята!</b>\n\n"
            f"Заказ #{order_id} успешно оформлен.\n"
            f"Менеджер свяжется с вами для подтверждения в течение 15 минут.",
            parse_mode="HTML"
        )

        await state.clear()

    elif status == 'pending':
        await callback.answer("⏳ Платеж еще обрабатывается. Попробуйте через минуту.")

    elif status == 'canceled':
        await callback.answer("❌ Платеж отменен. Попробуйте еще раз.")

    else:
        await callback.answer("❌ Не удалось проверить статус платежа. Попробуйте позже.")


# Фоновая задача для проверки pending платежей
async def check_pending_payments():
    """Фоновая проверка pending платежей каждые 5 минут"""
    while True:
        try:
            # Здесь будет логика проверки pending платежей
            # Например: поиск платежей со статусом 'pending' старше 10 минут
            logger.info("Checking pending payments...")
            await asyncio.sleep(300)  # Проверяем каждые 5 минут

        except Exception as e:
            logger.error(f"Pending payments check failed: {e}")
            await asyncio.sleep(60)  # Ждем минуту при ошибке


async def show_order_summary_from_message(callback: CallbackQuery, state: FSMContext, total: float):
    """Показывает сводку заказа и кнопку подтверждения"""
    data = await state.get_data()

    delivery_type = data.get('delivery_type', 'delivery')
    delivery_type_text = "Самовывоз" if delivery_type == "pickup" else "Доставка"

    # Безопасное получение адреса
    address = data.get('address', '')
    address_text = f"🏠 <b>Адрес:</b> {address}\n" if delivery_type == "delivery" and address else ""

    order_summary = (
        "📋 <b>Сводка заказа</b>\n\n"
        f"👤 <b>Имя:</b> {data.get('name', 'Не указано')}\n"
        f"📞 <b>Телефон:</b> {data.get('phone', 'Не указан')}\n"
        f"📍 <b>Способ:</b> {delivery_type_text}\n"
        f"{address_text}"
        f"📅 <b>Дата:</b> {data.get('delivery_date', 'Не указана')}\n"
        f"⏰ <b>Время:</b> {data.get('delivery_time', 'Не указано')}\n"
        f"💳 <b>Оплата:</b> {get_payment_method_name(data.get('payment_method', ''))}\n\n"
        f"💰 <b>Итого: {total} ₽</b>\n\n"
        "✅ Все верно? Подтвердите заказ:"
    )

    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить заказ", callback_data="confirm_order")],
        [InlineKeyboardButton(text="✏️ Изменить данные", callback_data="cancel_order")]
    ])

    await callback.message.answer(order_summary, reply_markup=confirm_kb, parse_mode="HTML")


@router.callback_query(F.data == "confirm_order")
async def confirm_order(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    user_id = callback.from_user.id

    # Проверяем доступность бонусов
    bonus_used = data.get('bonus_used', 0)
    if bonus_used > 0:
        cart_items = get_cart(user_id)
        check = can_use_bonus(user_id, bonus_used, cart_items)

        if not check['can_use'] or check['actual_usable'] < bonus_used:
            await callback.message.answer(
                f"❌ Недостаточно бонусов для использования\n"
                f"Доступно: {check['available_bonus']} ₽\n"
                f"Можно использовать: {check['max_allowed']} ₽"
            )
            await callback.answer()
            return

    # Создаем заказ
    order_id = create_order(
        user_id,
        data['name'],
        data['phone'],
        data['address'],
        data['delivery_date'],
        data['delivery_time'],
        data['payment_method'],
        data.get('delivery_cost', 0),
        data.get('delivery_type', 'delivery'),
        bonus_used
    )

    if order_id == -1:
        await callback.message.answer(
            "❌ Ошибка при создании заказа. Недостаточно бонусов."
        )
        await callback.answer()
        return

    # ДОПОЛНИТЕЛЬНАЯ ПРОВЕРКА - УБЕДИМСЯ ЧТО КОРЗИНА ОЧИЩЕНА
    cart_after_order = get_cart(user_id)
    if cart_after_order:
        # Если корзина не очистилась, очищаем вручную
        clear_cart(user_id)
        print(f"⚠️ Корзина не очистилась автоматически для пользователя {user_id}, очищаем вручную")

    # Получаем информацию о начисленных бонусах
    bonus_info = get_bonus_info(user_id)

    await callback.message.answer(
        f"🎉 Заказ #{order_id} оформлен!\n"
        f"💎 Использовано бонусов: {bonus_used} ₽\n"
        f"💎 Начислено бонусов: {bonus_info['current_bonus']} ₽\n"
        f"💰 Итоговая сумма: {data.get('final_total', 0)} ₽"
    )

    await state.clear()
    await callback.answer()


# --- YOOKASSA ---
@router.callback_query(F.data == "pay_yookassa")
async def create_yookassa_payment(callback: CallbackQuery, state: FSMContext):
    if Payment is None:
        await callback.message.answer("❌ Платежная система временно недоступна")
        await callback.answer()
        return

    data = await state.get_data()
    user_id = callback.from_user.id
    cart = get_cart(user_id)
    total = sum(item['price'] * item['quantity'] for item in cart)
    payment_id = str(uuid.uuid4())

    try:
        payment = Payment.create({
            "amount": {"value": str(total), "currency": "RUB"},
            "confirmation": {
                "type": "redirect",
                "return_url": "https://t.me/Therry_Voyager"
            },
            "capture": True,
            "description": f"Заказ #{payment_id}",
            "metadata": {"user_id": user_id, "order_id": payment_id}
        }, idempotency_key=payment_id)

        confirmation_url = payment.confirmation.confirmation_url
        await state.update_data(payment_id=payment_id, payment_system="YooKassa")

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить", url=confirmation_url)],
            [InlineKeyboardButton(text="✅ Я оплатил", callback_data="check_payment")]
        ])
        await callback.message.answer("🔗 Перейдите по ссылке для оплаты:", reply_markup=kb)
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка оплаты: {e}")
    await callback.answer()


@router.callback_query(F.data == "check_payment")
async def check_payment(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    method = data.get("payment_system", "не указано")
    order_id = data.get("payment_id", "неизвестен")
    user_data = data

    # Создаем заказ
    order_id_db = create_order(
        callback.from_user.id,
        user_data.get('name', ''),
        user_data.get('phone', ''),
        user_data.get('address', ''),
        user_data.get('delivery_date', ''),
        user_data.get('delivery_time', ''),
        method,
        user_data.get('delivery_cost', 0)
    )

    await callback.message.answer(
        f"✅ Оплата принята!\n"
        f"Система: {method}\n"
        f"ID заказа: #{order_id_db}\n"
        "Менеджер свяжется с вами для подтверждения."
    )
    await state.clear()
    await callback.answer()


# --- МОИ ЗАКАЗЫ ---
@router.message(F.text == "🧾 Мои заказы")
async def my_orders(message: Message):
    orders = get_user_orders(message.from_user.id)
    if not orders:
        await message.answer("У вас пока нет заказов.")
        return

    for o in orders:
        items_data = json.loads(o['items'])
        items = ", ".join([f"{item['name']} (×{item['quantity']})" for item in items_data])
        created_at = o['created_at'].split(".")[0].replace("T", " ")

        text = (
            f"📦 <b>Заказ #{o['id']}</b>\n"
            f"📅 {created_at}\n"
            f"💰 {o['total']} ₽\n"
            f"🛒 {items}\n"
            f"📊 Статус: {o['status']}"
        )

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Повторить", callback_data=f"repeat_{o['id']}")],
            [InlineKeyboardButton(text="📦 Отслеживание", callback_data=f"track_{o['id']}")]
        ])
        await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("repeat_"))
async def repeat_order(callback: CallbackQuery):
    order_id = int(callback.data.split("_")[1])
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT items FROM orders WHERE id=? AND user_id=?", (order_id, callback.from_user.id))
        row = cur.fetchone()

    if not row:
        await callback.answer("❌ Заказ не найден")
        return

    items = json.loads(row['items'])
    clear_cart(callback.from_user.id)

    for item in items:
        for _ in range(item['quantity']):
            add_to_cart(callback.from_user.id, item['id'])

    await callback.answer("✅ Товары добавлены в корзину!")
    await show_cart(callback.message)


@router.callback_query(F.data.startswith("track_"))
async def track_order(callback: CallbackQuery):
    order_id = callback.data.split("_")[1]

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT status, delivery_date, delivery_time FROM orders WHERE id=? AND user_id=?",
                    (order_id, callback.from_user.id))
        order = cur.fetchone()

    if not order:
        await callback.answer("Заказ не найден")
        return

    status_text = {
        "new": "🕒 Заказ принят",
        "processing": "📦 Формируется",
        "on_way": "🚚 В пути",
        "delivered": "✅ Доставлен",
        "cancelled": "❌ Отменён"
    }.get(order['status'], "📊 Статус неизвестен")

    await callback.message.answer(
        f"📦 <b>Отслеживание заказа #{order_id}</b>\n\n"
        f"📌 Статус: {status_text}\n"
        f"📅 Дата доставки: {order['delivery_date']}\n"
        f"⏰ Время: {order['delivery_time']}\n\n"
        "📞 Для уточнения свяжитесь с менеджером",
        parse_mode="HTML"
    )
    await callback.answer()


# --- СИСТЕМА ЛОЯЛЬНОСТИ ---
@router.callback_query(F.data == "my_bonus")
@router.message(F.text == "💎 Мои бонусы")
async def show_bonus_info(event: Union[CallbackQuery, Message]):
    user_id = event.from_user.id

    # Определяем, откуда пришёл запрос
    is_callback = isinstance(event, CallbackQuery)
    message = event.message if is_callback else event

    """Показывает информацию о бонусах"""
    bonus_info = get_bonus_info(user_id)

    text = (
        f"💎 <b>Ваша бонусная программа</b>\n\n"
        f"💰 Всего потрачено: {bonus_info['total_spent']} ₽\n"
        f"🎁 Доступно бонусов: {bonus_info['current_bonus']} ₽\n"
        f"🏆 Всего начислено: {bonus_info['total_bonus_earned']} ₽\n\n"
        "💎 <b>Как работает бонусная система?</b>\n\n"

        "🎁 <b>Начисление бонусов:</b>\n"
        "• За каждый заказ начисляем <b>5% от суммы</b> в бонусах\n"
        "• Например: заказ на 2000 ₽ = 100 бонусов (2000 × 5%)\n"
        "• Бонусы начисляются после подтверждения заказа\n\n"

        "💰 <b>Использование бонусов:</b>\n"
        "• 1 бонус = 1 рубль скидки\n"
        "• Можно оплатить <b>до 30% стоимости</b> следующего заказа\n"
        "• Например: заказ на 3000 ₽ → макс. 900 бонусов (30%)\n\n"

        "🎉 <b>Специальное предложение:</b>\n"
        "• <b>10% скидка на первый заказ!</b> (действует автоматически)\n\n"

        "📋 <b>Пример расчета:</b>\n"
        "• Сумма заказа: 5000 ₽\n"
        "• Макс. бонусов к списанию: 1500 ₽ (30%)\n"
        "• Если у вас 2000 бонусов → используете 1500 ₽\n"
        "• Итог к оплате: 3500 ₽ (5000 - 1500)\n"
        "• + начислится 175 бонусов (5% от 3500 ₽)\n\n"

        "⭐ <b>Преимущества:</b>\n"
        "• Бонусы не сгорают\n"
        "• Накопления видны сразу после заказа\n"
        "• Можно использовать частично\n"
        "• Действуют на все товары\n\n"

        "💡 <i>Бонусы начисляются только при успешной оплате заказа!</i>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 История начислений", callback_data="bonus_history")]
    ])

    if is_callback:
        await message.answer(text, parse_mode="HTML", reply_markup=kb)
        await event.answer()
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "bonus_history")
async def show_bonus_history(callback: CallbackQuery):
    """Показывает историю операций с бонусами"""
    history = get_loyalty_history(callback.from_user.id, 10)
    if not history:
        await callback.message.answer("📊 История операций с бонусами пуста")
        await callback.answer()
        return

    text = "📊 <b>История операций с бонусами:</b>\n\n"
    for operation in history:
        change = operation['points_change']
        sign = "➕" if change > 0 else "➖"
        date = operation['created_at'].split(".")[0] if isinstance(operation['created_at'], str) else \
            str(operation['created_at']).split(".")[0]
        date = date.replace("T", " ")

        text += (
            f"{sign} <b>{abs(change)} ₽</b>\n"
            f"📝 {operation['reason']}\n"
            f"📅 {date}\n"
            f"💎 Остаток: {operation['remaining_points']} ₽\n\n"
        )

    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.in_(["pay_online", "pay_sbp", "pay_cash"]))
async def process_payment_with_bonus(callback: CallbackQuery, state: FSMContext):
    """Предлагаем использовать бонусы перед оплатой"""
    bonus_info = get_bonus_info(callback.from_user.id)

    if bonus_info['current_bonus'] > 0:
        # Получаем данные корзины для расчета максимума
        cart_items = get_cart(callback.from_user.id)
        products_total = sum(item['price'] * item['quantity'] for item in cart_items)
        max_bonus_allowed = int(products_total * 0.3)
        available_bonus = min(bonus_info['current_bonus'], max_bonus_allowed)

        if available_bonus > 0:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💎 Использовать бонусы", callback_data="use_bonus_points")],
                [InlineKeyboardButton(text="💳 Оплатить без бонусов", callback_data="skip_bonus_points")]
            ])

            await callback.message.answer(
                f"💎 У вас есть {bonus_info['current_bonus']} бонусов\n"
                f"Можно использовать: {available_bonus} ₽ (30% от стоимости товаров)\n"
                f"Сумма товаров: {products_total} ₽ × 30% = {max_bonus_allowed} ₽\n\n"
                "Хотите использовать бонусы для оплаты?",
                reply_markup=kb
            )
            await state.set_state(OrderState.use_bonus)
            return

    # Если бонусов нет или нельзя использовать, переходим к обычной оплате
    await process_online_payment(callback, state)


@router.callback_query(F.data == "skip_bonus_points", OrderState.use_bonus)
async def skip_bonus_usage(callback: CallbackQuery, state: FSMContext):
    """Пропуск использования бонусов"""
    await state.update_data(bonus_used=0)
    await callback.message.answer("💳 Продолжаем оплату без использования бонусов")

    # Продолжаем стандартный процесс оплаты
    data = await state.get_data()
    payment_method = data.get('payment_method', 'online')
    if payment_method in ['online', 'sbp']:
        await process_online_payment(callback, state)
    else:
        await process_cash_payment(callback, state)
    await callback.answer()


@router.callback_query(F.data == "use_bonus_points", OrderState.use_bonus)
async def ask_bonus_amount(callback: CallbackQuery, state: FSMContext):
    """Запрашиваем количество бонусов для использования"""
    bonus_info = get_bonus_info(callback.from_user.id)
    cart_items = get_cart(callback.from_user.id)
    products_total = sum(item['price'] * item['quantity'] for item in cart_items)
    max_bonus_allowed = int(products_total * 0.3)

    available_bonus = min(bonus_info['current_bonus'], max_bonus_allowed)

    await callback.message.answer(
        f"💎 Введите количество бонусов для использования\n"
        f"Доступно: {available_bonus} ₽ (максимум 30% от заказа)\n"
        f"Ваш баланс: {bonus_info['current_bonus']} ₽"
    )


# Добавьте этот обработчик после других payment обработчиков
@router.callback_query(F.data.in_(["pay_online", "pay_sbp", "pay_cash"]))
async def ask_about_bonus_usage(callback: CallbackQuery, state: FSMContext):
    """Спрашиваем, хочет ли пользователь использовать бонусы"""
    payment_method = callback.data.split("_")[1]
    await state.update_data(payment_method=payment_method)

    # Получаем данные о бонусах пользователя
    bonus_info = get_bonus_info(callback.from_user.id)
    data = await state.get_data()
    products_total = data.get('products_total', 0)

    # Рассчитываем максимально доступные бонусы (30% от суммы)
    max_bonus_allowed = int(products_total * 0.3)
    available_bonus = min(bonus_info['current_bonus'], max_bonus_allowed)

    if available_bonus > 0:
        kb = bonus_usage_keyboard(available_bonus, max_bonus_allowed)

        await callback.message.answer(
            f"💎 У вас есть {bonus_info['current_bonus']}₽ бонусов\n"
            f"Можно использовать: {available_bonus}₽ (30% от заказа)\n\n"
            "Хотите использовать бонусы для оплаты?",
            reply_markup=kb
        )
        await state.set_state(OrderState.use_bonus)
        await callback.answer()
        return

    # Если бонусов нет, переходим к обычной оплате
    await process_payment_method(callback, state)
    await callback.answer()


@router.callback_query(F.data == "use_bonus_yes", OrderState.use_bonus)
async def use_bonus_yes(callback: CallbackQuery, state: FSMContext):
    """Пользователь хочет использовать бонусы"""
    bonus_info = get_bonus_info(callback.from_user.id)
    data = await state.get_data()
    products_total = data.get('products_total', 0)
    max_bonus_allowed = int(products_total * 0.3)
    available_bonus = min(bonus_info['current_bonus'], max_bonus_allowed)

    await callback.message.answer(
        f"💎 Введите количество бонусов для использования\n"
        f"Доступно: {available_bonus}₽ (максимум 30% от заказа)\n"
        f"Ваш баланс: {bonus_info['current_bonus']}₽"
    )
    await state.set_state(OrderState.bonus_amount)
    await callback.answer()


@router.callback_query(F.data == "use_bonus_no", OrderState.use_bonus)
async def use_bonus_no(callback: CallbackQuery, state: FSMContext):
    """Пользователь не хочет использовать бонусы"""
    await state.update_data(bonus_used=0)
    await callback.message.answer("💳 Продолжаем оплату без использования бонусов")
    await process_payment_method(callback, state)
    await callback.answer()


async def process_payment_method(callback: CallbackQuery, state: FSMContext):
    """Обрабатываем выбранный способ оплаты"""
    data = await state.get_data()
    payment_method = data.get('payment_method')

    if payment_method in ['online', 'sbp']:
        await process_online_payment(callback, state)
    elif payment_method == 'cash':
        await process_cash_payment(callback, state)


@router.message(OrderState.bonus_amount)
async def process_bonus_amount(message: Message, state: FSMContext):
    """Обрабатываем ввод количества бонусов"""
    try:
        bonus_to_use = int(message.text)
        bonus_info = get_bonus_info(message.from_user.id)
        data = await state.get_data()
        products_total = data.get('products_total', 0)
        max_bonus_allowed = int(products_total * 0.3)

        # Проверяем лимиты
        if bonus_to_use <= 0:
            await message.answer("❌ Введите положительное число:")
            return

        if bonus_to_use > bonus_info['current_bonus']:
            await message.answer(f"❌ Недостаточно бонусов. Доступно: {bonus_info['current_bonus']}₽")
            return

        if bonus_to_use > max_bonus_allowed:
            await message.answer(f"❌ Можно использовать не более {max_bonus_allowed}₽ (30% от заказа)")
            return

        await state.update_data(bonus_used=bonus_to_use)

        # Рассчитываем итоговую сумму
        final_total = products_total - bonus_to_use
        await state.update_data(final_total=final_total)

        await message.answer(f"✅ Будет использовано {bonus_to_use}₽ бонусов\n"
                             f"💰 Итоговая сумма к оплате: {final_total}₽")

        # Продолжаем процесс оплаты
        await process_payment_method(message, state)

    except ValueError:
        await message.answer("❌ Введите число. Например: 500")


@router.callback_query(F.data.startswith("use_actual_"))
async def use_actual_bonus(callback: CallbackQuery, state: FSMContext):
    """Использовать предложенное количество бонусов"""
    actual_bonus = int(callback.data.split("_")[2])
    await state.update_data(bonus_used=actual_bonus)

    data = await state.get_data()
    cart_items = get_cart(callback.from_user.id)
    delivery_cost = data.get('delivery_cost', 0)

    order_calc = calculate_order_total(cart_items, delivery_cost, actual_bonus)

    await callback.message.answer(
        f"✅ Будет использовано {actual_bonus} бонусов\n\n"
        f"📊 Расчет заказа:\n"
        f"• Стоимость товаров: {order_calc['products_total']} ₽\n"
        f"• Доставка: {delivery_cost} ₽\n"
        f"• Использовано бонусов: {order_calc['bonus_used']} ₽\n"
        f"• Итого к оплате: {order_calc['final_total']} ₽"
    )

    # Продолжаем процесс оплаты
    payment_method = data.get('payment_method', 'online')
    if payment_method in ['online', 'sbp']:
        await process_online_payment(callback, state)
    else:
        await process_cash_payment(callback, state)

    await callback.answer()


@router.callback_query(F.data == "reenter_bonus")
async def reenter_bonus(callback: CallbackQuery, state: FSMContext):
    """Запросить повторный ввод количества бонусов"""
    bonus_info = get_bonus_info(callback.from_user.id)
    cart_items = get_cart(callback.from_user.id)
    products_total = sum(item['price'] * item['quantity'] for item in cart_items)
    max_bonus_allowed = int(products_total * 0.3)

    await callback.message.answer(
        f"💎 Введите количество бонусов для использования\n"
        f"Доступно: {bonus_info['current_bonus']} ₽\n"
        f"Максимум: {max_bonus_allowed} ₽ (30% от {products_total} ₽)"
    )
    await callback.answer()


@router.callback_query(F.data == "reenter_bonus")
async def reenter_bonus(callback: CallbackQuery, state: FSMContext):
    """Запросить повторный ввод количества бонусов"""
    bonus_info = get_bonus_info(callback.from_user.id)
    cart_items = get_cart(callback.from_user.id)
    products_total = sum(item['price'] * item['quantity'] for item in cart_items)
    max_bonus_allowed = int(products_total * 0.3)

    await callback.message.answer(
        f"💎 Введите количество бонусов для использования\n"
        f"Доступно: {bonus_info['current_bonus']} ₽\n"
        f"Максимум: {max_bonus_allowed} ₽ (30% от {products_total} ₽)"
    )
    await callback.answer()


@router.callback_query(F.data == "loyalty_history")
async def show_loyalty_history(callback: CallbackQuery):
    history = get_loyalty_history(callback.from_user.id, 10)
    if not history:
        await callback.message.answer("📊 История операций пуста")
        return

    text = "📊 <b>Последние операции с баллами:</b>\n\n"
    for operation in history:
        change = operation['points_change']
        sign = "➕" if change > 0 else "➖"
        date = operation['created_at'].split(".")[0] if isinstance(operation['created_at'], str) else \
            str(operation['created_at']).split(".")[0]

        text += (
            f"{sign} <b>{abs(change)} баллов</b>\n"
            f"📝 {operation['reason']}\n"
            f"📅 {date.replace('T', ' ')}\n"
            f"💎 Остаток: {operation['remaining_points']} баллов\n\n"
        )

    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()


# --- ОФОРМЛЕНИЕ ЗАКАЗА С БАЛЛАМИ ---
@router.callback_query(F.data == "use_bonus")
async def use_bonus_handler(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора использования бонусов"""
    user_id = callback.from_user.id
    cart_items = get_cart(user_id)
    products_total = sum(item['price'] * item['quantity'] for item in cart_items)

    bonus_info = get_bonus_info(user_id)
    max_bonus_allowed = int(products_total * 0.3)
    available_bonus = min(bonus_info['current_bonus'], max_bonus_allowed)

    await callback.message.answer(
        f"💎 <b>Введите сумму бонусов для использования</b>\n\n"
        f"Доступно: {available_bonus}₽\n"
        f"Максимум можно использовать: {max_bonus_allowed}₽\n\n"
        f"Пример: 500 (для использования 500₽ бонусов)",
        parse_mode="HTML"
    )
    await state.set_state(OrderState.bonus_amount)
    await callback.answer()


@router.callback_query(F.data == "skip_bonus")
async def skip_bonus_handler(callback: CallbackQuery, state: FSMContext):
    """Пропуск использования бонусов"""
    await callback.message.answer("👤 Введите ваше имя:")
    await state.set_state(OrderState.name)
    await callback.answer()


@router.callback_query(F.data.startswith("pay_"))
async def process_payment(callback: CallbackQuery, state: FSMContext):
    payment_method = callback.data.split("_")[1]
    await state.update_data(payment_method=payment_method)

    # Получаем данные
    data = await state.get_data()
    cart_items = get_cart(callback.from_user.id)
    products_total = sum(item['price'] * item['quantity'] for item in cart_items)
    delivery_cost = data.get('delivery_cost', 0)
    total = products_total + delivery_cost

    # Проверяем бонусы
    bonus_info = get_bonus_info(callback.from_user.id)
    available_bonus = bonus_info['current_bonus']
    max_bonus_allowed = int(products_total * 0.3)  # Максимум 30% от стоимости товаров

    if available_bonus > 0 and max_bonus_allowed > 0:
        usable_bonus = min(available_bonus, max_bonus_allowed)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"✅ Использовать {usable_bonus} ₽", callback_data=f"use_bonus_{usable_bonus}")],
            [InlineKeyboardButton(text="❌ Не использовать", callback_data="skip_bonus")]
        ])
        await callback.message.answer(
            f"У вас есть {available_bonus} ₽ бонусов.\n"
            f"Можно использовать до {max_bonus_allowed} ₽ (30% от заказа).\n"
            f"Использовать {usable_bonus} ₽?",
            reply_markup=kb
        )
    else:
        # Если бонусов нет — сразу показываем сводку
        await show_order_summary(callback, state, total)
        await callback.answer()


@router.callback_query(F.data.in_(["pay_online", "pay_sbp", "pay_cash"]))
async def process_payment_with_points(callback: CallbackQuery, state: FSMContext):
    """Предлагаем использовать баллы перед оплатой"""
    loyalty_info = get_loyalty_info(callback.from_user.id)

    if loyalty_info['current_points'] > 0:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💎 Использовать баллы", callback_data="use_loyalty_points")],
            [InlineKeyboardButton(text="💳 Оплатить без баллов", callback_data="skip_loyalty_points")]
        ])

        await callback.message.answer(
            f"💎 У вас есть {loyalty_info['current_points']} баллов\n"
            f"Это ≈ {loyalty_info['current_points']} ₽ скидки\n\n"
            "Хотите использовать баллы для оплаты?",
            reply_markup=kb
        )
        await state.set_state(OrderState.use_bonus)
    else:
        await process_online_payment(callback, state)


@router.callback_query(F.data == "use_loyalty_points", OrderState.use_bonus)
async def ask_points_amount(callback: CallbackQuery, state: FSMContext):
    loyalty_info = get_loyalty_info(callback.from_user.id)
    await callback.message.answer(
        f"💎 Введите количество баллов для использования\n"
        f"Доступно: {loyalty_info['current_points']} баллов"
    )


@router.message(OrderState.use_bonus)
async def process_points_amount(message: Message, state: FSMContext):
    try:
        points_to_use = int(message.text)
        loyalty_info = get_loyalty_info(message.from_user.id)

        if points_to_use <= 0 or points_to_use > loyalty_info['current_points']:
            await message.answer("❌ Неверное количество баллов. Попробуйте снова:")
            return

        await state.update_data(points_used=points_to_use)
        await message.answer(f"✅ Будет использовано {points_to_use} баллов")

        # Продолжаем стандартный процесс оплаты
        data = await state.get_data()
        if data.get('payment_method') in ['online', 'sbp']:
            await process_online_payment(message, state)
        else:
            await process_cash_payment(message, state)

    except ValueError:
        await message.answer("❌ Введите число. Например: 100")


# --- АДМИН КОМАНДЫ ---

@router.message(Command("help"))
async def help_command(message: Message):
    """Помощь по командам"""
    if is_admin(message.from_user.id):
        help_text = (
            "👑 <b>Команды администратора:</b>\n\n"
            "• /admin - Панель администратора\n"
            "• /add - Добавить новый товар\n"
            "• /mark_delivered - Отметить заказ как доставленный\n"
            "• /reviews_debug - Просмотр отзывов\n"
            "• /myid - Показать мой ID\n"
            "• /clear_my_cart - Очистить корзину\n"
            "• /reset_bonus - Сбросить бонусы\n"
            "• /pending_prices - Изменить цену, который по запросу\n"

            "📊 <b>Управление через кнопки:</b>\n"
            "• 📦 Управление заказами\n"
            "• ⭐ Управление отзывами\n"
            "• 📊 Статистика магазина\n"
            "• 💎 Управление бонусами\n\n"

            "💡 <i>Используйте кнопки в панели администратора для удобного управления</i>"
        )
    else:
        help_text = (
            "🌸 <b>Доступные команды:</b>\n\n"
            "• /start - Главное меню\n"
            "• /myid - Показать мой ID\n"
            "• /clear_my_cart - Очистить корзину\n"
            "• /help - Помощь по командам\n\n"

            "📱 <b>Основное меню:</b>\n"
            "• 🌸 Каталог - Просмотр товаров\n"
            "• 🚚 Доставка - Условия доставки\n"
            "• 📞 Менеджер - Связь с менеджером\n"
            "• 📍 На карте - Адрес магазина\n"
            "• 🎁 Сертификат - Подарочные сертификаты\n"
            "• ⭐ Отзывы - Отзывы клиентов\n"
            "• 🛒 Корзина - Ваша корзина\n"
            "• 🧾 Мои заказы - История заказов\n"
            "• 💎 Мои бонусы - Бонусная программа"
        )

    await message.answer(help_text, parse_mode="HTML")


@router.message(Command("admin"))
async def admin_panel(message: Message):
    """Панель администратора"""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Доступ запрещен")
        return

    await message.answer(
        "👑 <b>Панель администратора</b>\n\n"
        "Выберите раздел для управления:",
        reply_markup=admin_keyboard(),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "admin_back")
async def admin_back(callback: CallbackQuery):
    """Возврат в главное меню админки"""
    await callback.message.edit_text(
        "👑 <b>Панель администратора</b>\n\n"
        "Выберите раздел для управления:",
        reply_markup=admin_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "manage_orders")
async def manage_orders(callback: CallbackQuery):
    """Управление заказами"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Доступ запрещен")
        return

    await callback.message.edit_text(
        "📦 <b>Управление заказами</b>\n\n"
        "Выберите действие:",
        reply_markup=orders_management_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "orders_list")
async def show_orders_list(callback: CallbackQuery):
    """Показать список всех заказов"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Доступ запрещен")
        return

    # Получаем все заказы
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT o.*, u.first_name, u.last_name 
            FROM orders o 
            LEFT JOIN users u ON o.user_id = u.id 
            ORDER BY o.created_at DESC
        """)
        orders = [dict(row) for row in cur.fetchall()]

    if not orders:
        await callback.message.answer("📦 Заказов пока нет")
        return

    await callback.message.edit_text(
        f"📋 <b>Список заказов</b>\n\n"
        f"Всего заказов: {len(orders)}",
        reply_markup=orders_list_keyboard(orders),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("order_detail_"))
async def show_order_detail(callback: CallbackQuery):
    """Показать детали заказа"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Доступ запрещен")
        return

    order_id = int(callback.data.split("_")[2])

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT o.*, u.first_name, u.last_name, u.username 
            FROM orders o 
            LEFT JOIN users u ON o.user_id = u.id 
            WHERE o.id = ?
        """, (order_id,))
        order = cur.fetchone()

        if not order:
            await callback.answer("❌ Заказ не найден")
            return

        order = dict(order)
        items = json.loads(order['items'])

        order_text = (
            f"📦 <b>Заказ #{order['id']}</b>\n\n"
            f"👤 <b>Клиент:</b> {order['first_name']} {order['last_name']}\n"
            f"📞 <b>Телефон:</b> {order['phone']}\n"
            f"📍 <b>Адрес:</b> {order['address'] or 'Самовывоз'}\n"
            f"📅 <b>Дата доставки:</b> {order['delivery_date']}\n"
            f"⏰ <b>Время:</b> {order['delivery_time']}\n"
            f"💳 <b>Оплата:</b> {get_payment_method_name(order['payment_method'])}\n"
            f"📊 <b>Статус:</b> {order['status']}\n"
            f"💰 <b>Сумма:</b> {order['total']} ₽\n\n"
            f"🛒 <b>Товары:</b>\n"
        )

        for item in items:
            order_text += f"• {item['name']} × {item['quantity']} - {item['price'] * item['quantity']} ₽\n"

        if order['bonus_used'] > 0:
            order_text += f"\n💎 <b>Использовано бонусов:</b> {order['bonus_used']} ₽"

        await callback.message.edit_text(
            order_text,
            reply_markup=order_detail_keyboard(order['id']),
            parse_mode="HTML"
        )

    await callback.answer()


@router.callback_query(F.data.startswith("deliver_"))
async def mark_order_delivered(callback: CallbackQuery):
    """Отметить заказ как доставленный"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Доступ запрещен")
        return

    order_id = int(callback.data.split("_")[1])

    # Обновляем статус заказа
    update_order_status(order_id, 'delivered')

    # Получаем информацию о заказе для уведомления пользователя
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM orders WHERE id = ?", (order_id,))
        order = cur.fetchone()

    if order:
        user_id = order['user_id']
        # Отправляем уведомление пользователю
        try:
            await bot.send_message(
                user_id,
                f"🎉 <b>Ваш заказ #{order_id} доставлен!</b>\n\n"
                f"Спасибо за покупку! Надеемся, вам понравились наши цветы 💐\n\n"
                f"Не забудьте оставить отзыв о заказе!",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление пользователю {user_id}: {e}")

    await callback.answer(f"✅ Заказ #{order_id} отмечен как доставленный")
    await callback.message.edit_text(
        f"✅ <b>Заказ #{order_id} отмечен как доставленный</b>\n\n"
        f"Клиент получил уведомление о доставке.",
        parse_mode="HTML"
    )


@router.callback_query(F.data == "manage_reviews")
async def manage_reviews(callback: CallbackQuery):
    """Управление отзывами"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Доступ запрещен")
        return

    # Получаем статистику по отзывам
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM reviews")
        total_reviews = cur.fetchone()[0]

        cur.execute("SELECT AVG(rating) FROM reviews WHERE rating > 0")
        avg_rating = cur.fetchone()[0] or 0

    await callback.message.edit_text(
        f"⭐ <b>Управление отзывами</b>\n\n"
        f"📊 Всего отзывов: {total_reviews}\n"
        f"🌟 Средний рейтинг: {avg_rating:.1f}/5\n\n"
        f"Выберите действие:",
        reply_markup=reviews_management_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "all_reviews")
async def show_all_reviews(callback: CallbackQuery):
    """Показать все отзывы"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Доступ запрещен")
        return

    reviews = get_reviews(limit=50)  # Получаем больше отзывов

    if not reviews:
        await callback.message.answer("📝 Отзывов пока нет")
        return

    review_text = "⭐ <b>Последние отзывы:</b>\n\n"

    for i, review in enumerate(reviews[:10], 1):  # Показываем первые 10
        stars = "⭐" * min(5, max(1, review.get('rating', 5)))
        created_at = review['created_at'].split(".")[0] if isinstance(review['created_at'], str) else \
            str(review['created_at']).split(".")[0]

        review_text += (
            f"{stars}\n"
            f"<i>\"{review['text'][:100]}...\"</i>\n"
            f"<b>— {review.get('user_name', 'Аноним')}</b>\n"
            f"<code>📅 {created_at}</code>\n\n"
        )

    await callback.message.answer(review_text, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin_stats")
async def show_admin_stats(callback: CallbackQuery):
    """Показать статистику магазина"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Доступ запрещен")
        return

    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()

        # Статистика заказов
        cur.execute("SELECT COUNT(*) FROM orders")
        total_orders = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM orders WHERE status = 'delivered'")
        delivered_orders = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM orders WHERE status = 'new'")
        new_orders = cur.fetchone()[0]

        cur.execute("SELECT SUM(total) FROM orders WHERE status = 'delivered'")
        total_revenue = cur.fetchone()[0] or 0

        # Статистика пользователей
        cur.execute("SELECT COUNT(DISTINCT user_id) FROM orders")
        active_clients = cur.fetchone()[0]

        # Статистика отзывов
        cur.execute("SELECT COUNT(*) FROM reviews")
        total_reviews = cur.fetchone()[0]

        cur.execute("SELECT AVG(rating) FROM reviews WHERE rating > 0")
        avg_rating = cur.fetchone()[0] or 0

    stats_text = (
        "📊 <b>Статистика магазина</b>\n\n"
        f"📦 <b>Заказы:</b>\n"
        f"• Всего заказов: {total_orders}\n"
        f"• Новые заказы: {new_orders}\n"
        f"• Доставлено: {delivered_orders}\n"
        f"• Общая выручка: {total_revenue} ₽\n\n"

        f"👥 <b>Клиенты:</b>\n"
        f"• Активных клиентов: {active_clients}\n\n"

        f"⭐ <b>Отзывы:</b>\n"
        f"• Всего отзывов: {total_reviews}\n"
        f"• Средний рейтинг: {avg_rating:.1f}/5\n\n"

        f"<i>Последнее обновление: {datetime.now().strftime('%d.%m.%Y %H:%M')}</i>"
    )

    await callback.message.answer(stats_text, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "manage_bonuses")
async def manage_bonuses(callback: CallbackQuery):
    """Управление бонусной системой"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Доступ запрещен")
        return

    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM loyalty_program WHERE current_bonus > 0")
        users_with_bonuses = cur.fetchone()[0]

        cur.execute("SELECT SUM(current_bonus) FROM loyalty_program")
        total_bonuses = cur.fetchone()[0] or 0

        cur.execute("SELECT SUM(total_bonus_earned) FROM loyalty_program")
        total_earned = cur.fetchone()[0] or 0

    bonus_text = (
        "💎 <b>Управление бонусной системой</b>\n\n"
        f"👥 Пользователей с бонусами: {users_with_bonuses}\n"
        f"💰 Всего бонусов на счетах: {total_bonuses} ₽\n"
        f"🏆 Всего начислено бонусов: {total_earned} ₽\n\n"

        "<b>Доступные действия:</b>\n"
        "• /reset_bonus - Сбросить бонусы\n"
    )

    await callback.message.answer(bonus_text, parse_mode="HTML")
    await callback.answer()


@router.message(Command("reviews_debug"))
async def reviews_debug(message: Message):
    """Отладочная команда для проверки отзывов"""
    if not is_admin(message.from_user.id):  # Обновляем проверку
        await message.answer("❌ Эта команда только для администратора")
        return

    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()

        # Проверяем существует ли таблица отзывов
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='reviews'")
        table_exists = cur.fetchone()

        if table_exists:
            cur.execute("SELECT COUNT(*) FROM reviews")
            count = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM reviews WHERE order_id IS NOT NULL")
            order_reviews = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM reviews WHERE order_id IS NULL")
            general_reviews = cur.fetchone()[0]

            text = (
                f"✅ Таблица отзывов существует\n"
                f"📊 Всего отзывов: {count}\n"
                f"📦 Отзывов на заказы: {order_reviews}\n"
                f"🏪 Общих отзывов: {general_reviews}"
            )

            await message.answer(text)

            # Показываем последние отзывы
            cur.execute("SELECT * FROM reviews ORDER BY created_at DESC LIMIT 5")
            reviews = cur.fetchall()
            if reviews:
                text = "📝 Последние 5 отзывов:\n\n"
                for review in reviews:
                    order_info = f" (Заказ #{review[5]})" if review[5] else " (Общий)"
                    text += f"ID: {review[0]}, User: {review[2]}, Rating: {review[4]}{order_info}\n"
                    text += f"Text: {review[3][:50]}...\n\n"
                await message.answer(text)
        else:
            await message.answer("❌ Таблица отзывов не существует!")


@router.message(Command("mark_delivered"))
async def mark_delivered(message: Message):
    """Пометить последний заказ как доставленный (для тестирования)"""
    if not is_admin(message.from_user.id):  # Обновляем проверку
        await message.answer("❌ Эта команда только для администратора")
        return

    user_id = message.from_user.id
    orders = get_user_orders(user_id)

    if not orders:
        await message.answer("❌ Нет заказов")
        return

    last_order = orders[0]
    update_order_status(last_order['id'], 'delivered')

    # Имитируем запрос отзыва
    await ask_for_review_after_delivery(user_id, last_order['id'])

    await message.answer(f"✅ Заказ #{last_order['id']} помечен как доставленный")


@router.message(Command("add"))
async def add_bouquet_cmd(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):  # Проверяем всех админов
        await message.answer("❌ Доступ запрещен")
        return

    await message.answer("📸 Отправьте фото нового букета")
    await state.set_state(AdminState.photo)


@router.message(AdminState.photo)
async def get_bouquet_photo(message: Message, state: FSMContext):
    if message.photo:
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        photo_path = f"images/bouquet_{timestamp}.jpg"
        try:
            await bot.download_file(file.file_path, photo_path)
            await state.update_data(photo=photo_path)
        except Exception as e:
            await message.answer(f"❌ Ошибка сохранения фото: {e}")
            return
    else:
        await state.update_data(photo=None)

    await message.answer("📝 Введите название букета:")
    await state.set_state(AdminState.name)


@router.message(AdminState.name)
async def get_bouquet_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("📝 Введите категорию (букет/горшечный):")
    await state.set_state(AdminState.category)


@router.message(AdminState.category)
async def get_bouquet_category(message: Message, state: FSMContext):
    category_text = message.text.strip().lower()

    # Преобразуем в понятный формат для БД
    if "букет" in category_text:
        category = "bouquet"
    elif "горшечный" in category_text or "растение" in category_text:
        category = "plant"
    else:
        await message.answer("❌ Неизвестная категория. Введите 'букет' или 'горшечный':")
        return

    await state.update_data(category=category)
    await message.answer("💬 Введите краткое описание:")
    await state.set_state(AdminState.description)


@router.message(AdminState.description)
async def get_bouquet_desc(message: Message, state: FSMContext):
    await state.update_data(description=message.text)
    await message.answer("📄 Введите полное описание:")
    await state.set_state(AdminState.full_description)


@router.message(AdminState.full_description)
async def get_bouquet_full_desc(message: Message, state: FSMContext):
    await state.update_data(full_description=message.text)
    await message.answer("💰 Введите цену (только число):")
    await state.set_state(AdminState.price)


@router.message(AdminState.price)
async def get_bouquet_price(message: Message, state: FSMContext):
    price_text = message.text.strip().lower()

    # Проверяем, является ли ввод числом
    try:
        price = float(price_text)
        # Если это число - сохраняем как обычно
        data = await state.get_data()

        product_id = add_product(
            name=data['name'],
            description=data['description'],
            full_description=data['full_description'],
            price=price,
            photo=data.get('photo'),
            category=data['category'],
            is_daily=True
        )

        await message.answer(f"✅ Букет «{data['name']}» добавлен как букет дня! Цена: {price} ₽")
        await state.clear()


    except ValueError:
        # Если цена — не число, например "по запросу"
        data = await state.get_data()
        name = data['name']
        category = data['category']
        description = data.get('description', 'Нет описания')
        full_description = data.get('full_description', description)
        photo_path = data.get('photo')

        # Сохраняем товар с меткой "по запросу"
        product_id = add_product(
            name=name,
            description=description,
            full_description=full_description,
            price=0,  # или None, если поддерживается
            photo=photo_path,
            category=category,
            is_daily=True,
            on_request=True  # или добавь в запрос: DEFAULT FALSE
        )

        # Уведомляем, что товар добавлен, но цена по запросу
        await message.answer(
            f"✅ Товар «{name}» добавлен в каталог как 'по запросу'.\n"
            f"💬 Клиенты смогут уточнить цену у менеджера.",
            parse_mode="HTML"
        )

        # Оповещаем админов, что новый товар ожидает цену
        admin_msg = (
            "🟡 <b>НОВЫЙ ТОВАР 'ПО ЗАПРОСУ'</b>\n"
            f"👤 <b>Флорист:</b> {message.from_user.full_name}\n"
            f"🌸 <b>Товар:</b> {name}\n"
            f"📝 <b>Описание:</b> {description}\n"
            f"⚠️ <b>Требуется утвердить цену вручную.</b>"
        )

        await notify_admins(admin_msg)

        await state.clear()


@router.callback_query(F.data == "ask_manager_price")
async def ask_manager_for_price(callback: CallbackQuery, state: FSMContext):
    """Запрос цены у менеджера"""
    data = await state.get_data()

    # Формируем сообщение для менеджера
    admin_msg = (
        "💰 <b>ЗАПРОС ЦЕНЫ ОТ ФЛОРИСТА</b>\n\n"
        f"👤 <b>Флорист:</b> {callback.from_user.full_name}\n"
        f"🆔 <b>ID:</b> {callback.from_user.id}\n\n"
        f"🌸 <b>Букет:</b> {data['name']}\n"
        f"📝 <b>Описание:</b> {data['description']}\n"
        f"💬 <b>Предполагаемая цена:</b> {data.get('price_text', 'не указана')}\n\n"
        f"⚠️ <b>Требуется уточнить точную цену!</b>"
    )

    try:
        await notify_admins(admin_msg)
        await callback.message.answer(
            "✅ Запрос отправлен менеджеру!\n"
            "Менеджер свяжется с вами для уточнения точной цены."
        )
    except Exception as e:
        await callback.message.answer(
            "❌ Ошибка при отправке запроса. "
            "Пожалуйста, свяжитесь с менеджером самостоятельно: @mgk71"
        )

    await state.clear()
    await callback.answer()


@router.callback_query(F.data == "budget_selection_admin")
async def budget_selection_admin(callback: CallbackQuery, state: FSMContext):
    """Подбор цены под бюджет для админа"""
    data = await state.get_data()
    await state.update_data(admin_product_data=data)  # Сохраняем данные товара

    await callback.message.answer(
        "💰 <b>Подбор цены под бюджет</b>\n\n"
        "Введите предполагаемый бюджет для этого букета (в рублях):",
        parse_mode="HTML"
    )
    await state.set_state(AdminState.budget)


@router.callback_query(F.data == "cancel_add_product")
async def cancel_add_product(callback: CallbackQuery, state: FSMContext):
    """Отмена добавления товара"""
    await state.clear()
    await callback.message.answer("❌ Добавление товара отменено.")
    await callback.answer()


@router.message(AdminState.budget)
async def process_admin_budget(message: Message, state: FSMContext):
    """Обработка бюджета от админа"""
    try:
        budget = float(message.text)
        data = await state.get_data()
        product_data = data['admin_product_data']

        # Используем бюджет как примерную цену
        product_id = add_product(
            name=product_data['name'],
            description=product_data['description'],
            full_description=product_data['full_description'],
            price=budget,
            photo=product_data.get('photo'),
            category=product_data['category'],
            is_daily=True
        )

        await message.answer(
            f"✅ Букет «{product_data['name']}» добавлен с примерной ценой {budget} ₽\n\n"
            f"💡 <i>Цена будет уточнена менеджером перед продажей</i>",
            parse_mode="HTML"
        )

        # Уведомляем менеджера
        admin_msg = (
            "💰 <b>БУКЕТ ДОБАВЛЕН С ПРИМЕРНОЙ ЦЕНОЙ</b>\n\n"
            f"👤 <b>Флорист:</b> {message.from_user.full_name}\n"
            f"🌸 <b>Букет:</b> {product_data['name']}\n"
            f"💵 <b>Примерная цена:</b> {budget} ₽\n"
            f"📝 <b>Описание:</b> {product_data['description']}\n\n"
            f"⚠️ <b>Требуется утвердить окончательную цену!</b>"
        )
        await notify_admins(admin_msg)

    except ValueError:
        await message.answer("❌ Пожалуйста, введите число. Например: 2500")
        return

    await state.clear()


@router.message(AdminEditPrice.waiting_for_price)
async def process_new_price(message: Message, state: FSMContext):
    try:
        new_price = float(message.text)
        if new_price < 0:
            await message.answer("❌ Цена не может быть отрицательной.")
            return

        data = await state.get_data()
        product_id = data['product_id']

        # Обновляем цену и снимаем флаг on_request
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE products 
                SET price = ?, on_request = FALSE 
                WHERE id = ?
            """, (new_price, product_id))
            conn.commit()

        # Получаем обновлённый товар
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT name FROM products WHERE id = ?", (product_id,))
            product = cur.fetchone()

        await message.answer(
            f"✅ Цена установлена!\n"
            f"📦 <b>{product['name']}</b>\n"
            f"💰 <b>{new_price} ₽</b>",
            parse_mode="HTML"
        )

        # Оповещаем админа, что можно обновить список
        await message.answer("📌 Обновите список: /pending_prices")

        await state.clear()

    except ValueError:
        await message.answer("❌ Введите число. Например: 2800")
    except Exception as e:
        logger.error(f"Ошибка при установке цены: {e}")
        await message.answer("❌ Ошибка при сохранении.")
        await state.clear()


@router.message(Command("myid"))
async def show_my_id(message: Message):
    """Показывает ID пользователя (удобно для добавления админов)"""
    user_id = message.from_user.id
    is_user_admin = is_admin(user_id)

    await message.answer(
        f"🆔 <b>Ваш ID:</b> <code>{user_id}</code>\n"
        f"👑 <b>Статус администратора:</b> {'✅ Да' if is_user_admin else '❌ Нет'}\n\n"
        f"Чтобы добавить администратора, добавьте этот ID в config.py",
        parse_mode="HTML"
    )


# Запуск фоновых задач
async def auto_cleanup_daily_products():
    """Фоновая задача для очистки старых букетов дня"""
    while True:
        try:
            now = datetime.now()
            next_run = now.replace(hour=0, minute=1, second=0, microsecond=0)
            if now > next_run:
                next_run += timedelta(days=1)

            wait_seconds = (next_run - now).total_seconds()
            await asyncio.sleep(wait_seconds)

            cleaned = cleanup_old_daily_products()
            logger.info(f"Автоматически очищено {cleaned} старых букетов дня")

        except Exception as e:
            logger.error(f"Ошибка в auto_cleanup_daily_products: {e}")
            await asyncio.sleep(3600)


@router.callback_query(F.data == "test_create_order")
async def test_create_order(callback: CallbackQuery):
    """Создание тестового заказа с начислением бонусов"""
    try:
        user_id = callback.from_user.id

        # Создаем тестовые товары в корзине
        clear_cart(user_id)
        add_to_cart(user_id, 1)  # добавляем тестовый товар
        add_to_cart(user_id, 1)  # добавляем еще один

        # Создаем тестовый заказ
        order_id = create_order(
            user_id=user_id,
            name="Тестовый Пользователь",
            phone="+79999999999",
            address="Тестовый адрес",
            delivery_date="01.01.2024",
            delivery_time="12:00-15:00",
            payment="test",
            delivery_cost=300,
            delivery_type="delivery",
            bonus_used=0
        )

        bonus_info = get_bonus_info(user_id)

        await callback.message.answer(
            f"✅ <b>Тестовый заказ создан!</b>\n\n"
            f"📦 Заказ #: {order_id}\n"
            f"💎 Начислено бонусов: {bonus_info['current_bonus'] - bonus_info.get('previous_balance', 0)} ₽\n"
            f"💰 Текущий баланс: {bonus_info['current_bonus']} ₽\n\n"
            f"<i>Бонусы начислены без реальной оплаты</i>",
            parse_mode="HTML"
        )

    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {e}")
    await callback.answer()


@router.callback_query(F.data == "test_check_balance")
async def test_check_balance(callback: CallbackQuery):
    """Проверка текущего баланса бонусов"""
    bonus_info = get_bonus_info(callback.from_user.id)

    text = (
        f"💎 <b>Ваш баланс бонусов:</b>\n\n"
        f"💰 Всего потрачено: {bonus_info['total_spent']} ₽\n"
        f"🎁 Доступно бонусов: {bonus_info['current_bonus']} ₽\n"
        f"🏆 Всего начислено: {bonus_info['total_bonus_earned']} ₽\n\n"
    )

    # Пример расчета для следующего заказа
    if bonus_info['current_bonus'] > 0:
        example_amount = 5000
        max_usable = min(bonus_info['current_bonus'], int(example_amount * 0.3))
        text += (
            f"<b>Пример для заказа на {example_amount} ₽:</b>\n"
            f"Можно использовать: {max_usable} ₽ бонусами\n"
            f"К оплате: {example_amount - max_usable} ₽\n"
        )

    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "test_bonus_history")
async def test_bonus_history(callback: CallbackQuery):
    """Показать историю бонусов"""
    await show_bonus_history(callback)


@router.callback_query(F.data == "test_use_bonus")
async def test_use_bonus(callback: CallbackQuery, state: FSMContext):
    """Тестирование использования бонусов"""
    bonus_info = get_bonus_info(callback.from_user.id)

    if bonus_info['current_bonus'] == 0:
        await callback.message.answer(
            "❌ У вас нет бонусов для тестирования.\n"
            "Сначала создайте тестовый заказ командой /test_bonus"
        )
        await callback.answer()
        return

    # Добавляем тестовые товары в корзину
    clear_cart(callback.from_user.id)
    add_to_cart(callback.from_user.id, 1)
    add_to_cart(callback.from_user.id, 1)

    cart_items = get_cart(callback.from_user.id)
    products_total = sum(item['price'] * item['quantity'] for item in cart_items)
    max_bonus_allowed = int(products_total * 0.3)
    available_bonus = min(bonus_info['current_bonus'], max_bonus_allowed)

    text = (
        f"🧪 <b>Тест использования бонусов</b>\n\n"
        f"🛒 Сумма заказа: {products_total} ₽\n"
        f"💎 Ваш баланс: {bonus_info['current_bonus']} ₽\n"
        f"📊 Можно использовать: {available_bonus} ₽ (30% от заказа)\n\n"
        f"Введите сумму бонусов для использования:"
    )

    await callback.message.answer(text, parse_mode="HTML")
    await state.set_state(OrderState.use_bonus)
    await state.update_data(test_mode=True)
    await callback.answer()


@router.message(Command("add_test_bonus"))
async def add_test_bonus(message: Message):
    """Добавить тестовые бонусы"""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Эта команда только для администратора")
        return

    try:
        # Добавляем 1000 бонусов для тестирования
        user_id = message.from_user.id
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE loyalty_program 
                SET current_bonus = current_bonus + ?,
                    total_bonus_earned = total_bonus_earned + ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
            """, (1000, 1000, user_id))

            cur.execute("""
                INSERT INTO loyalty_history 
                (user_id, points_change, reason, remaining_points)
                SELECT ?, ?, ?, current_bonus 
                FROM loyalty_program 
                WHERE user_id = ?
            """, (user_id, 1000, "Тестовое начисление бонусов", user_id))

            conn.commit()

        bonus_info = get_bonus_info(user_id)
        await message.answer(
            f"✅ Добавлено 1000 тестовых бонусов!\n"
            f"💎 Текущий баланс: {bonus_info['current_bonus']} ₽"
        )

    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@router.message(Command("reset_bonus"))
async def reset_bonus(message: Message):
    """Сбросить бонусы к начальному состоянию"""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Эта команда только для администратора")
        return

    try:
        user_id = message.from_user.id
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE loyalty_program 
                SET current_bonus = 0,
                    total_bonus_earned = 0,
                    total_spent = 0,
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
            """, (user_id,))

            cur.execute("DELETE FROM loyalty_history WHERE user_id = ?", (user_id,))
            conn.commit()

        await message.answer("✅ Бонусы сброшены к начальному состоянию!")

    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@router.message(Command("edit_price"))
async def edit_price_cmd(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Доступ запрещён.")
        return

    try:
        # Получаем ID товара из команды: /edit_price 123
        args = message.text.split()
        if len(args) != 2:
            await message.answer("📌 Использование: <code>/edit_price <id_товара></code>", parse_mode="HTML")
            return

        product_id = int(args[1])

        # Проверяем, существует ли товар и цена либо 0, либо on_request=True
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM products WHERE id = ?", (product_id,))
            product = cur.fetchone()

        if not product:
            await message.answer("❌ Товар не найден.")
            return

        if product['price'] > 0 and not product['on_request']:
            await message.answer(f"✅ У товара «{product['name']}» уже установлена цена: {product['price']} ₽\n"
                                 "Редактировать можно только товары с ценой 'по запросу'.")
            return

        await state.update_data(product_id=product_id)
        await message.answer(
            f"🔧 Редактирование цены для товара:\n"
            f"📦 <b>{product['name']}</b>\n"
            f"📝 {product['description']}\n\n"
            f"Введите новую цену в рублях (только число):",
            parse_mode="HTML"
        )
        await state.set_state(AdminEditPrice.waiting_for_price)

    except ValueError:
        await message.answer("❌ Неверный формат ID. Используйте число.")
    except Exception as e:
        logger.error(f"Ошибка в /edit_price: {e}")
        await message.answer("❌ Ошибка при обработке команды.")


@router.message(Command("pending_prices"))
async def show_pending_products(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Доступ запрещён.")
        return

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT id, name, description, photo, created_date
            FROM products
            WHERE (price = 0 OR on_request = TRUE)
            AND is_daily = TRUE
            ORDER BY created_date DESC
        """)
        products = [dict(row) for row in cur.fetchall()]

    if not products:
        await message.answer("🟢 Нет товаров с ценой 'по запросу'.")
        return

    for product in products:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Установить цену", callback_data=f"set_price_{product['id']}")],
            [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_product_{product['id']}")]
        ])
        await message.answer_photo(
            photo=FSInputFile(product['photo']),
            caption=f"🟡 <b>Товар без цены:</b>\n"
                    f"📦 <b>{product['name']}</b>\n"
                    f"📝 {product['description']}\n"
                    f"🆔 <code>{product['id']}</code>",
            reply_markup=kb,
            parse_mode="HTML"
        )


@router.callback_query(F.data.startswith("set_price_"))
async def start_set_price(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Доступ запрещён.")
        return

    product_id = int(callback.data.split("_")[2])

    # Проверяем, существует ли товар
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT name FROM products WHERE id = ?", (product_id,))
        product = cur.fetchone()

    if not product:
        await callback.answer("❌ Товар не найден.")
        return

    await state.update_data(product_id=product_id)
    await callback.message.answer(
        f"🔧 Введите новую цену для товара «{product['name']}» (в рублях):"
    )
    await state.set_state(AdminEditPrice.waiting_for_price)
    await callback.answer()


def calculate_order_with_bonus(user_id: int, delivery_cost: int, bonus_to_use: int = 0) -> dict:
    """Рассчитывает заказ с учетом бонусов"""
    cart_items = get_cart(user_id)
    products_total = sum(item['price'] * item['quantity'] for item in cart_items)

    # Максимально можно использовать бонусов - 30% от суммы товаров
    max_bonus_allowed = int(products_total * 0.3)
    actual_bonus_used = min(bonus_to_use, max_bonus_allowed)

    # Бонусы применяются только к стоимости товаров
    final_total = max(0, products_total - actual_bonus_used + delivery_cost)

    return {
        'products_total': products_total,
        'delivery_cost': delivery_cost,
        'bonus_used': actual_bonus_used,
        'max_bonus_allowed': max_bonus_allowed,
        'final_total': final_total
    }


def can_use_bonus(user_id: int, bonus_amount: int, cart_items: List[Dict] = None) -> Dict:
    """Проверяет, можно ли использовать указанное количество бонусов"""
    if cart_items is None:
        cart_items = get_cart(user_id)

    products_total = sum(item['price'] * item['quantity'] for item in cart_items)
    max_bonus_allowed = int(products_total * 0.3)  # 30% от суммы товаров
    bonus_info = get_bonus_info(user_id)
    available_bonus = bonus_info['current_bonus']

    actual_usable = min(bonus_amount, available_bonus, max_bonus_allowed)

    return {
        'can_use': actual_usable > 0,
        'actual_usable': actual_usable,
        'available_bonus': available_bonus,
        'max_allowed': max_bonus_allowed,
        'products_total': products_total
    }


@router.message(Command("test_cert"))
async def test_certificate_command(message: Message):
    """Быстрая команда для тестирования сертификата"""
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Тест сертификата (1 рубль)", callback_data="cert_1")]
    ])

    await message.answer(
        "🔧 <b>Тестирование сертификата</b>\n\n"
        "Нажмите кнопку для покупки тестового сертификата за 1 рубль:",
        reply_markup=kb,
        parse_mode="HTML"
    )
