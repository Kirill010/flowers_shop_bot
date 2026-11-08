"""Сквозные сценарии: оформление заказа, оплата, сертификаты, работа сотрудников, отзывы, «Мои заказы»."""
from __future__ import annotations

import dataclasses
import unittest
from datetime import timedelta
from pathlib import Path

from aiogram.methods import SendDocument

from flowerbot import billing, catalog, certs, crm, orders, texts
from flowerbot.callbacks import Prod
from flowerbot.orders import Draft

from .harness import Harness
from .helpers import ADMIN, CUSTOMER, NOW, OTHER, TODAY, real_shop

TOMORROW = TODAY + timedelta(days=1)


class FlowCase(unittest.IsolatedAsyncioTestCase):
    admin_ids = (ADMIN,)
    yookassa = False

    async def asyncSetUp(self):
        self.h = Harness(admin_ids=self.admin_ids, yookassa=self.yookassa)
        self.addCleanup(self.h.close)

    async def add(self, sku: str, times: int = 1, uid: int = CUSTOMER):
        for _ in range(times):
            await self.h.tap(uid, Prod(act="add", sku=sku).pack())

    async def checkout_until_summary(self, *, method="pickup", day_label="Сегодня, 09.11", slot="Как можно скорее",
                                     address="Москва, Тверская 1, кв. 5", uid=CUSTOMER):
        """Проходит оформление до экрана «Проверьте заказ»."""
        h = self.h
        await h.say(uid, texts.BTN_CART)
        calls = await h.press(uid, "💳 Перейти к оплате")
        if h.has_button("✅ Да, всё верно", calls):  # постоянному клиенту бот предлагает сохранённые данные
            await h.press(uid, "✅ Да, всё верно")
        else:
            await h.say(uid, "Мария")
            await h.say(uid, "8 (917) 549-49-41")
        if method == "delivery":
            await h.press(uid, "🚚 Доставка")
            await h.press(uid, "По Москве (в пределах МКАД) — 300 ₽")
            await h.say(uid, address)
            await h.press(uid, "👤 Получатель — я")
        else:
            await h.press(uid, "🛍 Самовывоз")
        await h.press(uid, day_label)
        await h.press(uid, slot)
        return await h.press(uid, "Пропустить ▶️")


