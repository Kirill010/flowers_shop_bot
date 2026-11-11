"""«Лёгкость»: сколько нажатий и сообщений нужно клиенту, не теряется ли он, не перегружены ли экраны.

Эти проверки — не про ошибки, а про удобство: если кто-то в будущем добавит лишний шаг или десятую кнопку в ряд,
тест об этом скажет. Плюс обновление базы со старой версии и мелкие помощники для кнопок времени."""
from __future__ import annotations

import dataclasses
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from aiogram.methods import DeleteMessage, EditMessageText, SendDocument, SendMessage, SendPhoto
from aiogram.types import InlineKeyboardMarkup

from flowerbot import catalog, db as db_module, schedule, texts
from flowerbot.callbacks import Prod
from flowerbot.config import ConfigError, ROOT
from flowerbot.util import human_day, rub, slot_short, when_label

from .harness import Harness
from .helpers import ADMIN, CUSTOMER, TODAY, make_db, real_shop

TOMORROW = TODAY + timedelta(days=1)
CODE = "🏷 Промокод или сертификат"


class UxCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = Harness()
        self.addCleanup(self.h.close)


class PurchaseBudgetTests(UxCase):
    async def test_first_purchase_is_ten_taps_and_eight_messages(self):
        """Новый клиент от «/start» до принятого заказа: самовывоз, оплата при получении."""
        h, u = self.h, CUSTOMER
        taps, started = 0, len(h.session.calls)

        async def act(coro):
            nonlocal taps
            taps += 1
            return await coro

        await act(h.say(u, "/start"))
        await act(h.say(u, texts.BTN_CATALOG))
        await act(h.press(u, "Мини ·", prefix=True))
        await act(h.press(u, "🛒 В корзину"))
        await act(h.press(u, "🛒 Корзина ·", prefix=True))
        await act(h.press(u, "✅ Оформить заказ"))
        await act(h.contact(u, "79175494941", first_name="Мария"))
        await act(h.press(u, "🛍 Самовывоз"))
        await act(h.press(u, "⚡ Как можно скорее"))
        await act(h.press(u, "💵 Оплата при получении"))

        mine = [c for c in h.session.calls[started:] if isinstance(c, SendMessage) and c.chat_id == u]
        self.assertEqual(h.db.scalar("SELECT COUNT(*) FROM orders"), 1, "заказ оформлен")
        self.assertLessEqual(taps, 10, "нажатий")
        self.assertLessEqual(len(mine), 8, f"новых сообщений от бота: {[m.text[:30] for m in mine]}")
        self.assertLessEqual(sum(len(m.text) for m in mine), 2000, "знаков, которые клиенту надо прочитать")
        self.assertLessEqual(max(len(m.text) for m in mine), 400, "самое длинное сообщение")

    async def test_returning_customer_orders_in_nine_taps(self):
        """Постоянный клиент: каталог → карточка → в корзину → корзина → оформить → «всё верно» → самовывоз → время → оплата."""
        h, u = self.h, CUSTOMER
        await h.tap(u, Prod(act="add", sku="mini").pack())
        await h.say(u, texts.BTN_CART)
        await h.press(u, "✅ Оформить заказ")
        await h.contact(u, "79175494941", first_name="Мария")
        await h.press(u, "🛍 Самовывоз")
        await h.press(u, "⚡ Как можно скорее")
        await h.press(u, "💵 Оплата при получении")
        started, taps = len(h.session.calls), 0
        for coro in (h.say(u, texts.BTN_CATALOG), h.press(u, "Классика ·", prefix=True), h.press(u, "🛒 В корзину"),
                     h.press(u, "🛒 Корзина ·", prefix=True), h.press(u, "✅ Оформить заказ"),
                     h.press(u, "✅ Да, всё верно"), h.press(u, "🛍 Самовывоз"), h.press(u, "⚡ Как можно скорее"),
                     h.press(u, "💵 Оплата при получении")):
            taps += 1
            await coro
        self.assertEqual(h.db.scalar("SELECT COUNT(*) FROM orders"), 2)
        self.assertLessEqual(taps, 9)
        mine = [c for c in h.session.calls[started:] if isinstance(c, SendMessage) and c.chat_id == u]
        self.assertLessEqual(len(mine), 6)

    async def test_browsing_the_whole_catalog_adds_no_new_messages(self):
        h, u = self.h, CUSTOMER
        await h.say(u, texts.BTN_CATALOG)
        started = len(h.session.calls)
        for label, prefix in (("▶", False), ("▶", False), ("🌱 Растения", False), ("Орхидея", True), ("▶", False),
                              ("▶", False), ("◀", False), ("📖 Подробнее", False), ("📖 Свернуть", False),
                              ("⬅️ Назад к списку", False), ("💐 Букеты", False), ("Мини ·", True), ("🛒 В корзину", False),
                              ("🛒 Корзина ·", True), ("🌸 Добавить ещё", False)):
            await h.press(u, label, prefix=prefix)
        for kind in (SendMessage, SendPhoto, DeleteMessage, SendDocument):
            self.assertEqual(h.count(kind, h.session.calls[started:]), 0, kind.__name__)
        self.assertGreaterEqual(h.count(EditMessageText, h.session.calls[started:]), 10)

    async def test_staff_order_card_is_short_and_has_the_next_step_first(self):
        h = self.h
        await h.tap(CUSTOMER, Prod(act="add", sku="classic").pack())
        await h.say(CUSTOMER, texts.BTN_CART)
        await h.press(CUSTOMER, "✅ Оформить заказ")
        await h.contact(CUSTOMER, "79175494941", first_name="Мария")
        await h.press(CUSTOMER, "🛍 Самовывоз")
        await h.press(CUSTOMER, "Завтра 9–11")
        calls = await h.press(CUSTOMER, "🔗 Оплатить переводом")
        card = h.sent_to(ADMIN, calls)[0]
        self.assertLess(len(card), 650)
        self.assertIn("Ждём предоплату", card)
        self.assertEqual(h.screen_labels([c for c in calls if getattr(c, "chat_id", None) == ADMIN])[0], "✅ Принять")


