"""Бот глазами клиента: старт, каталог, корзина, справочные разделы, связь с менеджером."""
from __future__ import annotations

import unittest

from aiogram.methods import AnswerCallbackQuery, SendMessage, SendPhoto

from flowerbot import catalog, crm, texts
from flowerbot.callbacks import Adm

from .harness import Harness
from .helpers import ADMIN, CUSTOMER, OTHER


class BotCase(unittest.IsolatedAsyncioTestCase):
    admin_ids = (ADMIN,)

    async def asyncSetUp(self):
        self.h = Harness(admin_ids=self.admin_ids)
        self.addCleanup(self.h.close)


class StartAndMenuTests(BotCase):
    async def test_start_shows_greeting_and_all_menu_buttons(self):
        calls = await self.h.say(CUSTOMER, "/start")
        text = self.h.last_text(calls)
        self.assertIn("MuzFlowers", text)
        self.assertIn("Земляной Вал", text)
        self.assertIn("8:00–21:00", text)
        self.assertEqual(set(self.h.reply_labels()), texts.MENU_BUTTONS)
        self.assertEqual(len(texts.MENU_BUTTONS), 8)
        self.assertIsNotNone(crm.get_user(self.h.db, CUSTOMER), "клиент запомнен")

    async def test_every_menu_button_answers(self):
        for button in sorted(texts.MENU_BUTTONS):
            calls = await self.h.say(CUSTOMER, button)
            self.assertTrue(self.h.texts(calls), f"кнопка {button} промолчала")

    async def test_unknown_text_gets_polite_answer(self):
        calls = await self.h.say(CUSTOMER, "абракадабра")
        self.assertIn("Выберите раздел", self.h.last_text(calls))

    async def test_myid(self):
        self.assertIn(str(CUSTOMER), self.h.last_text(await self.h.say(CUSTOMER, "/myid")))
        self.assertIn("администратор", self.h.last_text(await self.h.say(ADMIN, "/myid")))

    async def test_nothing_is_sent_for_stale_buttons(self):
        calls = await self.h.tap(CUSTOMER, "zzz:garbage")
        self.assertIn("устарела", self.h.alert(calls).text)


class CatalogTests(BotCase):
    async def test_catalog_pages(self):
        calls = await self.h.say(CUSTOMER, texts.BTN_CATALOG)
        self.assertTrue(self.h.has_button("💐 Букеты", calls) and self.h.has_button("🌱 Горшечные растения", calls))
        calls = await self.h.press(CUSTOMER, "💐 Букеты")
        cards = [t for t in self.h.texts(calls) if "₽" in t or "по запросу" in t]
        self.assertEqual(len(cards), 5)
        self.assertIn("Мини", cards[0])
        self.assertIn("990", cards[0])
        self.assertIn("Показано 1–5 из 12", self.h.last_text(calls))
        calls = await self.h.press(CUSTOMER, "Показать ещё ▶️")
        self.assertIn("Показано 6–10 из 12", self.h.last_text(calls))
        calls = await self.h.press(CUSTOMER, "Показать ещё ▶️")
        self.assertIn("Показано 11–12 из 12", self.h.last_text(calls))
        self.assertFalse(self.h.has_button("Показать ещё ▶️", calls))

    async def test_card_has_required_buttons(self):
        calls = await self.h.say(CUSTOMER, "/catalog")
        calls = await self.h.press(CUSTOMER, "💐 Букеты")
        for label in ("📖 Подробнее", "🛒 В корзину", "⬅️ Назад"):
            self.assertTrue(self.h.has_button(label, calls), label)

    async def test_plants_have_care_and_ask_price(self):
        await self.h.say(CUSTOMER, texts.BTN_CATALOG)
        calls = await self.h.press(CUSTOMER, "🌱 Горшечные растения")
        cards = [t for t in self.h.texts(calls) if "по запросу" in t]
        self.assertEqual(len(cards), 5)
        self.assertTrue(self.h.has_button("🔎 Цена и наличие", calls))
        self.assertFalse(self.h.has_button("🛒 В корзину", calls), "растение без цены в корзину не кладётся")
        calls = await self.h.press(CUSTOMER, "📖 Подробнее")
        self.assertIn("Как ухаживать", self.h.last_text(calls))

    async def test_bouquet_details_show_composition(self):
        await self.h.say(CUSTOMER, texts.BTN_CATALOG)
        await self.h.press(CUSTOMER, "💐 Букеты")
        calls = await self.h.press(CUSTOMER, "📖 Подробнее")
        text = self.h.last_text(calls)  # нажата кнопка у последней показанной карточки — «25 роз»
        self.assertIn("Состав", text)
        self.assertIn("• 25 роз", text)
        self.assertTrue(self.h.has_button("🛒 Добавить в корзину", calls))
        self.assertTrue(self.h.has_button("💬 Задать вопрос менеджеру", calls))

    async def test_ask_price_notifies_staff_without_typing(self):
        await self.h.say(CUSTOMER, texts.BTN_CATALOG)
        await self.h.press(CUSTOMER, "🌱 Горшечные растения")
        calls = await self.h.press(CUSTOMER, "🔎 Цена и наличие")
        staff = self.h.sent_to(ADMIN, calls)
        self.assertTrue(staff and "Узнать цену и наличие" in staff[0] and "цена по запросу" in staff[0], staff)
        self.assertIn("Передали менеджеру", self.h.last_text(calls))
        self.assertEqual(len(crm.list_leads(self.h.db)), 1)

    async def test_out_of_season_tulips_not_orderable(self):
        await self.h.say(CUSTOMER, texts.BTN_CATALOG)
        await self.h.press(CUSTOMER, "💐 Букеты")
        await self.h.press(CUSTOMER, "Показать ещё ▶️")
        calls = await self.h.press(CUSTOMER, "Показать ещё ▶️")
        tulip = next(t for t in self.h.texts(calls) if "Тюльпаны" in t) if any("Тюльпаны" in t for t in self.h.texts(calls)) else None
        calls2 = await self.h.tap(CUSTOMER, __import__("flowerbot.callbacks", fromlist=["Prod"]).Prod(act="add", sku="tulips").pack())
        self.assertIn("с февраля по май", self.h.alert(calls2).text)
        self.assertEqual(catalog.cart_count(self.h.db, CUSTOMER), 0)