class CheckoutTests(FlowCase):
    async def test_pickup_cash_full_flow(self):
        await self.add("classic", 2)
        calls = await self.checkout_until_summary()
        summary = self.h.last_text(calls)
        self.assertIn("Проверьте заказ", summary)
        self.assertIn("Самовывоз", summary)
        self.assertIn("Классика × 2", summary)
        self.assertIn("3 980 ₽", summary)
        for label in ("💵 Наличными при получении", "💳 Картой при получении", "🔗 Перевод / ссылка от менеджера",
                      "🏷 Промокод", "🎁 Сертификат"):
            self.assertTrue(self.h.has_button(label, calls), label)
        self.assertFalse(self.h.has_button("💳 Онлайн (карта или СБП)", calls), "ЮKassa не подключена")

        calls = await self.h.press(CUSTOMER, "💵 Наличными при получении")
        self.assertIn("Заказ №1 оформлен", self.h.sent_to(CUSTOMER, calls)[0])
        staff = self.h.sent_to(ADMIN, calls)
        self.assertTrue(staff and "Новый заказ" in staff[0] and "Мария" in staff[0] and "+7 917 549-49-41" in staff[0], staff)
        self.assertTrue(self.h.has_button("✅ Принять", calls) and self.h.has_button("❌ Отменить", calls))
        order = orders.get_order(self.h.db, 1)
        self.assertEqual((order.total, order.pay_method, order.pay_status, order.method), (3980, "cash", "none", "pickup"))
        self.assertEqual(catalog.cart_count(self.h.db, CUSTOMER), 0)
        user = crm.get_user(self.h.db, CUSTOMER)
        self.assertEqual((user["name"], user["phone"]), ("Мария", "+79175494941"), "данные запомнены для следующего раза")

    async def test_delivery_with_recipient_and_comment(self):
        h = self.h
        await self.add("mini")
        await h.say(CUSTOMER, texts.BTN_CART)
        await h.press(CUSTOMER, "💳 Перейти к оплате")
        await h.say(CUSTOMER, "Мария")
        await h.say(CUSTOMER, "+79175494941")
        await h.press(CUSTOMER, "🚚 Доставка")
        await h.press(CUSTOMER, "По Москве (в пределах МКАД) — 300 ₽")
        await h.say(CUSTOMER, "Москва, Арбат 10, кв. 3")
        await h.press(CUSTOMER, "🎁 Другой человек")
        await h.say(CUSTOMER, "Анна")
        await h.say(CUSTOMER, "+7 900 111-22-33")
        calls = await h.press(CUSTOMER, "Завтра, 10.11")
        self.assertIn("Выберите время", h.last_text(calls))
        await h.press(CUSTOMER, "17:00–19:00")
        calls = await h.say(CUSTOMER, "Музыкальная открытка: Земфира — Хочешь?")
        summary = h.last_text(calls)
        for needle in ("Анна", "+7 900 111-22-33", "Земфира", "Арбат 10", "Доставка: 300", "Итого: 1 290 ₽"):
            self.assertIn(needle, summary)
        self.assertIn("только по предоплате", summary)
        self.assertFalse(h.has_button("💵 Наличными при получении", calls), "на завтра — только предоплата")
        calls = await h.press(CUSTOMER, "🔗 Перевод / ссылка от менеджера")
        order = orders.get_order(h.db, 1)
        self.assertEqual((order.total, order.delivery, order.pay_status, order.recipient_name), (1290, 300, "pending", "Анна"))
        self.assertIn("Менеджер пришлёт", h.sent_to(CUSTOMER, calls)[0])

    async def test_saved_details_are_offered_next_time(self):
        await self.add("mini")
        await self.checkout_until_summary()
        await self.h.press(CUSTOMER, "💵 Наличными при получении")
        await self.add("mini")
        await self.h.say(CUSTOMER, texts.BTN_CART)
        calls = await self.h.press(CUSTOMER, "💳 Перейти к оплате")
        self.assertIn("Оформляем на", self.h.last_text(calls))
        self.assertIn("Мария", self.h.last_text(calls))
        calls = await self.h.press(CUSTOMER, "✅ Да, всё верно")
        self.assertIn("Как получите заказ", self.h.last_text(calls))

    async def test_bad_input_is_rejected_politely(self):
        h = self.h
        await self.add("mini")
        await h.say(CUSTOMER, texts.BTN_CART)
        await h.press(CUSTOMER, "💳 Перейти к оплате")
        self.assertIn("имя буквами", h.last_text(await h.say(CUSTOMER, "12345")))
        await h.say(CUSTOMER, "Мария")
        self.assertIn("распознать номер", h.last_text(await h.say(CUSTOMER, "позвоните мне")))
        await h.say(CUSTOMER, "+79175494941")
        await h.press(CUSTOMER, "🚚 Доставка")
        await h.press(CUSTOMER, "По Москве (в пределах МКАД) — 300 ₽")
        self.assertIn("адрес полностью", h.last_text(await h.say(CUSTOMER, "дом")))
        await h.say(CUSTOMER, "Москва, Арбат 10")
        await h.press(CUSTOMER, "👤 Получатель — я")
        await h.press(CUSTOMER, "📅 Другая дата")
        self.assertIn("распознать дату", h.last_text(await h.say(CUSTOMER, "послезавтра")))
        self.assertIn("уже прошла", h.texts(await h.say(CUSTOMER, "01.11.2026"))[0])
        self.assertIn("не дальше", h.texts(await h.say(CUSTOMER, "01.06.2027"))[0])

    async def test_custom_date_works(self):
        await self.add("mini")
        h = self.h
        await h.say(CUSTOMER, texts.BTN_CART)
        await h.press(CUSTOMER, "💳 Перейти к оплате")
        await h.say(CUSTOMER, "Мария")
        await h.say(CUSTOMER, "+79175494941")
        await h.press(CUSTOMER, "🛍 Самовывоз")
        await h.press(CUSTOMER, "📅 Другая дата")
        calls = await h.say(CUSTOMER, "14.11")
        self.assertIn("Выберите время", h.last_text(calls))
        self.assertTrue(h.has_button("09:00–11:00", calls) and h.has_button("19:00–21:00", calls), "на будущую дату — все окна")

    async def test_cart_changes_during_checkout_are_caught(self):
        await self.add("classic")
        await self.checkout_until_summary()
        self.h.db.run("UPDATE products SET price = 2190 WHERE sku = 'classic'")
        calls = await self.h.press(CUSTOMER, "💵 Наличными при получении")
        self.assertIn("Цена изменилась", self.h.alert(calls).text)
        self.assertIn("2 190 ₽", self.h.last_text(calls), "показана новая сумма")
        self.assertEqual(self.h.db.scalar("SELECT COUNT(*) FROM orders"), 0)
        calls = await self.h.press(CUSTOMER, "💵 Наличными при получении")
        self.assertEqual(orders.get_order(self.h.db, 1).total, 2190)

    async def test_item_removed_from_stock_during_checkout(self):
        await self.add("classic")
        await self.checkout_until_summary()
        catalog.set_in_stock(self.h.db, "classic", False)
        calls = await self.h.press(CUSTOMER, "💵 Наличными при получении")
        self.assertIn("нет в наличии", self.h.last_text(calls))
        self.assertEqual(self.h.db.scalar("SELECT COUNT(*) FROM orders"), 0)

    async def test_double_tap_creates_one_order(self):
        await self.add("classic")
        calls = await self.checkout_until_summary()
        pay = next(b.callback_data for b in self.h.buttons(calls) if b.text == "💵 Наличными при получении")
        await self.h.tap(CUSTOMER, pay)
        calls = await self.h.tap(CUSTOMER, pay)
        self.assertEqual(self.h.db.scalar("SELECT COUNT(*) FROM orders"), 1)
        self.assertTrue("устарело" in self.h.alert(calls).text or "уже оформлен" in self.h.alert(calls).text)

    async def test_menu_button_during_checkout_is_not_saved_as_name(self):
        await self.add("classic")
        await self.h.say(CUSTOMER, texts.BTN_CART)
        await self.h.press(CUSTOMER, "💳 Перейти к оплате")
        calls = await self.h.say(CUSTOMER, texts.BTN_CART)
        self.assertIn("Классика", self.h.last_text(calls))
        self.assertIsNone(crm.get_user(self.h.db, CUSTOMER)["name"] or None)
        calls = await self.h.say(CUSTOMER, "Мария")  # диалог сброшен — это уже не имя
        self.assertIn("Выберите раздел", self.h.last_text(calls))

    async def test_old_buttons_do_not_work_after_checkout_ended(self):
        await self.add("classic")
        calls = await self.checkout_until_summary()
        stale_pay = next(b.callback_data for b in self.h.buttons(calls) if b.text == "💵 Наличными при получении")
        await self.h.press(CUSTOMER, "❌ Отмена")
        calls = await self.h.tap(CUSTOMER, stale_pay)
        self.assertIn("устарело", self.h.alert(calls).text)
        self.assertEqual(self.h.db.scalar("SELECT COUNT(*) FROM orders"), 0, "по старой кнопке заказ не создаётся")
        self.assertEqual(catalog.cart_count(self.h.db, CUSTOMER), 1, "корзина сохранена после отмены")

    async def test_outside_mkad_becomes_a_lead_with_cart(self):
        h = self.h
        await self.add("classic")
        await h.say(CUSTOMER, texts.BTN_CART)
        await h.press(CUSTOMER, "💳 Перейти к оплате")
        await h.say(CUSTOMER, "Мария")
        await h.say(CUSTOMER, "+79175494941")
        await h.press(CUSTOMER, "🚚 Доставка")
        calls = await h.press(CUSTOMER, "🚗 За МКАД / в другой город")
        self.assertIn("Стоимость рассчитает менеджер", h.last_text(calls))
        calls = await h.say(CUSTOMER, "Мытищи, ул. Ленина 5, завтра к 12:00")
        staff = h.sent_to(ADMIN, calls)
        self.assertTrue("Мытищи" in staff[0] and "Классика × 1" in staff[0] and "Доставка за МКАД" in staff[0], staff)
        self.assertEqual(h.db.scalar("SELECT COUNT(*) FROM orders"), 0, "заказ не создаётся — цену назовёт менеджер")

    async def test_paused_shop_refuses_orders(self):
        await self.add("classic")
        await self.h.say(ADMIN, "/pause")
        await self.h.say(CUSTOMER, texts.BTN_CART)
        calls = await self.h.press(CUSTOMER, "💳 Перейти к оплате")
        self.assertIn("Сейчас не принимаем заказы", self.h.last_text(calls))
        await self.h.say(ADMIN, "/resume")
        calls = await self.h.press(CUSTOMER, "💳 Перейти к оплате")
        self.assertIn("Как вас зовут", self.h.last_text(calls))

    async def test_empty_or_broken_cart_blocks_checkout(self):
        from flowerbot.callbacks import Cart
        await self.h.say(CUSTOMER, texts.BTN_CART)
        calls = await self.h.tap(CUSTOMER, Cart(act="pay").pack())
        self.assertIn("поправьте корзину", self.h.last_text(calls))
        self.h.db.run("INSERT INTO cart (user_id, sku, qty) VALUES (?, 'orchid', 1)", (CUSTOMER,))
        calls = await self.h.tap(CUSTOMER, Cart(act="pay").pack())
        self.assertIn("цену и наличие уточняйте", self.h.last_text(calls))

    async def test_html_in_user_text_cannot_inject_markup(self):
        await self.add("mini")
        h = self.h
        await h.say(CUSTOMER, texts.BTN_CART)
        await h.press(CUSTOMER, "💳 Перейти к оплате")
        await h.say(CUSTOMER, "<b>Хакер</b>")
        await h.say(CUSTOMER, "+79175494941")
        await h.press(CUSTOMER, "🛍 Самовывоз")
        await h.press(CUSTOMER, "Сегодня, 09.11")
        await h.press(CUSTOMER, "Как можно скорее")
        await h.say(CUSTOMER, '<a href="http://evil.example">нажми</a> & <script>')
        calls = await h.press(CUSTOMER, "💵 Наличными при получении")
        staff = h.sent_to(ADMIN, calls)[0]
        self.assertNotIn('<a href="http://evil', staff)
        self.assertNotIn("<script>", staff)
        self.assertIn("&lt;a href=", staff)
        self.assertIn("&lt;b&gt;Хакер&lt;/b&gt;", staff)

    async def test_without_staff_orders_are_not_accepted(self):
        h = Harness(admin_ids=())
        self.addCleanup(h.close)
        await h.tap(CUSTOMER, Prod(act="add", sku="mini").pack())
        await h.say(CUSTOMER, texts.BTN_CART)
        calls = await h.press(CUSTOMER, "💳 Перейти к оплате")
        self.assertIn("настраивается", h.last_text(calls))
        self.assertIn("настраивается", h.last_text(await h.say(CUSTOMER, "/start")))
        self.assertIn(str(CUSTOMER), h.last_text(await h.say(CUSTOMER, "/myid")))
        self.assertIn("ADMIN_IDS", h.last_text(await h.say(CUSTOMER, "/myid")))