class ScreenLimitsTests(UxCase):
    async def tour(self):
        """Заходит на все основные экраны клиента и сотрудника."""
        h, u = self.h, CUSTOMER
        await h.say(u, "/start")
        await h.say(u, texts.BTN_CATALOG)
        for sku in [p.sku for p in catalog.list_products(h.db)]:
            await h.tap(u, Prod(act="more", sku=sku).pack())
        await h.press(u, "🌱 Растения")
        await h.press(u, "💐 Букеты")
        await h.press(u, "▶")
        for sku in ("roses51", "chrysanthemum", "musical"):
            await h.tap(u, Prod(act="add", sku=sku).pack())
        await h.say(u, texts.BTN_CART)
        await h.press(u, "✅ Оформить заказ")
        await h.contact(u, "79175494941", first_name="Мария")
        await h.press(u, "🚚 Доставка")
        await h.say(u, "Москва, Тверская 1, кв. 5")
        await h.press(u, "🗓 Другая дата")
        await h.press(u, "Завтра, 10.11")
        await h.press(u, "09:00–11:00")
        await h.say(u, "Земфира — Хочешь?")
        await h.press(u, "✏️ Изменить или добавить")
        await h.press(u, "⬅️ Назад к заказу")
        await h.press(u, "🔗 Оплатить переводом")  # на другой день — только предоплата
        for button in (texts.BTN_ORDERS, texts.BTN_MANAGER, texts.BTN_DELIVERY, texts.BTN_CERT, texts.BTN_MAP,
                       texts.BTN_REVIEWS, texts.BTN_CART):
            await h.say(u, button)
        for page in ("🚚 Условия доставки", "💳 Способы оплаты", "🛍 Самовывоз"):
            await h.say(u, texts.BTN_DELIVERY)
            await h.press(u, page)
        await h.say(ADMIN, texts.BTN_PANEL)
        for step in ("📦 Новые", "⬅️ В панель", "📬 Обращения", "⬅️ В панель", "🛒 Наличие", "⬅️ В панель", "⚙️ Ещё",
                     "📷 Фото товаров", "⬅️ Назад", "📊 Статистика", "⬅️ В панель"):
            try:
                await h.press(ADMIN, step, prefix=True)
            except AssertionError:
                pass

    async def test_every_screen_is_light_enough(self):
        await self.tour()
        calls = self.h.session.calls
        screens = 0
        for call in calls:
            markup = getattr(call, "reply_markup", None)
            if not isinstance(markup, InlineKeyboardMarkup):
                continue
            screens += 1
            rows = markup.inline_keyboard
            buttons = [b for row in rows for b in row]
            # списки «наличие» и «фото товаров» у сотрудников — по кнопке на каждый из 20 товаров; для клиентов — не больше 16
            picker = all((b.callback_data or "").startswith(("ad:toggle", "ad:photo", "ad:panel", "ad:more")) for b in buttons)
            self.assertLessEqual(len(buttons), 24 if picker else 16, [b.text for b in buttons])
            self.assertLessEqual(len(rows), 24 if picker else 13, [b.text for b in buttons])
            for row in rows:
                is_cart_row = {"➖", "➕"} <= {b.text for b in row}
                self.assertLessEqual(len(row), 4 if is_cart_row else 3, [b.text for b in row])
            for b in buttons:
                self.assertLessEqual(len(b.text), 42, b.text)
                if b.callback_data:
                    self.assertLessEqual(len(b.callback_data.encode()), 64, b.callback_data)
        self.assertGreater(screens, 60, "прогулка прошла по многим экранам")
        for text in self.h.texts(calls):
            self.assertLessEqual(len(text), 1000, text[:60])

    async def test_main_menu_is_four_rows_of_two(self):
        from flowerbot import keyboards as kb
        rows = kb.main_menu(False).keyboard
        self.assertEqual([len(r) for r in rows], [2, 2, 2, 2])
        self.assertEqual([len(r) for r in kb.main_menu(True).keyboard], [2, 2, 2, 2, 1])

    async def test_every_card_fits_under_a_photo_in_every_month(self):
        h = self.h
        for product in catalog.list_products(h.db):
            for month in range(1, 13):
                self.assertLessEqual(len(texts.product_details(product, month)), 1024, (product.sku, month))
                self.assertLessEqual(len(texts.product_card(product, month)), 400, (product.sku, month))