class CartTests(BotCase):
    async def add(self, sku: str, uid: int = CUSTOMER):
        from flowerbot.callbacks import Prod
        return await self.h.tap(uid, Prod(act="add", sku=sku).pack())

    async def test_add_and_show(self):
        calls = await self.add("classic")
        self.assertIn("Добавлено", self.h.alert(calls).text)
        await self.add("classic")
        await self.add("mini")
        calls = await self.h.say(CUSTOMER, texts.BTN_CART)
        text = self.h.last_text(calls)
        self.assertIn("Классика", text)
        self.assertIn("1 990 ₽ × 2", text)
        self.assertIn("4 970 ₽", text, "итого 2×1990+990")
        self.assertIn("Доставка по Москве — бесплатно", text, "от 3 000 ₽")
        for label in ("💳 Перейти к оплате", "🔎 Узнать о наличии", "🧹 Очистить"):
            self.assertTrue(self.h.has_button(label, calls), label)

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
        from flowerbot.callbacks import Prod
        calls = await self.h.tap(CUSTOMER, Prod(act="add", sku="nonexistent").pack())
        self.assertIn("нет в каталоге", self.h.alert(calls).text)
        calls = await self.h.tap(CUSTOMER, "p:add:' OR 1=1 --")
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
        for needle in ("300", "3 000", "МКАД", "09:00–11:00", "только с полной предоплатой"):
            self.assertIn(needle, text)
        text = self.h.last_text(await self.h.press(CUSTOMER, "💳 Способы оплаты"))
        self.assertIn("При получении", text)
        self.assertNotIn("ЮKassa", text, "онлайн-оплата не подключена — про неё не пишем")
        text = self.h.last_text(await self.h.press(CUSTOMER, "🛍 Самовывоз"))
        self.assertIn("Земляной Вал", text)
        self.assertIn("20 минут", text)

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
        self.assertEqual(set(self.h.reply_labels()), texts.MENU_BUTTONS, "главное меню вернулось")

    async def test_someone_elses_contact_is_refused(self):
        await self.h.say(CUSTOMER, texts.BTN_MANAGER)
        await self.h.press(CUSTOMER, "📞 Оставить номер")
        calls = await self.h.contact(CUSTOMER, "79990001122", contact_user_id=OTHER)
        self.assertEqual(self.h.sent_to(ADMIN, calls), [])
        self.assertEqual(crm.list_leads(self.h.db), [])

    async def test_budget_flow(self):
        await self.h.say(CUSTOMER, texts.BTN_CATALOG)
        await self.h.press(CUSTOMER, "💰 Подбор под бюджет")
        calls = await self.h.say(CUSTOMER, "100")
        self.assertIn("от 500", self.h.last_text(calls))
        await self.h.say(CUSTOMER, "3000")
        await self.h.contact(CUSTOMER, "79175494941")
        calls = await self.h.say(CUSTOMER, "Любит розовые пионы, день рождения мамы")
        staff = self.h.sent_to(ADMIN, calls)
        self.assertTrue("3 000" in staff[0] and "пионы" in staff[0], staff)

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