class PromoAndCertificateInCheckoutTests(FlowCase):
    async def asyncSetUp(self):
        from flowerbot.config import Promo
        shop = dataclasses.replace(real_shop(), promos=(Promo("ВЕСНА", percent=10, until=TODAY + timedelta(days=30)),))
        self.h = Harness(admin_ids=self.admin_ids, shop=shop)
        self.addCleanup(self.h.close)

    async def test_promo_code(self):
        await self.add("classic", 2)
        await self.checkout_until_summary()
        await self.h.press(CUSTOMER, "🏷 Промокод")
        calls = await self.h.say(CUSTOMER, "не такого")
        self.assertIn("Такого промокода нет", self.h.sent_to(CUSTOMER, calls)[0])
        await self.h.press(CUSTOMER, "🏷 Промокод")
        calls = await self.h.say(CUSTOMER, "весна")
        summary = self.h.last_text(calls)
        self.assertIn("Промокод ВЕСНА: −398 ₽", summary)
        self.assertIn("Итого: 3 582 ₽", summary)
        self.assertTrue(self.h.has_button("❌ Убрать промокод", calls))
        calls = await self.h.press(CUSTOMER, "💵 Наличными при получении")
        self.assertEqual(orders.get_order(self.h.db, 1).discount, 398)

    async def test_certificate_in_checkout(self):
        cert = certs.issue(self.h.db, self.h.ctx.shop, 1000)
        await self.add("classic")
        await self.checkout_until_summary()
        await self.h.press(CUSTOMER, "🎁 Сертификат")
        calls = await self.h.say(CUSTOMER, "AAAA-BBBB-CCCC")
        self.assertIn("не найден", self.h.sent_to(CUSTOMER, calls)[0])
        await self.h.press(CUSTOMER, "🎁 Сертификат")
        calls = await self.h.say(CUSTOMER, cert.code.lower())
        summary = self.h.last_text(calls)
        self.assertIn("Сертификат", summary)
        self.assertIn("К оплате: 990 ₽", summary)
        await self.h.press(CUSTOMER, "💵 Наличными при получении")
        order = orders.get_order(self.h.db, 1)
        self.assertEqual((order.cert_amount, order.to_pay), (1000, 990))
        self.assertEqual(certs.get_by_code(self.h.db, cert.code).balance, 0)

    async def test_certificate_covering_everything(self):
        cert = certs.issue(self.h.db, self.h.ctx.shop, 5000)
        await self.add("mini")
        await self.checkout_until_summary()
        await self.h.press(CUSTOMER, "🎁 Сертификат")
        calls = await self.h.say(CUSTOMER, cert.code)
        self.assertTrue(self.h.has_button("✅ Подтвердить заказ", calls))
        self.assertFalse(self.h.has_button("💵 Наличными при получении", calls))
        calls = await self.h.press(CUSTOMER, "✅ Подтвердить заказ")
        self.assertIn("Оплачен сертификатом", self.h.last_text(calls))
        self.assertEqual(orders.get_order(self.h.db, 1).pay_status, "paid")

    async def test_certificate_brute_force_is_blocked(self):
        await self.add("mini")
        await self.checkout_until_summary()
        for _ in range(5):
            await self.h.press(CUSTOMER, "🎁 Сертификат")
            calls = await self.h.say(CUSTOMER, "ZZZZ-ZZZZ-ZZZZ")
        self.assertIn("повторите через 30 мин", self.h.sent_to(CUSTOMER, calls)[0])


