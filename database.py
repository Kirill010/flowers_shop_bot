import sqlite3
import os
import json
from typing import List, Dict, Optional
from datetime import datetime, timedelta

DB_PATH = "data/florist.db"


def init_db():
    print("🔧 Инициализация базы данных...")
    os.makedirs("data", exist_ok=True)
    os.makedirs("images", exist_ok=True)

    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()

        # Создание таблиц
        tables = [
            # products table
            """
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                full_description TEXT,
                price REAL NOT NULL,
                photo TEXT,
                category TEXT NOT NULL,
                created_date DATE DEFAULT CURRENT_DATE,
                is_daily BOOLEAN DEFAULT TRUE,
                in_stock BOOLEAN DEFAULT TRUE
            )
            """,
            # cart table
            """
            CREATE TABLE IF NOT EXISTS cart (
                user_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                quantity INTEGER DEFAULT 1,
                PRIMARY KEY(user_id, product_id)
            )
            """,
            # orders table (обновленная с полями для скидок)
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                items TEXT NOT NULL,
                total REAL NOT NULL,
                customer_name TEXT NOT NULL,
                phone TEXT NOT NULL,
                address TEXT,
                delivery_date TEXT,
                delivery_time TEXT,
                payment_method TEXT,
                delivery_cost INTEGER,
                delivery_type TEXT,
                bonus_used INTEGER,
                status TEXT DEFAULT 'new',
                points_used INTEGER DEFAULT 0,
                discount_applied REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            # Таблица для системы лояльности
            """
            CREATE TABLE IF NOT EXISTS loyalty_program (
                user_id INTEGER PRIMARY KEY,
                total_spent REAL DEFAULT 0,     -- Всего потрачено
                current_bonus INTEGER DEFAULT 0, -- Доступные бонусы
                total_bonus_earned INTEGER DEFAULT 0, -- Всего начислено бонусов
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            # loyalty_history table
            """
            CREATE TABLE IF NOT EXISTS loyalty_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                order_id INTEGER,
                points_change INTEGER NOT NULL,
                reason TEXT NOT NULL,
                remaining_points INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            # Таблица для хранения платежей
            """
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payment_id TEXT UNIQUE NOT NULL,
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                currency TEXT DEFAULT 'RUB',
                status TEXT NOT NULL,
                description TEXT,
                metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            # Таблица для отслеживания попыток ввода сертификатов
            """
            CREATE TABLE IF NOT EXISTS certificate_attempts (
                user_id INTEGER NOT NULL,
                attempts INTEGER DEFAULT 0,
                last_attempt TIMESTAMP,
                blocked_until TIMESTAMP,
                PRIMARY KEY (user_id)
            )
            """,
            # Таблица для хранения сертификатов
            """
            CREATE TABLE IF NOT EXISTS certificates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                cert_code TEXT UNIQUE NOT NULL,
                payment_id TEXT NOT NULL,
                used BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            # Таблица для отзывов
            """
            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                user_name TEXT,
                text TEXT NOT NULL,
                rating INTEGER DEFAULT 5,
                order_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            # Таблица для истории изменений заказов
            """
            CREATE TABLE IF NOT EXISTS order_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (order_id) REFERENCES orders (id)
            )
            """,
            # Таблица пользователей
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                first_name TEXT,
                last_name TEXT,
                username TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        ]

        for table in tables:
            cur.execute(table)

        # Создание индексов
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_products_category ON products(category)",
            "CREATE INDEX IF NOT EXISTS idx_products_stock ON products(in_stock)",
            "CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status)",
            "CREATE INDEX IF NOT EXISTS idx_payments_payment_id ON payments(payment_id)",
            "CREATE INDEX IF NOT EXISTS idx_loyalty_user_id ON loyalty_program(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_loyalty_history_user ON loyalty_history(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_loyalty_history_order ON loyalty_history(order_id)"
        ]

        for index in indexes:
            cur.execute(index)
        # init_test_data()
        print("✅ База данных инициализирована")


def calculate_order_total(cart_items: list, delivery_cost: int, bonus_used: int = 0, user_id: int = None) -> dict:
    """Рассчитывает итоговую сумму заказа с учетом бонусов"""
    products_total = sum(item['price'] * item['quantity'] for item in cart_items)

    # Максимально можно использовать бонусов - 30% от суммы товаров (без доставки)
    max_bonus_allowed = int(products_total * 0.3)
    actual_bonus_used = min(bonus_used, max_bonus_allowed)

    # Бонусы применяются только к стоимости товаров, доставка оплачивается отдельно
    final_total = max(0, products_total - actual_bonus_used + delivery_cost)

    return {
        'products_total': products_total,
        'bonus_used': actual_bonus_used,
        'max_bonus_allowed': max_bonus_allowed,
        'final_total': final_total
    }


def calculate_bonus_from_order(total_amount: float) -> int:
    """Рассчитывает бонусы на основе суммы заказа - 10%"""
    return int(total_amount * 0.1)


def spend_bonus_points(user_id: int, points_to_spend: int) -> bool:
    """Списывает бонусные баллы"""
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT current_bonus FROM loyalty_program WHERE user_id = ?", (user_id,))
        result = cur.fetchone()

        if not result or result[0] < points_to_spend:
            return False

        new_bonus = result[0] - points_to_spend
        cur.execute("UPDATE loyalty_program SET current_bonus = ? WHERE user_id = ?", (new_bonus, user_id))
        conn.commit()
        return True


def get_bonus_info(user_id: int) -> dict:
    """Получает информацию о бонусах пользователя"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM loyalty_program WHERE user_id = ?", (user_id,))
        result = cur.fetchone()

        if not result:
            # Если пользователя нет — создаём запись с нуля
            init_user_loyalty(user_id)
            return {
                "total_spent": 0,
                "current_bonus": 0,
                "total_bonus_earned": 0
            }

        return dict(result)


