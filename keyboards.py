from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)

# Главное меню с бонусами
main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🌸 Каталог")],
        [KeyboardButton(text="🚚 Доставка")],
        [KeyboardButton(text="📞 Менеджер")],
        [KeyboardButton(text="📍 На карте")],
        [KeyboardButton(text="🎁 Сертификат")],
        [KeyboardButton(text="⭐ Отзывы")],
        [KeyboardButton(text="🛒 Корзина")],
        [KeyboardButton(text="🧾 Мои заказы")],
        [KeyboardButton(text="💎 Мои бонусы")],
    ],
    resize_keyboard=True
)

# Меню категорий
catalog_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💐 Букеты")],
        [KeyboardButton(text="🌱 Горшечные растения")],
        [KeyboardButton(text="⬅️ Назад в меню")],
    ],
    resize_keyboard=True
)


# Клавиатура товара
def product_keyboard(product_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📖 Подробнее", callback_data=f"details_{product_id}")],
            [InlineKeyboardButton(text="🛒 В корзину", callback_data=f"add_{product_id}")],
            [InlineKeyboardButton(text="📞 Уточнить наличие", callback_data=f"check_avail_{product_id}")],
            [InlineKeyboardButton(text="⬅️ Назад к категориям", callback_data="back_to_categories")]
        ]
    )


# Клавиатура "Подробнее"
def details_keyboard(product_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛒 Добавить в корзину", callback_data=f"add_{product_id}")],
            [InlineKeyboardButton(text="📞 Уточнить наличие", callback_data=f"check_avail_{product_id}")],
            [InlineKeyboardButton(text="💬 Спросить у менеджера", url="https://t.me/mgk71")],
            [InlineKeyboardButton(text="⬅️ Назад к товарам", callback_data="back_to_products")]
        ]
    )


# Корзина
def cart_keyboard(cart_items):
    """Клавиатура корзины с кнопками управления количеством"""

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Оформить заказ", callback_data="checkout")],
        [InlineKeyboardButton(text="🗑 Очистить корзину", callback_data="clear_cart")],
        [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
    ])


# Клавиатура доставки
def delivery_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚗 Условия доставки", callback_data="delivery_conditions")],
            [InlineKeyboardButton(text="💳 Способы оплаты", callback_data="payment_methods")],
            [InlineKeyboardButton(text="📦 Самовывоз", callback_data="pickup_info")],
            [InlineKeyboardButton(text="⏰ Сроки доставки", callback_data="delivery_times")],
            [InlineKeyboardButton(text="💬 Спросите у менеджера", url="https://t.me/mgk71")]
        ]
    )


def loyalty_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 История операций", callback_data="loyalty_history")],
            [InlineKeyboardButton(text="ℹ️ Как использовать", callback_data="loyalty_help")],
            [InlineKeyboardButton(text="🎯 Условия программы", callback_data="loyalty_terms")]
        ]
    )


def points_usage_keyboard(available_points: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💎 Использовать баллы", callback_data="use_loyalty_points")],
            [InlineKeyboardButton(text="💳 Оплатить без баллов", callback_data="skip_loyalty_points")],
            [InlineKeyboardButton(text="📋 Правила использования", callback_data="loyalty_rules")]
        ]
    )


# Клавиатура для использования бонусов
def bonus_usage_keyboard(available_bonus: int, max_allowed: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"💎 Использовать бонусы (до {max_allowed}₽)", callback_data="use_bonus_yes")],
            [InlineKeyboardButton(text="💳 Без бонусов", callback_data="use_bonus_no")]
        ]
    )

# --------------

# Клавиатура администратора
def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Управление заказами", callback_data="manage_orders")],
        [InlineKeyboardButton(text="⭐ Управление отзывами", callback_data="manage_reviews")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="💎 Управление бонусами", callback_data="manage_bonuses")]
    ])


# Клавиатура управления заказами
def orders_management_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Список заказов", callback_data="orders_list")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")]
    ])


# Клавиатура управления отзывами
def reviews_management_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Все отзывы", callback_data="all_reviews")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")]
    ])


# Клавиатура для списка заказов
def orders_list_keyboard(orders, page=0, per_page=5):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])

    start_idx = page * per_page
    end_idx = start_idx + per_page
    current_orders = orders[start_idx:end_idx]

    for order in current_orders:
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(
                text=f"Заказ #{order['id']} - {order['status']}",
                callback_data=f"order_detail_{order['id']}"
            )
        ])

    # Кнопки навигации
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"orders_page_{page - 1}"))
    if end_idx < len(orders):
        nav_buttons.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"orders_page_{page + 1}"))

    if nav_buttons:
        keyboard.inline_keyboard.append(nav_buttons)

    keyboard.inline_keyboard.append([InlineKeyboardButton(text="⬅️ Назад к управлению", callback_data="manage_orders")])

    return keyboard


# Клавиатура для деталей заказа
def order_detail_keyboard(order_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отметить доставленным", callback_data=f"deliver_{order_id}")],
        [InlineKeyboardButton(text="📋 Назад к списку", callback_data="orders_list")]
    ])