class OnlinePaymentTests(FlowCase):
    yookassa = True

    async def place_online_order(self):
        await self.add("classic", 2)
        await self.checkout_until_summary()
        return await self.h.press(CUSTOMER, "💳 Онлайн (карта или СБП)")

    async def test_online_order_gets_payment_link(self):
        calls = await self.place_online_order()
        self.assertIn("Оплатить", self.h.last_text(calls) + " ".join(b.text for b in self.h.buttons(calls)))
        link = next(b for b in self.h.buttons(calls) if b.text == "💳 Оплатить")
        self.assertTrue(link.url.startswith("https://pay.example/"))
        created = self.h.yk.created[0]
        self.assertEqual((created["amount"], created["return_url"], created["phone"]), (3980, "https://t.me/test_bot", "+79175494941"))
        self.assertEqual(created["key"], "order-1")
        self.assertEqual(sum(i.amount for i in created["items"]), 3980)
        self.assertEqual(orders.get_order(self.h.db, 1).pay_status, "pending")

    async def test_paid_once_even_if_checked_many_times(self):
        await self.place_online_order()
        pid = next(iter(self.h.yk.status))
        calls = await self.h.press(CUSTOMER, "🔄 Проверить оплату")
        self.assertIn("пока не завершён", self.h.alert(calls).text)
        self.h.yk.status[pid] = "succeeded"
        calls = await self.h.press(CUSTOMER, "🔄 Проверить оплату")
        self.assertIn("Оплата получена", self.h.alert(calls).text)
        self.assertEqual(orders.get_order(self.h.db, 1).pay_status, "paid")
        notices = sum("оплачен онлайн" in t for t in self.h.sent_to(ADMIN, self.h.session.calls))
        self.assertEqual(notices, 1)
        await self.h.press(CUSTOMER, "🔄 Проверить оплату")
        await billing.poll_all(self.h.ctx, self.h.bot)  # и фоновая проверка не дублирует
        self.assertEqual(sum("оплачен онлайн" in t for t in self.h.sent_to(ADMIN, self.h.session.calls)), 1)
        self.assertEqual(sum("Оплата заказа №1 получена" in t for t in self.h.sent_to(CUSTOMER, self.h.session.calls)), 1)

    async def test_background_poll_confirms_payment(self):
        await self.place_online_order()
        self.h.yk.status[next(iter(self.h.yk.status))] = "succeeded"
        await billing.poll_all(self.h.ctx, self.h.bot)
        self.assertEqual(orders.get_order(self.h.db, 1).pay_status, "paid")

    async def test_failed_payment_alerts_staff(self):
        await self.place_online_order()
        self.h.yk.status[next(iter(self.h.yk.status))] = "canceled"
        await billing.poll_all(self.h.ctx, self.h.bot)
        self.assertEqual(orders.get_order(self.h.db, 1).pay_status, "failed")
        self.assertTrue(any("не прошёл" in t for t in self.h.sent_to(ADMIN, self.h.session.calls)))

    async def test_wrong_amount_is_not_trusted(self):
        await self.place_online_order()
        pid = next(iter(self.h.yk.status))
        self.h.yk.status[pid] = "succeeded"
        from decimal import Decimal
        self.h.yk.meta[pid] = (Decimal(1), self.h.yk.meta[pid][1])
        await billing.poll_all(self.h.ctx, self.h.bot)
        self.assertEqual(orders.get_order(self.h.db, 1).pay_status, "pending", "сумма не совпала — заказ не помечен оплаченным")
        self.assertTrue(any("не совпали" in t for t in self.h.sent_to(ADMIN, self.h.session.calls)))

    async def test_payment_for_cancelled_order_is_flagged(self):
        await self.place_online_order()
        orders.set_status(self.h.db, 1, "cancelled")
        self.h.yk.status[next(iter(self.h.yk.status))] = "succeeded"
        await billing.poll_all(self.h.ctx, self.h.bot)
        self.assertTrue(any("ОТМЕНЁННОМУ" in t for t in self.h.sent_to(ADMIN, self.h.session.calls)))

    async def test_other_users_cannot_check_my_payment(self):
        await self.place_online_order()
        data = next(b.callback_data for b in self.h.buttons() if b.text == "🔄 Проверить оплату")
        calls = await self.h.tap(OTHER, data)
        self.assertIn("не найден", self.h.alert(calls).text)

    async def test_payment_service_down_keeps_the_order(self):
        self.h.yk.fail = True
        calls = await self.place_online_order()
        self.assertIn("Не удалось создать ссылку", self.h.sent_to(CUSTOMER, calls)[0])
        self.assertEqual(orders.get_order(self.h.db, 1).id, 1)
        self.assertTrue(any("вручную" in t for t in self.h.sent_to(ADMIN, calls)))

    async def test_certificate_purchase_online(self):
        h = self.h
        await h.say(CUSTOMER, texts.BTN_CERT)
        calls = await h.press(CUSTOMER, "3 000 ₽")
        self.assertIn("телефон", h.last_text(calls))
        calls = await h.contact(CUSTOMER, "79175494941")
        link = next(b for b in h.buttons(calls) if b.text == "💳 Оплатить")
        self.assertIn("pay.example", link.url)
        item = h.yk.created[0]["items"][0]
        self.assertEqual((h.yk.created[0]["amount"], item.subject, item.mode), (3000, "payment", "advance"))
        h.yk.status[next(iter(h.yk.status))] = "succeeded"
        calls = await h.press(CUSTOMER, "🔄 Проверить оплату")
        docs = [c for c in h.session.calls if isinstance(c, SendDocument)]
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].chat_id, CUSTOMER)
        self.assertIn("3 000 ₽", docs[0].caption)
        code = h.db.scalar("SELECT code FROM certificates")
        self.assertIn(code, docs[0].caption)
        await h.press(CUSTOMER, "🔄 Проверить оплату")
        self.assertEqual(h.db.scalar("SELECT COUNT(*) FROM certificates"), 1, "второй проверкой сертификат не выпускается")
        self.assertEqual(len([c for c in h.session.calls if isinstance(c, SendDocument)]), 1)

    async def test_forged_certificate_amount_is_refused(self):
        calls = await self.h.tap(CUSTOMER, "ce:buy:1")
        self.assertIn("Такой суммы нет", self.h.alert(calls).text)
        self.assertEqual(self.h.yk.created, [])


