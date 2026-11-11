"""Бот глазами клиента: старт, каталог, корзина, справочные разделы, связь с менеджером."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from aiogram.methods import CopyMessage, DeleteMessage, EditMessageReplyMarkup, EditMessageText, SendMessage
from aiogram.types import Chat, InlineKeyboardButton, InlineKeyboardMarkup, Message

from flowerbot import catalog, crm, keyboards as kb, texts
from flowerbot.callbacks import Cat, Prod
from flowerbot.handlers.catalog import is_expanded
from flowerbot.util import rub

from .harness import Harness
from .helpers import ADMIN, CUSTOMER, OTHER


class BotCase(unittest.IsolatedAsyncioTestCase):
    admin_ids = (ADMIN,)

    async def asyncSetUp(self):
        self.h = Harness(admin_ids=self.admin_ids)
        self.addCleanup(self.h.close)

    async def open_catalog(self, uid: int = CUSTOMER):
        return await self.h.say(uid, texts.BTN_CATALOG)


class StartAndMenuTests(BotCase):
    async def test_start_shows_short_greeting_and_menu(self):
        calls = await self.h.say(CUSTOMER, "/start")
        text = self.h.last_text(calls)
        for needle in ("MuzFlowers", "Земляной Вал", "8:00–21:00", "просто напишите"):
            self.assertIn(needle, text)
        self.assertLess(len(text), 400, "приветствие короткое")
        self.assertEqual(set(self.h.reply_labels()), texts.CUSTOMER_BUTTONS)
        self.assertEqual(len(texts.CUSTOMER_BUTTONS), 8)
        self.assertIsNotNone(crm.get_user(self.h.db, CUSTOMER), "клиент запомнен")

    async def test_staff_get_an_extra_panel_button_and_customers_do_not(self):
        await self.h.say(CUSTOMER, "/start")
        self.assertNotIn(texts.BTN_PANEL, self.h.reply_labels())
        await self.h.say(ADMIN, "/start")
        self.assertEqual(set(self.h.reply_labels()), texts.MENU_BUTTONS)
        self.assertIn(texts.BTN_PANEL, self.h.reply_labels())

    async def test_every_menu_button_answers(self):
        for button in sorted(texts.MENU_BUTTONS):  # сотрудник видит все девять
            calls = await self.h.say(ADMIN, button)
            self.assertTrue(self.h.texts(calls), f"кнопка {button} промолчала")

    async def test_unknown_text_can_be_passed_to_the_manager_with_one_tap(self):
        calls = await self.h.say(CUSTOMER, "а розы красные есть?")
        self.assertIn("для менеджера", self.h.last_text(calls))
        self.assertTrue(self.h.has_button("✅ Отправить менеджеру", calls))
        self.assertEqual(crm.list_leads(self.h.db), [], "пока только спросили — ничего не отправлено")
        calls = await self.h.press(CUSTOMER, "✅ Отправить менеджеру")
        staff = self.h.sent_to(ADMIN, calls)
        self.assertTrue(staff and "красные" in staff[0], staff)
        self.assertIn("Передали менеджеру", self.h.last_text(calls))
        calls = await self.h.press(CUSTOMER, "✅ Отправить менеджеру")  # второе нажатие ничего не дублирует
        self.assertIn("уже отправлено", self.h.alert(calls).text)
        self.assertEqual(len(crm.list_leads(self.h.db)), 1)

    async def test_declining_the_offer_removes_it_and_sends_nothing(self):
        await self.h.say(CUSTOMER, "ничего особенного")
        calls = await self.h.press(CUSTOMER, "Нет, спасибо")
        self.assertEqual(self.h.count(DeleteMessage, calls), 1)
        self.assertEqual(crm.list_leads(self.h.db), [])

    async def test_one_symbol_is_not_met_with_silence(self):
        calls = await self.h.say(CUSTOMER, "?")
        self.assertIn("Выберите раздел", self.h.last_text(calls))
        self.assertEqual(set(self.h.reply_labels()), texts.CUSTOMER_BUTTONS)

    async def test_unknown_command_gets_a_hint(self):
        self.assertIn("Такой команды нет", self.h.last_text(await self.h.say(CUSTOMER, "/whatever")))

    async def test_non_text_content_gets_a_hint(self):
        calls = await self.h.contact(CUSTOMER, "79175494941")  # контакт вне диалога
        self.assertTrue(self.h.texts(calls))

    async def test_myid(self):
        self.assertIn(str(CUSTOMER), self.h.last_text(await self.h.say(CUSTOMER, "/myid")))
        self.assertIn("администратор", self.h.last_text(await self.h.say(ADMIN, "/myid")))

    async def test_stale_buttons_get_a_polite_alert(self):
        calls = await self.h.tap(CUSTOMER, "zzz:garbage")
        self.assertIn("устарела", self.h.alert(calls).text)


class MediaToManagerTests(BotCase):
    """Фото «хочу такой букет», голосовое или файл — не тупик: можно передать менеджеру одним нажатием."""

    def copies(self, calls):
        return [(c.chat_id, c.from_chat_id) for c in calls if isinstance(c, CopyMessage)]

    async def test_photo_is_offered_to_the_manager_and_copied_on_confirm(self):
        calls = await self.h.photo(CUSTOMER, "хочу такой же, только розовый")
        self.assertIn("для менеджера", self.h.last_text(calls))
        self.assertEqual(self.copies(calls), [], "пока только спросили — ничего не отправлено")
        calls = await self.h.press(CUSTOMER, "✅ Отправить менеджеру")
        staff = self.h.sent_to(ADMIN, calls)
        self.assertTrue(staff and "Клиент прислал фото" in staff[0] and "хочу такой же" in staff[0], staff)
        self.assertEqual(self.copies(calls), [(ADMIN, CUSTOMER)])
        self.assertIn("Передали менеджеру", self.h.last_text(calls))
        calls = await self.h.press(CUSTOMER, "✅ Отправить менеджеру")  # второе нажатие ничего не дублирует
        self.assertEqual(self.copies(calls), [])
        self.assertEqual(len(crm.list_leads(self.h.db)), 1)

    async def test_album_asks_once_and_passes_every_photo(self):
        calls = await self.h.photo(CUSTOMER, "вот такие", media_group_id="album1")
        self.assertEqual(self.h.count(SendMessage, calls), 1)
        for _ in range(2):
            calls = await self.h.photo(CUSTOMER, media_group_id="album1")
            self.assertEqual(calls, [], "остальные фото альбома — молча, вопрос не повторяется")
        calls = await self.h.press(CUSTOMER, "✅ Отправить менеджеру")
        self.assertEqual(self.copies(calls), [(ADMIN, CUSTOMER)] * 3)
        self.assertEqual(len(crm.list_leads(self.h.db)), 1)

    async def test_declining_sends_nothing(self):
        await self.h.photo(CUSTOMER, "ой, не то")
        calls = await self.h.press(CUSTOMER, "Нет, спасибо")
        self.assertEqual(self.copies(calls), [])
        self.assertEqual(crm.list_leads(self.h.db), [])

    async def test_voice_and_files_are_offered_too(self):
        calls = await self.h.other(CUSTOMER, voice={"file_id": "V", "file_unique_id": "v", "duration": 3})
        self.assertIn("для менеджера", self.h.last_text(calls))
        calls = await self.h.press(CUSTOMER, "✅ Отправить менеджеру")
        self.assertIn("голосовое", self.h.sent_to(ADMIN, calls)[0])
        self.assertEqual(self.copies(calls), [(ADMIN, CUSTOMER)])

    async def test_a_text_after_a_photo_does_not_drag_the_old_photo_along(self):
        await self.h.photo(CUSTOMER, "не отправляю")
        await self.h.say(CUSTOMER, "а розы красные есть?")
        calls = await self.h.press(CUSTOMER, "✅ Отправить менеджеру")
        self.assertEqual(self.copies(calls), [])
        self.assertIn("красные", self.h.sent_to(ADMIN, calls)[0])

    async def test_hourly_limit_applies_to_photos_too(self):
        for _ in range(6):
            await self.h.photo(CUSTOMER, "ещё фото")
            calls = await self.h.press(CUSTOMER, "✅ Отправить менеджеру")
        self.assertEqual(len(crm.list_leads(self.h.db, limit=50)), 5)
        self.assertEqual(self.copies(calls), [], "сверх лимита ничего не пересылаем")
        self.assertIn("уже получили", self.h.last_text(calls))

    async def test_photos_go_to_the_orders_chat_when_there_is_one(self):
        h = Harness(orders_chat_id=-1001234567890)
        self.addCleanup(h.close)
        await h.photo(CUSTOMER, "хочу такой")
        calls = await h.press(CUSTOMER, "✅ Отправить менеджеру")
        self.assertEqual([c.chat_id for c in calls if isinstance(c, CopyMessage)], [-1001234567890])

    async def test_staff_photo_gets_a_hint_instead_of_a_question(self):
        calls = await self.h.photo(ADMIN, "просто так")
        self.assertIn("Фото товаров", self.h.last_text(calls))
        self.assertFalse(self.h.has_button("✅ Отправить менеджеру", calls))

    async def test_stickers_and_other_content_get_a_hint_not_silence(self):
        calls = await self.h.other(CUSTOMER, sticker={"file_id": "S", "file_unique_id": "s", "type": "regular", "width": 5,
                                                      "height": 5, "is_animated": False, "is_video": False})
        self.assertIn("Выберите раздел", self.h.last_text(calls))
        self.assertEqual(set(self.h.reply_labels()), texts.CUSTOMER_BUTTONS)


class CatalogTests(BotCase):
    async def test_catalog_is_one_short_list_not_a_wall_of_cards(self):
        calls = await self.open_catalog()
        self.assertEqual(self.h.count(SendMessage, calls), 1)
        labels = self.h.screen_labels(calls)
        self.assertEqual(labels[:2], ["• 💐 Букеты", "🌱 Растения"])
        self.assertEqual(len(self.h.find("p:open:", calls)), kb.LIST_PAGE)
        self.assertIn("Мини · " + rub(990), labels)
        self.assertIn("1 из 2", labels)
        self.assertIn("💰 Подбор под бюджет", labels)
        self.assertFalse(any(label.startswith("🛒 Корзина") for label in labels), "корзина пуста — кнопки корзины нет")
        self.assertLess(len(self.h.last_text(calls)), 250)

    async def test_pages_and_tabs_change_the_same_message(self):
        await self.open_catalog()
        calls = await self.h.press(CUSTOMER, "▶")
        self.assertEqual((self.h.count(SendMessage, calls), self.h.count(EditMessageText, calls)), (0, 1))
        self.assertTrue(self.h.has_button("Премиум ·", calls, prefix=True))
        self.assertTrue(self.h.has_button("2 из 2", calls))
        calls = await self.h.press(CUSTOMER, "▶")  # по кругу — снова первая страница
        self.assertTrue(self.h.has_button("Мини ·", calls, prefix=True))
        calls = await self.h.press(CUSTOMER, "🌱 Растения")
        self.assertEqual((self.h.count(SendMessage, calls), self.h.count(EditMessageText, calls)), (0, 1))
        self.assertIn("Горшечные растения", self.h.last_text(calls))
        self.assertTrue(self.h.has_button("• 🌱 Растения", calls))

    async def test_card_opens_in_place_with_navigation(self):
        await self.open_catalog()
        calls = await self.h.press(CUSTOMER, "Мини ·", prefix=True)
        self.assertEqual((self.h.count(SendMessage, calls), self.h.count(EditMessageText, calls)), (0, 1))
        text = self.h.last_text(calls)
        self.assertIn("Мини", text)
        self.assertIn(rub(990), text)
        self.assertLess(len(text), 300, "карточка короткая: цена и главное")
        for label in ("🛒 В корзину", "📖 Подробнее", "◀", "1 из 12", "▶", "⬅️ Назад к списку"):
            self.assertTrue(self.h.has_button(label, calls), label)

    async def test_arrows_walk_through_neighbours_in_a_circle(self):
        await self.open_catalog()
        await self.h.press(CUSTOMER, "Мини ·", prefix=True)
        calls = await self.h.press(CUSTOMER, "▶")
        self.assertIn("Классика", self.h.last_text(calls))
        self.assertTrue(self.h.has_button("2 из 12", calls))
        calls = await self.h.press(CUSTOMER, "◀")
        self.assertIn("Мини", self.h.last_text(calls))
        calls = await self.h.press(CUSTOMER, "◀")  # с первого — на последний
        self.assertIn("Премиум", self.h.last_text(calls))
        self.assertTrue(self.h.has_button("12 из 12", calls))

    async def test_details_show_composition_and_collapse_back(self):
        await self.open_catalog()
        await self.h.press(CUSTOMER, "Классика ·", prefix=True)
        calls = await self.h.press(CUSTOMER, "📖 Подробнее")
        text = self.h.last_text(calls)
        self.assertIn("Состав", text)
        self.assertIn("• 7 роз", text)
        for label in ("📖 Свернуть", "💬 Вопрос менеджеру", "🛒 В корзину"):
            self.assertTrue(self.h.has_button(label, calls), label)
        calls = await self.h.press(CUSTOMER, "📖 Свернуть")
        self.assertNotIn("Состав", self.h.last_text(calls))
        self.assertEqual(self.h.count(SendMessage, calls), 0)

    async def test_question_from_a_card_goes_to_staff_with_the_product_name(self):
        await self.open_catalog()
        await self.h.press(CUSTOMER, "Классика ·", prefix=True)
        await self.h.press(CUSTOMER, "📖 Подробнее")
        await self.h.press(CUSTOMER, "💬 Вопрос менеджеру")
        calls = await self.h.say(CUSTOMER, "Какой высоты розы?")
        staff = self.h.sent_to(ADMIN, calls)
        self.assertTrue(staff and "«Классика»" in staff[0] and "Какой высоты" in staff[0], staff)

    async def test_plants_have_care_and_ask_price(self):
        await self.open_catalog()
        calls = await self.h.press(CUSTOMER, "🌱 Растения")
        items = self.h.find("p:open:", calls)
        self.assertEqual(len(items), 8)
        self.assertTrue(all("по запросу" in b.text for b in items), [b.text for b in items])
        calls = await self.h.press(CUSTOMER, "Орхидея", prefix=True)
        self.assertTrue(self.h.has_button("🔎 Цена и наличие", calls))
        self.assertFalse(self.h.has_button("🛒 В корзину", calls), "растение без цены в корзину не кладётся")
        calls = await self.h.press(CUSTOMER, "📖 Подробнее")
        self.assertIn("Как ухаживать", self.h.last_text(calls))

    async def test_ask_price_notifies_staff_without_typing(self):
        await self.open_catalog()
        await self.h.press(CUSTOMER, "🌱 Растения")
        await self.h.press(CUSTOMER, "Орхидея", prefix=True)
        calls = await self.h.press(CUSTOMER, "🔎 Цена и наличие")
        staff = self.h.sent_to(ADMIN, calls)
        self.assertTrue(staff and "Узнать цену и наличие" in staff[0] and "цена по запросу" in staff[0], staff)
        self.assertIn("Передали менеджеру", self.h.last_text(calls))
        self.assertEqual(len(crm.list_leads(self.h.db)), 1)

    async def test_back_returns_to_the_same_category_in_place(self):
        await self.open_catalog()
        await self.h.press(CUSTOMER, "🌱 Растения")
        await self.h.press(CUSTOMER, "Орхидея", prefix=True)
        calls = await self.h.press(CUSTOMER, "⬅️ Назад к списку")
        self.assertTrue(self.h.has_button("• 🌱 Растения", calls))
        self.assertEqual(self.h.count(SendMessage, calls), 0)

    async def test_out_of_season_tulips_are_shown_but_not_orderable(self):
        await self.open_catalog()
        calls = await self.h.press(CUSTOMER, "▶")
        self.assertTrue(self.h.has_button("🌷 Тюльпаны", calls, prefix=True), "в списке помечены")
        calls = await self.h.press(CUSTOMER, "🌷 Тюльпаны", prefix=True)
        self.assertIn("с февраля по май", self.h.last_text(calls))
        self.assertFalse(self.h.has_button("🛒 В корзину", calls))
        self.assertTrue(self.h.has_button("🔎 Наличие", calls))
        calls = await self.h.tap(CUSTOMER, Prod(act="add", sku="tulips").pack())  # поддельная кнопка
        self.assertIn("с февраля по май", self.h.alert(calls).text)
        self.assertEqual(catalog.cart_count(self.h.db, CUSTOMER), 0)

    async def test_stop_list_is_visible_in_the_list_and_the_card(self):
        catalog.set_in_stock(self.h.db, "mini", False)
        calls = await self.open_catalog()
        self.assertTrue(self.h.has_button("⛔ Мини", calls, prefix=True))
        calls = await self.h.press(CUSTOMER, "⛔ Мини", prefix=True)
        self.assertIn("Сейчас нет в наличии", self.h.last_text(calls))
        self.assertFalse(self.h.has_button("🛒 В корзину", calls))

    async def test_old_style_buttons_still_open_the_catalog(self):
        calls = await self.h.tap(CUSTOMER, Cat(cat="root").pack())
        self.assertEqual(len(self.h.find("p:open:", calls)), kb.LIST_PAGE)

    async def test_cart_button_appears_in_the_list_after_adding(self):
        await self.h.tap(CUSTOMER, Prod(act="add", sku="mini").pack())
        calls = await self.open_catalog()
        self.assertTrue(self.h.has_button("🛒 Корзина · 1 ·", calls, prefix=True))

    async def test_expanded_card_is_recognised_by_its_buttons(self):
        def message(*labels: str) -> Message:
            markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data="x") for t in labels]])
            return Message(message_id=1, date=datetime.now(timezone.utc), chat=Chat(id=1, type="private"), reply_markup=markup)
        self.assertTrue(is_expanded(message("🛒 В корзину", "📖 Свернуть")))
        self.assertFalse(is_expanded(message("🛒 В корзину", "📖 Подробнее")))
        self.assertFalse(is_expanded(Message(message_id=1, date=datetime.now(timezone.utc), chat=Chat(id=1, type="private"))))


class CartTests(BotCase):
    async def add(self, sku: str, uid: int = CUSTOMER):
        return await self.h.tap(uid, Prod(act="add", sku=sku).pack())

    async def test_adding_from_a_card_only_refreshes_the_buttons(self):
        await self.open_catalog()
        await self.h.press(CUSTOMER, "Мини ·", prefix=True)
        calls = await self.h.press(CUSTOMER, "🛒 В корзину")
        self.assertIn("Добавлено", self.h.alert(calls).text)
        self.assertEqual((self.h.count(SendMessage, calls), self.h.count(EditMessageReplyMarkup, calls)), (0, 1))
        self.assertTrue(self.h.has_button("🛒 Корзина · 1 · " + rub(990), calls))
        calls = await self.h.press(CUSTOMER, "🛒 В корзину")
        self.assertTrue(self.h.has_button("🛒 Корзина · 2 · " + rub(1980), calls))
        self.assertEqual(catalog.cart_count(self.h.db, CUSTOMER), 2)

    async def test_cart_button_opens_the_cart_in_place(self):
        await self.open_catalog()
        await self.h.press(CUSTOMER, "Мини ·", prefix=True)
        await self.h.press(CUSTOMER, "🛒 В корзину")
        calls = await self.h.press(CUSTOMER, "🛒 Корзина ·", prefix=True)
        self.assertEqual((self.h.count(SendMessage, calls), self.h.count(EditMessageText, calls)), (0, 1))
        self.assertIn("Ваша корзина", self.h.last_text(calls))
        self.assertIn("Мини", self.h.last_text(calls))

    async def test_add_and_show(self):
        calls = await self.add("classic")
        self.assertIn("Добавлено", self.h.alert(calls).text)
        await self.add("classic")
        await self.add("mini")
        calls = await self.h.say(CUSTOMER, texts.BTN_CART)
        text = self.h.last_text(calls)
        self.assertIn("Классика", text)
        self.assertIn(f"{rub(1990)} × 2", text)
        self.assertIn(rub(4970), text, "итого 2×1990+990")
        self.assertIn("Доставка по Москве — бесплатно", text, "от 3 000 ₽")
        for label in ("✅ Оформить заказ", "🔎 Узнать о наличии", "🌸 Добавить ещё", "🧹 Очистить"):
            self.assertTrue(self.h.has_button(label, calls), label)

    async def test_empty_cart_points_to_the_catalog(self):
        calls = await self.h.say(CUSTOMER, texts.BTN_CART)
        self.assertIn("пуста", self.h.last_text(calls))
        self.assertTrue(self.h.has_button("🌸 В каталог", calls))
        calls = await self.h.press(CUSTOMER, "🌸 В каталог")
        self.assertEqual(len(self.h.find("p:open:", calls)), kb.LIST_PAGE)

    async def test_plus_minus_delete_clear(self):
        await self.add("classic")
        await self.h.say(CUSTOMER, texts.BTN_CART)
        await self.h.press(CUSTOMER, "➕")
        self.assertEqual(catalog.cart_count(self.h.db, CUSTOMER), 2)
        await self.h.press(CUSTOMER, "➖")
        await self.h.press(CUSTOMER, "➖")
        self.assertEqual(catalog.cart_count(self.h.db, CUSTOMER), 0)
        await self.add("mini")
        await self.h.say(CUSTOMER, "/cart")
        await self.h.press(CUSTOMER, "🗑")
        self.assertEqual(catalog.cart_count(self.h.db, CUSTOMER), 0)
        await self.add("mini")
        await self.h.say(CUSTOMER, "/cart")
        calls = await self.h.press(CUSTOMER, "🧹 Очистить")
        self.assertEqual(catalog.cart_count(self.h.db, CUSTOMER), 0)
        self.assertIn("пуста", self.h.last_text(calls))

    async def test_carts_are_separate(self):
        await self.add("classic", CUSTOMER)
        self.assertEqual(catalog.cart_count(self.h.db, OTHER), 0)

    async def test_stop_list_blocks_adding(self):
        catalog.set_in_stock(self.h.db, "mini", False)
        calls = await self.add("mini")
        self.assertIn("нет в наличии", self.h.alert(calls).text)
        self.assertEqual(catalog.cart_count(self.h.db, CUSTOMER), 0)

    async def test_forged_sku_is_rejected(self):
        calls = await self.h.tap(CUSTOMER, Prod(act="add", sku="nonexistent").pack())
        self.assertIn("нет в каталоге", self.h.alert(calls).text)
        await self.h.tap(CUSTOMER, "p:add:' OR 1=1 --")
        self.assertEqual(catalog.cart_count(self.h.db, CUSTOMER), 0)

    async def test_check_availability_sends_cart_to_staff(self):
        await self.add("classic")
        await self.add("roses15")
        await self.h.say(CUSTOMER, texts.BTN_CART)
        calls = await self.h.press(CUSTOMER, "🔎 Узнать о наличии")
        staff = self.h.sent_to(ADMIN, calls)
        self.assertTrue(staff and "Классика × 1" in staff[0] and "15 роз × 1" in staff[0], staff)
        self.assertEqual(self.h.count(SendMessage, calls), 2)


class InfoTests(BotCase):
    async def test_delivery_and_payment_sections(self):
        calls = await self.h.say(CUSTOMER, texts.BTN_DELIVERY)
        for label in ("🚚 Условия доставки", "💳 Способы оплаты", "🛍 Самовывоз"):
            self.assertTrue(self.h.has_button(label, calls), label)
        text = self.h.last_text(await self.h.press(CUSTOMER, "🚚 Условия доставки"))
        for needle in (rub(300), rub(3000), "МКАД", "2 часа", "только с полной предоплатой"):
            self.assertIn(needle, text)
        text = self.h.last_text(await self.h.press(CUSTOMER, "💳 Способы оплаты"))
        self.assertIn("При получении", text)
        self.assertNotIn("ЮKassa", text, "онлайн-оплата не подключена — про неё не пишем")
        text = self.h.last_text(await self.h.press(CUSTOMER, "🛍 Самовывоз"))
        self.assertIn("Земляной Вал", text)
        self.assertIn("20 минут", text)

    async def test_pages_switch_in_place_and_lead_to_the_catalog(self):
        await self.h.say(CUSTOMER, texts.BTN_DELIVERY)
        calls = await self.h.press(CUSTOMER, "🚚 Условия доставки")
        self.assertEqual((self.h.count(SendMessage, calls), self.h.count(EditMessageText, calls)), (0, 1))
        calls = await self.h.press(CUSTOMER, "⬅️ Назад")
        self.assertIn("Что рассказать", self.h.last_text(calls))
        await self.h.press(CUSTOMER, "🚚 Условия доставки")
        calls = await self.h.press(CUSTOMER, "🌸 Выбрать букет")
        self.assertEqual(len(self.h.find("p:open:", calls)), kb.LIST_PAGE)

    async def test_map(self):
        calls = await self.h.say(CUSTOMER, texts.BTN_MAP)
        text = self.h.last_text(calls)
        self.assertIn("Земляной Вал", text)
        self.assertIn("+7 (917) 549-49-41", text)
        urls = [b.url for b in self.h.buttons(calls) if b.url]
        self.assertTrue(any("yandex.ru/maps" in u for u in urls) and any("google.com/maps" in u for u in urls), urls)

    async def test_online_payment_is_mentioned_only_when_enabled(self):
        h = Harness(yookassa=True)
        self.addCleanup(h.close)
        await h.say(CUSTOMER, texts.BTN_DELIVERY)
        text = h.last_text(await h.press(CUSTOMER, "💳 Способы оплаты"))
        self.assertIn("ЮKassa", text)
        self.assertIn("СБП", text)


class ManagerTests(BotCase):
    async def test_manager_screen_follows_the_brief(self):
        calls = await self.h.say(CUSTOMER, texts.BTN_MANAGER)
        text = self.h.last_text(calls)
        self.assertIn("Напишите свой вопрос — менеджер ответит в ближайшее время", text)
        for label in ("❓ Задать вопрос", "🔎 Уточнить наличие", "⚡ Срочный заказ", "📞 Оставить номер"):
            self.assertTrue(self.h.has_button(label, calls), label)
        self.assertFalse(any(b.url for b in self.h.buttons(calls)), "ника менеджера нет — кнопки-ссылки нет")

    async def test_chat_link_appears_when_username_is_set(self):
        import dataclasses
        from .helpers import real_shop
        h = Harness(shop=dataclasses.replace(real_shop(), manager_username="ivan_flowers"))
        self.addCleanup(h.close)
        calls = await h.say(CUSTOMER, texts.BTN_MANAGER)
        self.assertIn("https://t.me/ivan_flowers", [b.url for b in h.buttons(calls)])

    async def test_question_goes_to_staff_and_can_be_answered(self):
        await self.h.say(CUSTOMER, texts.BTN_MANAGER)
        await self.h.press(CUSTOMER, "❓ Задать вопрос")
        calls = await self.h.say(CUSTOMER, "Есть ли розы Пинк Флойд?")
        staff = self.h.sent_to(ADMIN, calls)
        self.assertTrue(staff and "Пинк Флойд" in staff[0] and "Вопрос" in staff[0], staff)
        self.assertIn("Передали менеджеру", self.h.last_text(calls))
        self.assertEqual(set(self.h.reply_labels()), texts.CUSTOMER_BUTTONS)
        # сотрудник отвечает из бота
        await self.h.press(ADMIN, "↩️ Ответить")
        calls = await self.h.say(ADMIN, "Да, есть, 70 см")
        self.assertIn("Да, есть, 70 см", self.h.sent_to(CUSTOMER, calls)[0])
        self.assertIn("Отправлено", self.h.sent_to(ADMIN, calls)[0])

    async def test_urgent_and_avail(self):
        await self.h.say(CUSTOMER, texts.BTN_MANAGER)
        await self.h.press(CUSTOMER, "⚡ Срочный заказ")
        calls = await self.h.say(CUSTOMER, "Нужен букет к 18:00, Тверская 1, +7 900 111-22-33")
        self.assertIn("Срочный заказ", self.h.sent_to(ADMIN, calls)[0])
        await self.h.say(CUSTOMER, texts.BTN_MANAGER)
        await self.h.press(CUSTOMER, "🔎 Уточнить наличие")
        calls = await self.h.say(CUSTOMER, "Есть ли гортензии на завтра?")
        self.assertIn("Узнать о наличии", self.h.sent_to(ADMIN, calls)[0])

    async def test_leave_phone_with_contact_button(self):
        await self.h.say(CUSTOMER, texts.BTN_MANAGER)
        calls = await self.h.press(CUSTOMER, "📞 Оставить номер")
        labels = [b.text for row in calls[-1].reply_markup.keyboard for b in row]
        self.assertTrue(any("Поделиться номером" in label for label in labels), labels)
        calls = await self.h.contact(CUSTOMER, "79175494941")
        staff = self.h.sent_to(ADMIN, calls)
        self.assertIn("+7 917 549-49-41", staff[0])
        self.assertEqual(crm.get_user(self.h.db, CUSTOMER)["phone"], "+79175494941")
        self.assertEqual(set(self.h.reply_labels()), texts.CUSTOMER_BUTTONS, "главное меню вернулось")

    async def test_someone_elses_contact_is_refused(self):
        await self.h.say(CUSTOMER, texts.BTN_MANAGER)
        await self.h.press(CUSTOMER, "📞 Оставить номер")
        calls = await self.h.contact(CUSTOMER, "79990001122", contact_user_id=OTHER)
        self.assertEqual(self.h.sent_to(ADMIN, calls), [])
        self.assertEqual(crm.list_leads(self.h.db), [])

    async def test_budget_flow(self):
        await self.open_catalog()
        await self.h.press(CUSTOMER, "💰 Подбор под бюджет")
        calls = await self.h.say(CUSTOMER, "100")
        self.assertIn("от 500", self.h.last_text(calls))
        await self.h.say(CUSTOMER, "3000")
        await self.h.contact(CUSTOMER, "79175494941")
        calls = await self.h.say(CUSTOMER, "Любит розовые пионы, день рождения мамы")
        staff = self.h.sent_to(ADMIN, calls)
        self.assertTrue(rub(3000) in staff[0] and "пионы" in staff[0], staff)

    async def test_lead_spam_is_limited(self):
        for i in range(6):
            await self.h.say(CUSTOMER, texts.BTN_MANAGER)
            await self.h.press(CUSTOMER, "❓ Задать вопрос")
            calls = await self.h.say(CUSTOMER, f"Вопрос номер {i}")
        self.assertEqual(len(crm.list_leads(self.h.db, limit=50)), 5)
        self.assertIn("уже получили", self.h.last_text(calls))

    async def test_menu_button_cancels_question_mode(self):
        await self.h.say(CUSTOMER, texts.BTN_MANAGER)
        await self.h.press(CUSTOMER, "❓ Задать вопрос")
        calls = await self.h.say(CUSTOMER, texts.BTN_CART)
        self.assertEqual(crm.list_leads(self.h.db), [], "название кнопки не стало вопросом")
        self.assertIn("пуста", self.h.last_text(calls))


if __name__ == "__main__":
    unittest.main()