class NoTrapsTests(UxCase):
    """Любая кнопка меню выводит из любого шага, где бот ждёт текст: название кнопки не становится ответом."""

    async def enter(self, name: str):
        h, u = self.h, CUSTOMER
        if name in ("address", "custom_date", "comment", "code", "recipient"):
            await h.tap(u, Prod(act="add", sku="mini").pack())
            await h.say(u, texts.BTN_CART)
            await h.press(u, "✅ Оформить заказ")
            await h.contact(u, "79175494941", first_name="Мария")
            if name == "address":
                await h.press(u, "🚚 Доставка")
            elif name == "custom_date":
                await h.press(u, "🛍 Самовывоз")
                await h.press(u, "🗓 Другая дата")
                await h.press(u, "📅 Ввести дату")
            else:
                await h.press(u, "🚚 Доставка")
                await h.say(u, "Москва, Тверская 1, кв. 5")
                await h.press(u, "Сегодня 15–17")
                await h.press(u, "✏️ Изменить или добавить")
                await h.press(u, {"comment": "💌 Открытка", "code": CODE, "recipient": "🎁 Получатель"}[name])
        elif name == "manager":
            await h.say(u, texts.BTN_MANAGER)
            await h.press(u, "❓ Задать вопрос")
        elif name == "budget":
            await h.say(u, texts.BTN_CATALOG)
            await h.press(u, "💰 Подбор под бюджет")
        elif name == "cert_amount":
            await h.say(u, texts.BTN_CERT)
            await h.press(u, "✏️ Другая сумма")
        elif name == "cert_balance":
            await h.say(u, texts.BTN_CERT)
            await h.press(u, "🔑 Проверить баланс")
        elif name == "review":
            await h.say(u, texts.BTN_REVIEWS)
            await h.press(u, "✍️ Оставить отзыв")
            await h.press(u, "⭐⭐⭐⭐⭐")
        elif name == "phone":
            await h.say(u, texts.BTN_MANAGER)
            await h.press(u, "📞 Оставить номер")

    async def test_menu_button_escapes_every_typing_step(self):
        steps = ("address", "custom_date", "comment", "code", "recipient", "manager", "budget", "cert_amount",
                 "cert_balance", "review", "phone")
        for name in steps:
            with self.subTest(step=name):
                h = Harness()
                self.addCleanup(h.close)
                self.h = h
                await self.enter(name)
                calls = await h.say(CUSTOMER, texts.BTN_MAP)
                self.assertIn("Мы на карте", h.last_text(calls), name)
                calls = await h.say(CUSTOMER, "просто слова после выхода")
                self.assertIn("для менеджера", h.last_text(calls), f"{name}: бот снова ждёт ответ на старый вопрос")
                self.assertEqual(h.db.scalar("SELECT COUNT(*) FROM leads"), 0, name)
                self.assertEqual(h.db.scalar("SELECT COUNT(*) FROM orders"), 0, name)
                self.assertEqual(h.db.scalar("SELECT COUNT(*) FROM reviews"), 0, name)