class CertificateWithoutOnlinePaymentTests(FlowCase):
    async def test_purchase_goes_through_manager_and_is_issued_by_staff(self):
        h = self.h
        await h.say(CUSTOMER, texts.BTN_CERT)
        calls = await h.press(CUSTOMER, "5 000 ₽")
        staff = h.sent_to(ADMIN, calls)
        self.assertTrue("сертификат" in staff[0] and "5 000" in staff[0], staff)
        calls = await h.press(ADMIN, "🎁 Оплата получена — выдать сертификат")
        docs = [c for c in h.session.calls if isinstance(c, SendDocument)]
        self.assertEqual(docs[0].chat_id, CUSTOMER)
        calls = await h.press(ADMIN, "🎁 Оплата получена — выдать сертификат")
        self.assertIn("уже выдан", h.alert(calls).text)
        self.assertEqual(h.db.scalar("SELECT COUNT(*) FROM certificates"), 1)

    async def test_custom_amount_limits(self):
        h = self.h
        await h.say(CUSTOMER, texts.BTN_CERT)
        await h.press(CUSTOMER, "✏️ Другая сумма")
        self.assertIn("от 500", h.last_text(await h.say(CUSTOMER, "100")))
        self.assertIn("от 500", h.last_text(await h.say(CUSTOMER, "999999")))
        calls = await h.say(CUSTOMER, "2 500")
        self.assertIn("2 500", h.sent_to(ADMIN, calls)[0])

    async def test_balance_check_and_bruteforce_limit(self):
        h = self.h
        cert = certs.issue(h.db, h.ctx.shop, 3000)
        await h.say(CUSTOMER, texts.BTN_CERT)
        await h.press(CUSTOMER, "🔑 Проверить баланс")
        calls = await h.say(CUSTOMER, cert.code)
        self.assertIn("Баланс: <b>3 000", h.last_text(calls))
        for _ in range(5):
            await h.say(CUSTOMER, texts.BTN_CERT)
            await h.press(CUSTOMER, "🔑 Проверить баланс")
            calls = await h.say(CUSTOMER, "ABCD-ABCD-ABCD")
        self.assertIn("30 мин", h.last_text(calls))


