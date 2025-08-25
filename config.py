import os
from pathlib import Path

# Токен бота
BOT_TOKEN = "8201157651:AAHIA7J5_NuRHhBmEEgB1H0vuXK4DnqBucI"

# ID администратора
ADMIN_ID = 1095668090  # Главный админ
ADMIN_ID1 = 6643553268  # Второй админ
ADMIN_ID2 = 905582217  # Третий админ

# Список всех администраторов
ADMINS = [ADMIN_ID, ADMIN_ID1, ADMIN_ID2]

# ЮKassa Live
YOOKASSA_SHOP_ID = "1037498"
YOOKASSA_SECRET_KEY = "live_jxIub1SHUSUh5F2hw_CjY2kK4a2Rc57yqHx5uSySQ34"

# Настройки вебхуков
WEBHOOK_HOST = "yourdomain.com"  # Ваш домен
WEBHOOK_PATH = "/webhook/yookassa"
WEBHOOK_URL = f"https://{WEBHOOK_HOST}{WEBHOOK_PATH}"
WEBHOOK_SECRET = "your_webhook_secret_token"  # Сгенерируйте случайный токен

# Информация о магазине
SHOP_INFO = {
    "name": "Цветочная лавка",
    "address": "1-й Вешняковский пр., 2А, Москва",
    "phone": "+7 (965) 230-17-29",
    "work_hours": "Ежедневно с 8:00 до 20:00"
}

# Путь к базе данных
DB_PATH = "data/florist.db"

# Создаем необходимые директории
Path("data").mkdir(exist_ok=True)
Path("ssl").mkdir(exist_ok=True)
Path("certificates").mkdir(exist_ok=True)

# rm -rf venv
# python3.10 -m venv venv
# source venv/bin/activate
# pip install aiohttp_socks

# /reset_bonus - сбросить бонусы
# /add - Добавить товар
# /mark_delivered - Отметка доставок
# /reviews_debug - Таблица отзывов
# /start - Старт
# /admin - все для админа
# /myid - мой id
# /clear_my_cart - корзина очищена