class NearestOptionsTests(unittest.TestCase):
    def setUp(self):
        self.shop = real_shop()

    def local(self, hour: int, minute: int = 0, day: int = 9) -> datetime:
        return datetime(2026, 11, day, hour, minute, tzinfo=self.shop.tz)

    def test_pickup_at_noon(self):
        self.assertEqual(schedule.nearest_options(self.shop, "pickup", self.local(12)),
                         [(date(2026, 11, 9), schedule.ASAP), (date(2026, 11, 9), "11:00–13:00"),
                          (date(2026, 11, 10), "09:00–11:00")])

    def test_delivery_at_noon_keeps_the_two_hour_lead(self):
        self.assertEqual(schedule.nearest_options(self.shop, "delivery", self.local(12)),
                         [(date(2026, 11, 9), "15:00–17:00"), (date(2026, 11, 9), "17:00–19:00"),
                          (date(2026, 11, 10), "09:00–11:00")])

    def test_pickup_late_in_the_evening(self):
        self.assertEqual(schedule.nearest_options(self.shop, "pickup", self.local(20, 30)),
                         [(date(2026, 11, 9), schedule.ASAP), (date(2026, 11, 9), "19:00–21:00"),
                          (date(2026, 11, 10), "09:00–11:00")])

    def test_after_closing_everything_moves_to_the_next_days(self):
        options = schedule.nearest_options(self.shop, "pickup", self.local(21, 30))
        self.assertEqual(options, [(date(2026, 11, 10), "09:00–11:00"), (date(2026, 11, 10), "11:00–13:00"),
                                   (date(2026, 11, 11), "09:00–11:00")])

    def test_delivery_in_the_evening_has_nothing_today(self):
        options = schedule.nearest_options(self.shop, "delivery", self.local(19))
        self.assertTrue(all(day > date(2026, 11, 9) for day, _ in options))
        self.assertEqual(len(options), 3)

    def test_every_option_is_valid_for_the_checkout_check(self):
        for hour in range(0, 24):
            local = self.local(hour, 15)
            for method in ("pickup", "delivery"):
                for day, label in schedule.nearest_options(self.shop, method, local):
                    self.assertTrue(schedule.slot_valid(self.shop, method, day, local, label), (hour, method, day, label))

    def test_count_and_short_horizon(self):
        self.assertEqual(len(schedule.nearest_options(self.shop, "pickup", self.local(12), count=1)), 1)
        short = dataclasses.replace(self.shop, max_days_ahead=0)
        self.assertEqual(schedule.nearest_options(short, "pickup", self.local(12)),
                         [(date(2026, 11, 9), schedule.ASAP), (date(2026, 11, 9), "11:00–13:00")])
        self.assertEqual(schedule.nearest_options(short, "pickup", self.local(21, 30)), [])


class LabelHelpersTests(unittest.TestCase):
    def test_slot_short(self):
        self.assertEqual(slot_short("11:00–13:00"), "11–13")
        self.assertEqual(slot_short("09:00–11:00"), "9–11")
        self.assertEqual(slot_short("11:00-13:00"), "11–13", "дефис вместо тире тоже понимаем")
        self.assertEqual(slot_short("09:30–11:00"), "09:30–11:00", "не круглые границы показываем как есть")
        self.assertEqual(slot_short(schedule.ASAP), "скорее")
        self.assertEqual(slot_short("вечером"), "вечером")

    def test_when_label(self):
        self.assertEqual(when_label(TODAY, schedule.ASAP, TODAY), "⚡ Как можно скорее")
        self.assertEqual(when_label(TODAY, "15:00–17:00", TODAY), "Сегодня 15–17")
        self.assertEqual(when_label(TOMORROW, "09:00–11:00", TODAY), "Завтра 9–11")
        self.assertEqual(when_label(date(2026, 11, 13), "09:00–11:00", TODAY), "Пт, 13.11 9–11")

    def test_human_day(self):
        self.assertEqual(human_day(TODAY, TODAY), "Сегодня")
        self.assertEqual(human_day(TOMORROW, TODAY), "Завтра")
        self.assertEqual(human_day(date(2026, 11, 14), TODAY), "Сб, 14 ноября")
        self.assertEqual(human_day(date(2026, 12, 31), TODAY), "Чт, 31 декабря")