class AdminTests(FlowCase):
    async def make_order(self, *, pay="cash", uid=CUSTOMER):
        await self.add("classic", uid=uid)
        await self.checkout_until_summary(uid=uid)
        return await self.h.press(uid, "💵 Наличными при получении" if pay == "cash" else "🔗 Перевод / ссылка от менеджера")

    async def test_panel_only_for_admin(self):
        calls = await self.h.say(ADMIN, "/admin")
        self.assertIn("Панель сотрудников", self.h.last_text(calls))
        calls = await self.h.say(CUSTOMER, "/admin")
        self.assertNotIn("Панель", self.h.last_text(calls))
        self.assertIn("Не совсем понял", self.h.last_text(calls))

    async def test_status_flow_notifies_customer(self):
        await self.make_order()
        calls = await self.h.press(ADMIN, "✅ Принять")
        self.assertEqual(orders.get_order(self.h.db, 1).status, "confirmed")
        self.assertIn("подтверждён", self.h.sent_to(CUSTOMER, calls)[0])
        await self.h.press(ADMIN, "💐 Собираем")
        calls = await self.h.press(ADMIN, "🛍 Готов к выдаче")
        self.assertIn("готов", self.h.sent_to(CUSTOMER, calls)[0])
        self.assertIn("Земляной Вал", self.h.sent_to(CUSTOMER, calls)[0])
        calls = await self.h.press(ADMIN, "🏁 Выполнен")
        self.assertEqual(orders.get_order(self.h.db, 1).status, "done")
        self.assertTrue(self.h.has_button("⭐ Оценить заказ", calls), "после выполнения просим отзыв")

    async def test_stale_status_button_is_harmless(self):
        calls = await self.make_order()
        accept = next(b.callback_data for b in self.h.buttons(calls) if b.text == "✅ Принять")
        await self.h.tap(ADMIN, accept)
        calls = await self.h.tap(ADMIN, accept)
        self.assertIn("уже изменён", self.h.alert(calls).text)

    async def test_cancel_and_paid_refund_warning(self):
        await self.make_order(pay="transfer")
        await self.h.press(ADMIN, "💰 Оплата получена")
        self.assertEqual(orders.get_order(self.h.db, 1).pay_status, "paid")
        calls = await self.h.press(ADMIN, "❌ Отменить")
        self.assertTrue(any("верните деньги" in t for t in self.h.texts(calls)), self.h.texts(calls))
        self.assertIn("отменён", self.h.sent_to(CUSTOMER, calls)[0])

    async def test_non_admin_cannot_use_admin_buttons_even_with_forged_data(self):
        """Главная проверка безопасности: в старом боте любой мог дёргать такие кнопки."""
        await self.make_order()
        forged = [Adm_(act="status", id=1, arg="done"), Adm_(act="status", id=1, arg="cancelled"),
                  Adm_(act="paid", id=1), Adm_(act="toggle", arg="classic"), Adm_(act="pause"),
                  Adm_(act="review_hide", id=1), Adm_(act="order", id=1), Adm_(act="msg", id=ADMIN)]
        for data in forged:
            calls = await self.h.tap(OTHER, data)
            self.assertIn("устарела", self.h.alert(calls).text, data)
        order = orders.get_order(self.h.db, 1)
        self.assertEqual((order.status, order.pay_status), ("new", "none"))
        self.assertTrue(catalog.get_product(self.h.db, "classic").in_stock)
        self.assertNotEqual(self.h.db.get_meta("paused"), "1")
        # и админ-команды у чужого не работают
        for command in ("/pause", "/reload", "/cert new 5000", "/order 1", "/photo classic"):
            calls = await self.h.say(OTHER, command)
            self.assertNotIn("Панель", " ".join(self.h.texts(calls)))
        self.assertEqual(self.h.db.scalar("SELECT COUNT(*) FROM certificates"), 0)
        self.assertNotEqual(self.h.db.get_meta("paused"), "1")

    async def test_lists_and_open_order(self):
        await self.make_order()
        await self.h.say(ADMIN, "/admin")
        calls = await self.h.press(ADMIN, "📦 Новые заказы")
        self.assertIn("Новые заказы</b> — 1", self.h.last_text(calls))
        calls = await self.h.press(ADMIN, next(b.text for b in self.h.buttons(calls) if b.text.startswith("№1")))
        self.assertIn("Заказ №1", self.h.last_text(calls))
        self.assertIn("tg://user?id=%d" % CUSTOMER, self.h.last_text(calls))
        calls = await self.h.say(ADMIN, "/order 1")
        self.assertIn("Заказ №1", self.h.last_text(calls))
        self.assertIn("Напишите так", self.h.last_text(await self.h.say(ADMIN, "/order")))
        await self.h.say(ADMIN, "/admin")
        calls = await self.h.press(ADMIN, "📅 На сегодня")
        self.assertIn("На сегодня</b> — 1", self.h.last_text(calls))
        calls = await self.h.press(ADMIN, "📅 На завтра")
        self.assertIn("Пока ничего нет", self.h.last_text(calls))

    async def test_find_by_number(self):
        await self.make_order()
        await self.h.say(ADMIN, "/admin")
        await self.h.press(ADMIN, "🔎 Заказ по номеру")
        self.assertIn("не найден", self.h.last_text(await self.h.say(ADMIN, "77")))
        self.assertIn("Заказ №1", self.h.last_text(await self.h.say(ADMIN, "1")))

    async def test_stock_toggle_affects_customers(self):
        await self.h.say(ADMIN, "/admin")
        await self.h.press(ADMIN, "🛒 Наличие")
        calls = await self.h.press(ADMIN, "✅ Классика")
        self.assertFalse(catalog.get_product(self.h.db, "classic").in_stock)
        self.assertTrue(self.h.has_button("⛔ Классика", calls))
        calls = await self.h.tap(CUSTOMER, Prod(act="add", sku="classic").pack())
        self.assertIn("нет в наличии", self.h.alert(calls).text)
        await self.h.press(ADMIN, "⛔ Классика")
        self.assertTrue(catalog.get_product(self.h.db, "classic").in_stock)

    async def test_stats(self):
        await self.make_order()
        await self.h.say(ADMIN, "/admin")
        calls = await self.h.press(ADMIN, "📊 Статистика")
        text = self.h.last_text(calls)
        self.assertIn("Заказов (без отменённых): <b>1</b>", text)
        self.assertIn("1 990", text)
        self.assertIn("Классика", text)
        calls = await self.h.press(ADMIN, "Сегодня")
        self.assertIn("сегодня", self.h.last_text(calls))

    async def test_pause_from_panel(self):
        await self.h.say(ADMIN, "/admin")
        calls = await self.h.press(ADMIN, "⏸ Пауза заказов")
        self.assertTrue(self.h.ctx.paused)
        self.assertIn("приостановлен", self.h.last_text(calls))
        calls = await self.h.press(ADMIN, "▶️ Возобновить приём заказов")
        self.assertFalse(self.h.ctx.paused)

    async def test_certificates_by_command(self):
        await self.h.say(ADMIN, "/cert")
        calls = await self.h.say(ADMIN, "/cert new 3000")
        code = self.h.db.scalar("SELECT code FROM certificates")
        self.assertIn(code, self.h.last_text(calls))
        self.assertEqual(len([c for c in self.h.session.calls if isinstance(c, SendDocument)]), 1)
        self.assertIn("Баланс: <b>3 000", self.h.last_text(await self.h.say(ADMIN, f"/cert {code}")))
        self.assertIn("Остаток: <b>1 500", self.h.last_text(await self.h.say(ADMIN, f"/cert {code} списать 1500")))
        self.assertIn("недостаточно", self.h.last_text(await self.h.say(ADMIN, f"/cert {code} списать 9999")))
        self.assertIn("не найден", self.h.last_text(await self.h.say(ADMIN, "/cert AAAA-BBBB-CCCC")))
        self.assertIn("от 500", self.h.last_text(await self.h.say(ADMIN, "/cert new 10")))

    async def test_photo_upload(self):
        calls = await self.h.photo(ADMIN, "/photo classic")
        self.assertIn("сохранено", self.h.last_text(calls))
        path = Path(self.h.ctx.settings.photos_dir) / "classic.jpg"
        self.assertTrue(path.is_file())
        self.assertEqual(catalog.get_product(self.h.db, "classic").photo, "classic.jpg")
        self.assertIn("Нет товара", self.h.last_text(await self.h.photo(ADMIN, "/photo ../../evil")))
        self.assertFalse((path.parent.parent / "evil.jpg").exists())
        calls = await self.h.photo(OTHER, "/photo mini")
        self.assertFalse((Path(self.h.ctx.settings.photos_dir) / "mini.jpg").exists(), "чужой не может загружать фото")
        # карточка теперь с фото, а file_id запоминается
        await self.h.say(CUSTOMER, texts.BTN_CATALOG)
        await self.h.press(CUSTOMER, "💐 Букеты")
        await self.h.press(CUSTOMER, "Показать ещё ▶️")
        self.assertIsNotNone(catalog.get_product(self.h.db, "classic").photo)

    async def test_reload_survives_a_broken_file(self):
        calls = await self.h.say(ADMIN, "/reload")
        self.assertIn("Товаров: 20", self.h.last_text(calls))
        root = Path(self.h.ctx.settings.root)
        (root / "shop.toml").write_text("[shop\nbroken", encoding="utf-8")
        calls = await self.h.say(ADMIN, "/reload")
        self.assertIn("всё осталось как было", self.h.last_text(calls))
        self.assertEqual(self.h.ctx.shop.name, "MuzFlowers")


