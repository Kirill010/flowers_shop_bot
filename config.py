import os
from pathlib import Path

# Токен бота
BOT_TOKEN = "8201157651:AAHIA7J5_NuRHhBmEEgB1H0vuXK4DnqBucI"

# ID администратора
ADMIN_ID = 1095668090
ADMIN_ID1 = 6643553268
ADMIN_ID2 = 905582217
ADMINS = [ADMIN_ID, ADMIN_ID1, ADMIN_ID2]

# ЮKassa Live
YOOKASSA_SHOP_ID = "1037498"
YOOKASSA_SECRET_KEY = "live_jxIub1SHUSUh5F2hw_CjY2kK4a2Rc57yqHx5uSySQ34"

# Настройки вебхуков
WEBAPP_HOST = "0.0.0.0"  # Слушаем все интерфейсы
WEBAPP_PORT = 3001       # Порт для вебхуков

# Ваш домен (замените на реальный)
WEBHOOK_HOST = "yourdomain.com"
WEBHOOK_PATH = "/webhook/yookassa"
WEBHOOK_URL = f"https://{WEBHOOK_HOST}{WEBHOOK_PATH}"

# Секрет для вебхука (сгенерируйте случайную строку)
WEBHOOK_SECRET = "your_secure_secret_token_here"

# Настройки для чеков
YOOKASSA_TAX_RATE = 1
YOOKASSA_TAX_SYSTEM = 1

# Путь к базе данных
DB_PATH = "data/florist.db"

# Создаем необходимые директории
Path("data").mkdir(exist_ok=True)
Path("ssl").mkdir(exist_ok=True)
Path("certificates").mkdir(exist_ok=True)

# Информация о магазине
SHOP_INFO = {
    "name": "Цветочная лавка",
    "address": "1-й Вешняковский пр., 2А, Москва",
    "phone": "+7 (965) 230-17-29",
    "work_hours": "Ежедневно с 8:00 до 20:00"
}

# SSL настройки (для вебхуков)
SSL_CERTIFICATE = "ssl/cert.pem" if os.path.exists("ssl/cert.pem") else None
SSL_PRIVATE_KEY = "ssl/private.key" if os.path.exists("ssl/private.key") else None

# Режим разработки
DEBUG = False