class CatalogUxTests(unittest.TestCase):
    def setUp(self):
        self.db = make_db()

    def test_only_the_musical_bouquet_has_a_question(self):
        prompts = {p.sku: p.note_prompt for p in catalog.list_products(self.db)}
        self.assertIn("песню", prompts["musical"])
        self.assertEqual([sku for sku, text in prompts.items() if text], ["musical"])

    def test_note_prompt_and_card_size_are_validated(self):
        base = {"sku": "a1", "category": "bouquet", "name": "A", "price": 100}
        catalog.parse_catalog({"product": [{**base, "note_prompt": "Какая песня?"}]})
        with self.assertRaises(ConfigError):
            catalog.parse_catalog({"product": [{**base, "note_prompt": "я" * 161}]})
        with self.assertRaises(ConfigError):  # всё вместе не помещается в подпись к фото
            catalog.parse_catalog({"product": [{**base, "short": "я" * 90, "description": "я" * 650, "care": "я" * 300}]})

    def test_neighbours_go_round_in_a_circle(self):
        prev_p, next_p, index, total = catalog.neighbors(self.db, "mini")
        self.assertEqual((prev_p.sku, next_p.sku, index, total), ("premium", "classic", 1, 12))
        prev_p, next_p, index, total = catalog.neighbors(self.db, "premium")
        self.assertEqual((prev_p.sku, next_p.sku, index, total), ("author", "mini", 12, 12))
        self.assertEqual(catalog.neighbors(self.db, "orchid")[2:], (1, 8), "у растений свой круг")
        self.assertIsNone(catalog.neighbors(self.db, "nonexistent"))

    def test_hidden_product_has_no_neighbours(self):
        items = catalog.load_catalog_file(ROOT / "catalog.toml")
        catalog.sync_catalog(self.db, items[:5], ROOT / "photos")
        self.assertIsNone(catalog.neighbors(self.db, "orchid"))

    def test_resync_updates_the_question_but_keeps_the_stop_list(self):
        catalog.set_in_stock(self.db, "musical", False)
        items = catalog.load_catalog_file(ROOT / "catalog.toml")
        for item in items:
            if item["sku"] == "musical":
                item["note_prompt"] = "Новая подсказка"
        catalog.sync_catalog(self.db, items, ROOT / "photos")
        product = catalog.get_product(self.db, "musical")
        self.assertEqual(product.note_prompt, "Новая подсказка")
        self.assertFalse(product.in_stock)


class MigrationTests(unittest.TestCase):
    """У владельца уже может быть база первой версии (без подсказки к товару): она должна обновиться сама, ничего не потеряв."""

    def make_v1(self, path: Path) -> None:
        schema_v1 = db_module.SCHEMA.replace("badge TEXT NOT NULL DEFAULT '', note_prompt TEXT NOT NULL DEFAULT '',",
                                             "badge TEXT NOT NULL DEFAULT '',")
        self.assertNotEqual(schema_v1, db_module.SCHEMA, "в первой версии этого столбца не было")
        conn = sqlite3.connect(path)
        conn.executescript(schema_v1)
        conn.execute("PRAGMA user_version = 1")
        conn.execute("INSERT INTO users (user_id, name, phone, created_at, last_seen) VALUES (7, 'Мария', '+79175494941', 'x', 'x')")
        conn.execute("INSERT INTO products (sku, category, name, price, sort) VALUES ('classic', 'bouquet', 'Классика', 1990, 2)")
        conn.execute("INSERT INTO orders (user_id, pay_method, name, phone, method, date, slot, goods, total, created_at, updated_at) "
                     "VALUES (7, 'cash', 'Мария', '+79175494941', 'pickup', '2026-11-09', '11:00–13:00', 1990, 1990, 'x', 'x')")
        conn.commit()
        conn.close()

    def test_old_database_is_upgraded_in_place(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / "shop.db"
            self.make_v1(path)
            db = db_module.Database(path)
            try:
                self.assertEqual(db.scalar("PRAGMA user_version"), db_module.SCHEMA_VERSION)
                self.assertEqual(db.scalar("SELECT note_prompt FROM products WHERE sku = 'classic'"), "")
                self.assertEqual(db.scalar("SELECT COUNT(*) FROM orders"), 1, "заказы на месте")
                self.assertEqual(db.scalar("SELECT name FROM users WHERE user_id = 7"), "Мария", "клиенты на месте")
                items = catalog.load_catalog_file(ROOT / "catalog.toml")
                catalog.sync_catalog(db, items, ROOT / "photos")  # и обычная синхронизация каталога работает
                self.assertIn("песню", catalog.get_product(db, "musical").note_prompt)
            finally:
                db.close()
            db = db_module.Database(path)  # повторное открытие ничего не ломает
            try:
                self.assertEqual(db.scalar("PRAGMA user_version"), db_module.SCHEMA_VERSION)
                self.assertEqual(db.scalar("SELECT COUNT(*) FROM orders"), 1)
            finally:
                db.close()

    def test_new_database_gets_the_current_schema_at_once(self):
        db = db_module.Database(":memory:")
        self.assertEqual(db.scalar("PRAGMA user_version"), db_module.SCHEMA_VERSION)
        columns = {row["name"] for row in db.all("PRAGMA table_info(products)")}
        self.assertIn("note_prompt", columns)


if __name__ == "__main__":
    unittest.main()