def Adm_(**kw) -> str:
    from flowerbot.callbacks import Adm
    return Adm(**kw).pack()


class MyOrdersAndReviewsTests(FlowCase):
    async def make_order(self, uid=CUSTOMER):
        await self.add("classic", uid=uid)
        await self.checkout_until_summary(uid=uid)
        await self.h.press(uid, "💵 Наличными при получении")

    async def test_list_open_repeat(self):
        await self.make_order()
        calls = await self.h.say(CUSTOMER, texts.BTN_ORDERS)
        self.assertIn("Нажмите на заказ", self.h.last_text(calls))
        calls = await self.h.press(CUSTOMER, next(b.text for b in self.h.buttons(calls) if b.text.startswith("№1")))
        self.assertIn("Заказ №1", self.h.last_text(calls))
        calls = await self.h.press(CUSTOMER, "🔁 Повторить заказ")
        self.assertEqual(catalog.cart_count(self.h.db, CUSTOMER), 1)
        self.assertIn("Добавил в корзину", self.h.texts(calls)[0])

    async def test_repeat_skips_unavailable(self):
        await self.make_order()
        catalog.set_in_stock(self.h.db, "classic", False)
        await self.h.say(CUSTOMER, "/orders")
        await self.h.press(CUSTOMER, next(b.text for b in self.h.buttons() if b.text.startswith("№1")))
        calls = await self.h.press(CUSTOMER, "🔁 Повторить заказ")
        self.assertEqual(catalog.cart_count(self.h.db, CUSTOMER), 0)
        self.assertIn("Сейчас недоступно", self.h.texts(calls)[0])

    async def test_empty_orders(self):
        self.assertIn("Пока заказов нет", self.h.last_text(await self.h.say(CUSTOMER, texts.BTN_ORDERS)))

    async def test_customer_can_cancel_new_unpaid_order_only(self):
        await self.make_order()
        await self.h.say(CUSTOMER, "/orders")
        await self.h.press(CUSTOMER, next(b.text for b in self.h.buttons() if b.text.startswith("№1")))
        calls = await self.h.press(CUSTOMER, "❌ Отменить заказ")
        self.assertEqual(orders.get_order(self.h.db, 1).status, "cancelled")
        self.assertTrue(any("отменил заказ №1" in t for t in self.h.sent_to(ADMIN, calls)))
        # принятый заказ отменить кнопкой нельзя
        await self.make_order()
        orders.set_status(self.h.db, 2, "confirmed")
        calls = await self.h.tap(CUSTOMER, "mo:cancel:2")
        self.assertIn("нельзя отменить", self.h.alert(calls).text)
        self.assertEqual(orders.get_order(self.h.db, 2).status, "confirmed")

    async def test_other_users_orders_are_invisible(self):
        await self.make_order()
        for data in ("mo:open:1", "mo:repeat:1", "mo:cancel:1", "mo:ask:1", "mo:review:1"):
            calls = await self.h.tap(OTHER, data)
            self.assertTrue(self.h.alert(calls) is not None, data)
        self.assertEqual(orders.get_order(self.h.db, 1).status, "new")
        self.assertEqual(catalog.cart_count(self.h.db, OTHER), 0)
        self.assertNotIn("Мария", " ".join(self.h.texts(calls)))

    async def test_review_flow_and_low_rating_alert(self):
        h = self.h
        await h.say(CUSTOMER, texts.BTN_REVIEWS)
        self.assertIn("Здесь пока нет отзывов", h.last_text(await h.press(CUSTOMER, "📖 Читать отзывы")))
        await h.press(CUSTOMER, "✍️ Оставить отзыв")
        await h.press(CUSTOMER, "⭐")
        self.assertIn("хотя бы пару слов", h.last_text(await h.say(CUSTOMER, "ок")))
        calls = await h.say(CUSTOMER, "Розы осыпались на второй день, доставили с опозданием")
        self.assertIn("Спасибо за отзыв", h.texts(calls)[0])
        staff = h.sent_to(ADMIN, calls)[0]
        self.assertIn("Низкая оценка", staff)
        self.assertIn("осыпались", staff)
        await h.say(CUSTOMER, texts.BTN_REVIEWS)
        calls = await h.press(CUSTOMER, "📖 Читать отзывы")
        self.assertIn("осыпались", h.last_text(calls))
        self.assertIn("— Иван", h.last_text(calls))

    async def test_order_review_only_for_done_own_order(self):
        await self.make_order()
        calls = await self.h.tap(CUSTOMER, "mo:review:1")
        self.assertIn("только выполненный", self.h.alert(calls).text)
        orders.set_status(self.h.db, 1, "confirmed")
        orders.set_status(self.h.db, 1, "done")
        await self.h.tap(CUSTOMER, "mo:review:1")
        await self.h.press(CUSTOMER, "⭐⭐⭐⭐⭐")
        calls = await self.h.say(CUSTOMER, "Всё было отлично, спасибо!")
        self.assertIn("заказ №1", self.h.sent_to(ADMIN, calls)[0])
        self.assertEqual(self.h.db.scalar("SELECT order_id FROM reviews"), 1)

    async def test_admin_can_hide_review(self):
        crm.add_review(self.h.db, OTHER, "Спамер", "купите дёшево", 5)
        await self.h.say(ADMIN, "/admin")
        await self.h.press(ADMIN, "⭐ Отзывы")
        await self.h.press(ADMIN, "🙈 Скрыть отзыв #1")
        calls = await self.h.say(CUSTOMER, texts.BTN_REVIEWS)
        self.assertIn("пока нет отзывов", self.h.last_text(await self.h.press(CUSTOMER, "📖 Читать отзывы")))