def init_user_loyalty(user_id: int):
    """Инициализирует запись пользователя в программе лояльности"""
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT OR IGNORE INTO loyalty_program (user_id) 
            VALUES (?)
        """, (user_id,))
        conn.commit()


def calculate_points_from_order(total_amount: float) -> int:
    """Рассчитывает баллы на основе суммы заказа"""
    # 1 балл за каждые 50 рублей
    return int(total_amount // 50)


def add_loyalty_points(user_id: int, order_id: int, total_amount: float):
    """Начисляет баллы за заказ"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # Получаем текущие данные пользователя
        cur.execute("SELECT * FROM loyalty_program WHERE user_id = ?", (user_id,))
        loyalty_data = cur.fetchone()

        if not loyalty_data:
            init_user_loyalty(user_id)
            loyalty_data = {"total_spent": 0, "current_points": 0, "level": 1}
        else:
            loyalty_data = dict(loyalty_data)

        # Рассчитываем базовые баллы
        base_points = calculate_points_from_order(total_amount)

        # Учитываем множитель уровня
        points_earned = int(base_points)

        # Обновляем данные пользователя
        new_total_spent = loyalty_data['total_spent'] + total_amount
        new_current_points = loyalty_data['current_points'] + points_earned
        new_level = new_total_spent

        cur.execute("""
            UPDATE loyalty_program 
            SET total_spent = ?, 
                current_points = ?, 
                lifetime_points = lifetime_points + ?,
                level = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
        """, (new_total_spent, new_current_points, points_earned, new_level, user_id))

        # Добавляем запись в историю
        cur.execute("""
            INSERT INTO loyalty_history 
            (user_id, order_id, points_change, reason, remaining_points)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, order_id, points_earned, f"Начисление за заказ #{order_id}", new_current_points))

        conn.commit()

        return {
            "points_earned": points_earned,
            "total_points": new_current_points,
            "level": new_level,
        }


def spend_loyalty_points(user_id: int, order_id: int, points_to_spend: int, reason: str = "Оплата заказа"):
    """Списывает баллы"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute("SELECT current_points FROM loyalty_program WHERE user_id = ?", (user_id,))
        result = cur.fetchone()

        if not result or result['current_points'] < points_to_spend:
            return False

        new_points = result['current_points'] - points_to_spend

        cur.execute("""
            UPDATE loyalty_program 
            SET current_points = ?, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
        """, (new_points, user_id))

        cur.execute("""
            INSERT INTO loyalty_history 
            (user_id, order_id, points_change, reason, remaining_points)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, order_id, -points_to_spend, reason, new_points))

        conn.commit()
        return True


def get_loyalty_info(user_id: int) -> dict:
    """Получает информацию о программе лояльности пользователя"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute("SELECT * FROM loyalty_program WHERE user_id = ?", (user_id,))
        result = cur.fetchone()

        if not result:
            init_user_loyalty(user_id)
            return {
                "total_spent": 0,
                "current_points": 0,
                "lifetime_points": 0,
                "level": 1,
            }

        data = dict(result)
        return data


def get_loyalty_history(user_id: int, limit: int = 10) -> list:
    """Получает историю операций с баллами"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute("""
            SELECT * FROM loyalty_history 
            WHERE user_id = ? 
            ORDER BY created_at DESC 
            LIMIT ?
        """, (user_id, limit))

        return [dict(row) for row in cur.fetchall()]


def get_points_discount_value(points: int) -> float:
    """Рассчитывает денежный эквивалент баллов"""
    # 1 балл = 1 рубль
    return points * 1.0


def cleanup_old_daily_products():
    """Удаляет букеты дня, которые старше 1 дня"""
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("""
            DELETE FROM products 
            WHERE is_daily = TRUE 
            AND created_date < DATE('now')
        """)
        deleted_count = cur.rowcount
        conn.commit()
        if deleted_count > 0:
            print(f"🗑️ Удалено {deleted_count} старых букетов дня")
        return deleted_count


def add_to_cart(user_id: int, product_id: int):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            # Проверяем, есть ли уже товар в корзине
            cur.execute("SELECT quantity FROM cart WHERE user_id=? AND product_id=?", (user_id, product_id))
            row = cur.fetchone()

            if row:
                # Товар уже есть в корзине - увеличиваем количество
                cur.execute("UPDATE cart SET quantity = quantity + 1 WHERE user_id=? AND product_id=?",
                            (user_id, product_id))
            else:
                # Товара нет в корзине - добавляем с количеством 1
                cur.execute("INSERT INTO cart (user_id, product_id, quantity) VALUES (?, ?, 1)",
                            (user_id, product_id))
            conn.commit()
            return True
    except Exception as e:
        print(f"Error adding to cart: {e}")
        return False


def get_cart(user_id: int) -> List[Dict]:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("""
                SELECT p.id, p.name, p.price, c.quantity, p.in_stock
                FROM cart c 
                LEFT JOIN products p ON c.product_id = p.id 
                WHERE c.user_id=?
            """, (user_id,))

            cart_items = []
            for row in cur.fetchall():
                cart_items.append(dict(row))
            return cart_items
    except Exception as e:
        print(f"Error getting cart: {e}")
        return []


def clear_cart(user_id: int):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM cart WHERE user_id=?", (user_id,))
            conn.commit()
            return True
    except Exception as e:
        print(f"Error clearing cart: {e}")
        return False


def add_bonus_points(user_id: int, order_id: int, total_amount: float):
    """Начисляет бонусы (10% от суммы заказа)"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # Получаем текущие данные
        cur.execute("SELECT current_bonus, total_spent FROM loyalty_program WHERE user_id = ?", (user_id,))
        row = cur.fetchone()

        if not row:
            init_user_loyalty(user_id)
            current_bonus = 0
            total_spent = 0
        else:
            current_bonus = row['current_bonus']
            total_spent = row['total_spent']

        # Рассчитываем бонусы: 10% от суммы
        bonus_earned = int(total_amount * 0.1)
        new_total_spent = total_spent + total_amount
        new_current_bonus = current_bonus + bonus_earned

        # Обновляем запись
        cur.execute("""
            UPDATE loyalty_program 
            SET total_spent = ?, 
                current_bonus = ?, 
                total_bonus_earned = total_bonus_earned + ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
        """, (new_total_spent, new_current_bonus, bonus_earned, user_id))

        # Добавляем запись в историю
        cur.execute("""
            INSERT INTO loyalty_history 
            (user_id, order_id, points_change, reason, remaining_points)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, order_id, bonus_earned, f"Начисление за заказ #{order_id}", new_current_bonus))

        conn.commit()

        return {
            "bonus_earned": bonus_earned,
            "total_bonus": new_current_bonus,
            "total_spent": new_total_spent
        }


def add_product(name: str, description: str, full_description: str, price: float,
                photo: str, category: str, is_daily: bool = True) -> int:
    """Добавляет товар в базу (для букетов дня is_daily=True)"""
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO products (name, description, full_description, price, photo, category, is_daily)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (name, description, full_description, price, photo, category, is_daily))
        conn.commit()
        return cur.lastrowid


def create_order(user_id: int, name: str, phone: str, address: str,
                 delivery_date: str, delivery_time: str, payment: str,
                 delivery_cost: int = 0, delivery_type: str = "delivery",
                 bonus_used: int = 0) -> int:
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()

        # Создаем/обновляем пользователя
        cur.execute("""
            INSERT OR IGNORE INTO users (id, first_name)
            VALUES (?, ?)
        """, (user_id, name.split()[0] if name else 'Пользователь'))

        # Получаем корзину и рассчитываем сумму
        cart_items = get_cart(user_id)
        products_total = sum(item['price'] * item['quantity'] for item in cart_items)

        # Проверяем доступность бонусов
        if bonus_used > 0:
            bonus_info = get_bonus_info(user_id)
            max_bonus_allowed = int(products_total * 0.3)
            actual_bonus_used = min(bonus_used, bonus_info['current_bonus'], max_bonus_allowed)

            if actual_bonus_used < bonus_used:
                return -1  # Код ошибки - нельзя использовать столько бонусов
        else:
            actual_bonus_used = 0

        # Рассчитываем итоговую сумму
        final_total = max(0, products_total + delivery_cost - actual_bonus_used)

        # Создаем заказ
        cur.execute("""
            INSERT INTO orders 
            (user_id, items, total, customer_name, phone, address, 
             delivery_date, delivery_time, payment_method, delivery_cost, 
             delivery_type, status, bonus_used)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, json.dumps(cart_items), final_total, name, phone, address,
              delivery_date, delivery_time, payment, delivery_cost,
              delivery_type, 'new', actual_bonus_used))

        order_id = cur.lastrowid

        # Списываем бонусы, если они использовались
        if actual_bonus_used > 0:
            # Обновляем баланс бонусов
            cur.execute("""
                UPDATE loyalty_program 
                SET current_bonus = current_bonus - ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
            """, (actual_bonus_used, user_id))

            # Записываем в историю списание
            cur.execute("""
                INSERT INTO loyalty_history 
                (user_id, order_id, points_change, reason, remaining_points)
                SELECT ?, ?, -?, ?, current_bonus 
                FROM loyalty_program 
                WHERE user_id = ?
            """, (user_id, order_id, actual_bonus_used,
                  f"Списание за заказ #{order_id}", user_id))

        # Начисляем новые бонусы (10% от итоговой суммы после скидки)
        bonus_earned = int(final_total * 0.1)
        if bonus_earned > 0:
            cur.execute("""
                UPDATE loyalty_program 
                SET current_bonus = current_bonus + ?,
                    total_bonus_earned = total_bonus_earned + ?,
                    total_spent = total_spent + ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
            """, (bonus_earned, bonus_earned, final_total, user_id))

            # Записываем в историю начисление
            cur.execute("""
                INSERT INTO loyalty_history 
                (user_id, order_id, points_change, reason, remaining_points)
                SELECT ?, ?, ?, ?, current_bonus 
                FROM loyalty_program 
                WHERE user_id = ?
            """, (user_id, order_id, bonus_earned,
                  f"Начисление за заказ #{order_id}", user_id))

        cur.execute("DELETE FROM cart WHERE user_id=?", (user_id,))
        conn.commit()
        return order_id
        # Очищаем корзину
        # clear_cart(user_id)

    except:
        conn.rollback()
        return -1


def add_certificate_purchase(user_id: int, amount: int, cert_code: str, payment_id: str):
    """Сохраняет информацию о покупке сертификата"""
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO certificates (user_id, amount, cert_code, payment_id)
            VALUES (?, ?, ?, ?)
        """, (user_id, amount, cert_code, payment_id))
        conn.commit()


def get_delivered_orders(user_id: int) -> List[Dict]:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("""
                SELECT id, items, total, created_at, delivery_date 
                FROM orders 
                WHERE user_id=? AND status='delivered' 
                ORDER BY created_at DESC
            """, (user_id,))

            orders = []
            for row in cur.fetchall():
                orders.append(dict(row))
            return orders
    except Exception as e:
        print(f"Error getting delivered orders: {e}")
        return []


# database.py - улучшаем функцию обновления статуса

def update_order_status(order_id: int, status: str):
    """Обновляет статус заказа и записывает в историю"""
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()

        # Получаем текущий статус для проверки
        cur.execute("SELECT status FROM orders WHERE id = ?", (order_id,))
        current_status = cur.fetchone()

        if current_status and current_status[0] != status:
            # Обновляем статус
            cur.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))

            # Записываем в историю изменений
            cur.execute("""
                INSERT INTO order_history (order_id, status, changed_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            """, (order_id, status))

            conn.commit()
            return True
        return False


def get_user_orders(user_id: int) -> List[Dict]:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("""
                SELECT id, items, total, status, created_at, delivery_date, delivery_time
                FROM orders WHERE user_id=? ORDER BY created_at DESC
            """, (user_id,))

            orders = []
            for row in cur.fetchall():
                order = dict(row)
                if order['created_at']:
                    utc_time = datetime.strptime(order['created_at'], "%Y-%m-%d %H:%M:%S")
                    moscow_time = utc_time + timedelta(hours=3)
                    order['created_at'] = moscow_time.strftime("%Y-%m-%d %H:%M:%S")
                orders.append(order)
            return orders
    except Exception as e:
        print(f"Error getting orders: {e}")
        return []


def add_review(user_id: int, user_name: str, text: str, rating: int = 5, order_id: int = None):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO reviews (user_id, user_name, text, rating, order_id) 
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, user_name, text, rating, order_id))
        conn.commit()


def get_reviews(limit: int = 10) -> List[Dict]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT user_name, text, rating, created_at 
            FROM reviews 
            ORDER BY created_at DESC 
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in cur.fetchall()]


def check_product_availability(product_id: int) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT in_stock FROM products WHERE id=?", (product_id,))
        result = cur.fetchone()
        return result[0] if result else False


def get_available_delivery_dates():
    """Возвращает доступные даты доставки (включая сегодня)"""
    today = datetime.now()
    dates = []

    # Доставка доступна на сегодня + следующие 7 дней
    for i in range(8):
        delivery_date = today + timedelta(days=i)
        # Проверяем время - если сегодня после 15:00, то доставка на сегодня недоступна
        if i == 0 and today.hour >= 15:
            continue  # Пропускаем сегодня, если уже после 15:00

        if delivery_date.weekday() < 5:  # Только рабочие дни (пн-пт)
            dates.append(delivery_date.strftime("%d.%m.%Y"))

    return dates


def get_delivery_time_slots():
    """Возвращает доступные временные интервалы"""
    return [
        "08:00-11:00",
        "11:00-14:00",
        "14:00-17:00",
        "17:00-20:00"
    ]


def check_certificate_validity(cert_code: str) -> dict:
    """Проверяет валидность сертификата"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT * FROM certificates 
            WHERE cert_code = ? AND used = FALSE 
            AND date(created_at, '+1 year') > date('now')
        """, (cert_code,))

        result = cur.fetchone()
        return dict(result) if result else None


def mark_certificate_used(cert_code: str):
    """Помечает сертификат как использованный"""
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("UPDATE certificates SET used = TRUE WHERE cert_code = ?", (cert_code,))
        conn.commit()


def save_payment(payment_id: str, user_id: int, amount: float, status: str,
                 description: str = "", metadata: dict = None):
    """Сохранение информации о платеже"""
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT OR REPLACE INTO payments 
            (payment_id, user_id, amount, status, description, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (payment_id, user_id, amount, status, description,
              json.dumps(metadata) if metadata else None))
        conn.commit()


def update_payment_status(payment_id: str, status: str):
    """Обновление статуса платежа"""
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE payments 
            SET status = ?, updated_at = CURRENT_TIMESTAMP 
            WHERE payment_id = ?
        """, (status, payment_id))
        conn.commit()


def get_payment(payment_id: str) -> Optional[Dict]:
    """Получение информации о платеже"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM payments WHERE payment_id = ?", (payment_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def add_certificate_attempt(user_id: int):
    """Добавляет попытку ввода сертификата"""
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cur.execute("""
            INSERT OR REPLACE INTO certificate_attempts 
            (user_id, attempts, last_attempt, blocked_until)
            VALUES (?, 
                    COALESCE((SELECT attempts FROM certificate_attempts WHERE user_id = ?), 0) + 1,
                    ?,
                    CASE 
                        WHEN COALESCE((SELECT attempts FROM certificate_attempts WHERE user_id = ?), 0) + 1 >= 3 
                        THEN datetime(?, '+30 minutes')
                        ELSE NULL
                    END)
        """, (user_id, user_id, now, user_id, now))
        conn.commit()


def get_certificate_attempts(user_id: int) -> dict:
    """Получает информацию о попытках ввода"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM certificate_attempts WHERE user_id = ?", (user_id,))
        result = cur.fetchone()
        return dict(result) if result else None


def reset_certificate_attempts(user_id: int):
    """Сбрасывает счетчик попыток"""
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM certificate_attempts WHERE user_id = ?", (user_id,))
        conn.commit()


# В начало файла database.py добавить:
def init_test_data():
    """Инициализация тестовых данных"""
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()

        # Проверяем есть ли тестовый товар
        cur.execute("SELECT COUNT(*) FROM products WHERE id = 1")
        if cur.fetchone()[0] == 0:
            # Добавляем тестовый товар
            cur.execute("""
                INSERT INTO products (id, name, description, full_description, price, category, is_daily, in_stock)
                VALUES (1, 'Тестовый букет', 'Красивый тестовый букет', 'Полное описание тестового букета', 
                        2500, 'bouquet', TRUE, TRUE)
            """)
            conn.commit()
            print("✅ Тестовый товар добавлен")


def get_connection():
    """Создает подключение с правильными настройками"""
    conn = sqlite3.connect(DB_PATH, timeout=30)  # Увеличиваем timeout
    conn.execute("PRAGMA journal_mode=WAL")  # Включаем WAL mode для лучшей параллельности
    return conn