class ThrottleTests(unittest.IsolatedAsyncioTestCase):
    async def test_flood_is_stopped_but_admin_is_not(self):
        from flowerbot.middlewares import Throttle
        now = [0.0]
        h = Harness(throttle=Throttle(limit=3, window=10, clock=lambda: now[0]))
        self.addCleanup(h.close)
        for _ in range(3):
            await h.say(CUSTOMER, "/help")
        calls = await h.say(CUSTOMER, "/help")
        self.assertIn("Слишком много", h.last_text(calls))
        calls = await h.say(CUSTOMER, "/help")
        self.assertEqual(calls, [], "дальше молчим — не усиливаем спам")
        calls = await h.tap(CUSTOMER, Prod(act="add", sku="mini").pack())
        self.assertIn("Не так быстро", h.alert(calls).text)
        self.assertEqual(catalog.cart_count(h.db, CUSTOMER), 0)
        for _ in range(10):
            calls = await h.say(ADMIN, "/help")
        self.assertTrue(h.texts(calls))
        now[0] = 11.0
        self.assertTrue(h.texts(await h.say(CUSTOMER, "/help")), "через 10 секунд снова можно")


class ErrorHandlingTests(unittest.IsolatedAsyncioTestCase):
    async def test_unexpected_error_is_reported_without_leaking_details(self):
        h = Harness()
        self.addCleanup(h.close)
        h.db.close()  # ломаем базу: любое действие упадёт
        calls = await h.say(CUSTOMER, "/start")
        text = " ".join(h.texts(calls))
        self.assertIn("Что-то пошло не так", h.sent_to(CUSTOMER, calls)[0] if h.sent_to(CUSTOMER, calls) else text)
        self.assertNotIn("Traceback", text)
        self.assertNotIn("sqlite", text.lower())


if __name__ == "__main__":
    unittest.main()
