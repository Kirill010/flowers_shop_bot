"""Заказы, деньги, сертификаты и ЮKassa: то, где ошибка стоит денег."""
from __future__ import annotations

import base64
import dataclasses
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from aiohttp import web
from aiohttp.test_utils import TestServer
from PIL import Image

from flowerbot import catalog, certs, orders, schedule
from flowerbot.config import Promo
from flowerbot.orders import Draft, OrderError, PriceChanged
from flowerbot.yookassa import (ReceiptItem, YooKassaClient, YooKassaError, certificate_receipt_items,
                                order_receipt_items)

from .helpers import CUSTOMER, NOW, OTHER, TODAY, make_db, real_shop

TOMORROW = TODAY + timedelta(days=1)


def draft(**kw) -> Draft:
    base = dict(user_id=CUSTOMER, name="Иван", phone="+79175494941", method="pickup", zone="", address="",
                recipient_name="", recipient_phone="", date=TODAY, slot=schedule.ASAP, comment="", pay_method="cash")
    base.update(kw)
    return Draft(**base)


def place(db, shop, d=None, online=False, expected_total=None, now=NOW):
    return orders.place_order(db, shop, d or draft(), now_utc=now, online_enabled=online, expected_total=expected_total)


def fill(db, *items, user=CUSTOMER):
    for sku, qty in items:
        for _ in range(qty):
            assert catalog.cart_add(db, user, sku) is None


class PlaceOrderTests(unittest.TestCase):
    def setUp(self):
        self.db = make_db()
        self.shop = real_shop()

    def test_pickup_cash(self):
        fill(self.db, ("classic", 1), ("mini", 2))
        oid = place(self.db, self.shop)
        order = orders.get_order(self.db, oid)
        self.assertEqual((order.goods, order.delivery, order.total, order.to_pay), (3970, 0, 3970, 3970))
        self.assertEqual((order.status, order.pay_status, order.pay_method), ("new", "none", "cash"))
        self.assertEqual([(i.name, i.price, i.qty) for i in order.items], [("Классика", 1990, 1), ("Мини", 990, 2)])
        self.assertEqual(catalog.cart_lines(self.db, CUSTOMER), [], "корзина очищена")
        self.assertEqual(order.address, "")

    def test_delivery_fee_and_free_threshold(self):
        zone = self.shop.zones[0].name
        d = draft(method="delivery", zone=zone, address="Москва, Тверская 1, кв. 5", slot="17:00–19:00")
        fill(self.db, ("classic", 1))
        order = orders.get_order(self.db, place(self.db, self.shop, d))
        self.assertEqual((order.delivery, order.total), (300, 2290))
        fill(self.db, ("classic", 2))
        order = orders.get_order(self.db, place(self.db, self.shop, d))
        self.assertEqual((order.goods, order.delivery, order.total), (3980, 0, 3980), "от 3 000 ₽ доставка бесплатна")

    def test_delivery_needs_address_and_valid_zone(self):
        fill(self.db, ("classic", 1))
        zone = self.shop.zones[0].name
        with self.assertRaises(OrderError):
            place(self.db, self.shop, draft(method="delivery", zone=zone, address="д", slot="17:00–19:00"))
        with self.assertRaises(OrderError):
            place(self.db, self.shop, draft(method="delivery", zone="Луна", address="Москва, Тверская 1", slot="17:00–19:00"))
        with self.assertRaises(OrderError):
            place(self.db, dataclasses.replace(self.shop, delivery_enabled=False),
                  draft(method="delivery", zone=zone, address="Москва, Тверская 1", slot="17:00–19:00"))
        self.assertEqual(len(catalog.cart_lines(self.db, CUSTOMER)), 1, "ошибка не стирает корзину")

    def test_other_day_requires_prepayment(self):
        fill(self.db, ("classic", 1))
        for method in ("cash", "card"):
            with self.subTest(method), self.assertRaises(OrderError):
                place(self.db, self.shop, draft(date=TOMORROW, slot="09:00–11:00", pay_method=method))
        with self.assertRaises(OrderError) as ctx:
            place(self.db, self.shop, draft(date=TOMORROW, slot="09:00–11:00", pay_method="online"), online=False)
        self.assertIn("недоступен", str(ctx.exception))
        order = orders.get_order(self.db, place(self.db, self.shop, draft(date=TOMORROW, slot="09:00–11:00", pay_method="transfer")))
        self.assertEqual(order.pay_status, "pending")

    def test_prepay_rule_can_be_switched_off(self):
        fill(self.db, ("classic", 1))
        shop = dataclasses.replace(self.shop, prepay_future_orders=False)
        order = orders.get_order(self.db, place(self.db, shop, draft(date=TOMORROW, slot="09:00–11:00")))
        self.assertEqual(order.pay_status, "none")

    def test_online_method_only_when_enabled(self):
        fill(self.db, ("classic", 1))
        with self.assertRaises(OrderError):
            place(self.db, self.shop, draft(pay_method="online"), online=False)
        order = orders.get_order(self.db, place(self.db, self.shop, draft(pay_method="online"), online=True))
        self.assertEqual(order.pay_status, "pending")

    def test_cash_can_be_disabled(self):
        fill(self.db, ("classic", 1))
        with self.assertRaises(OrderError):
            place(self.db, dataclasses.replace(self.shop, allow_pay_on_receipt=False))

    def test_price_change_is_caught_and_nothing_is_saved(self):
        fill(self.db, ("classic", 1))
        shown = 1990
        self.db.run("UPDATE products SET price = 2190 WHERE sku = 'classic'")
        with self.assertRaises(PriceChanged) as ctx:
            place(self.db, self.shop, expected_total=shown)
        self.assertEqual(ctx.exception.new_total, 2190)
        self.assertEqual(self.db.scalar("SELECT COUNT(*) FROM orders"), 0)
        self.assertEqual(len(catalog.cart_lines(self.db, CUSTOMER)), 1)

    def test_unavailable_products_block_the_order(self):
        fill(self.db, ("classic", 1))
        catalog.set_in_stock(self.db, "classic", False)
        with self.assertRaises(OrderError) as ctx:
            place(self.db, self.shop)
        self.assertIn("Классика", str(ctx.exception))
        catalog.set_in_stock(self.db, "classic", True)
        self.db.run("INSERT INTO cart (user_id, sku, qty) VALUES (?, 'tulips', 1)", (CUSTOMER,))
        with self.assertRaises(OrderError):
            place(self.db, self.shop)  # тюльпаны вне сезона
        self.db.run("DELETE FROM cart WHERE sku = 'tulips'")
        self.db.run("INSERT INTO cart (user_id, sku, qty) VALUES (?, 'orchid', 1)", (CUSTOMER,))
        with self.assertRaises(OrderError):
            place(self.db, self.shop)  # цена по запросу

    def test_empty_cart_and_minimum(self):
        with self.assertRaises(OrderError):
            place(self.db, self.shop)
        fill(self.db, ("mini", 1))
        with self.assertRaises(OrderError) as ctx:
            place(self.db, dataclasses.replace(self.shop, min_order=1500))
        self.assertIn("Минимальная", str(ctx.exception))

    def test_time_slot_must_exist_and_not_be_in_the_past(self):
        fill(self.db, ("mini", 1))
        for slot in ("09:00–11:00", "25:00–26:00", "", "'; DROP TABLE orders; --"):
            with self.subTest(slot), self.assertRaises(OrderError):
                place(self.db, self.shop, draft(slot=slot))
        with self.assertRaises(OrderError):
            place(self.db, self.shop, draft(date=TODAY - timedelta(days=1), slot="09:00–11:00"))

    def test_pause(self):
        fill(self.db, ("mini", 1))
        with self.assertRaises(OrderError):
            place(self.db, dataclasses.replace(self.shop, paused=True))
        self.db.set_meta("paused", "1")
        with self.assertRaises(OrderError):
            place(self.db, self.shop)
        self.db.set_meta("paused", "0")
        place(self.db, self.shop)

    def test_active_orders_limit(self):
        shop = dataclasses.replace(self.shop, max_active_orders=2)
        for _ in range(2):
            fill(self.db, ("mini", 1))
            place(self.db, shop)
        fill(self.db, ("mini", 1))
        with self.assertRaises(OrderError):
            place(self.db, shop)
        orders.set_status(self.db, 1, "cancelled")
        place(self.db, shop)

    def test_promo_code(self):
        shop = dataclasses.replace(self.shop, promos=(Promo("ВЕСНА", percent=10, min_order=2000),
                                                       Promo("ПЕРВЫЙ", percent=15, first_order_only=True)))
        fill(self.db, ("classic", 2))
        order = orders.get_order(self.db, place(self.db, shop, draft(promo="весна")))
        self.assertEqual((order.goods, order.discount, order.total, order.promo), (3980, 398, 3582, "ВЕСНА"))
        fill(self.db, ("classic", 1))
        with self.assertRaises(OrderError):
            place(self.db, shop, draft(promo="ПЕРВЫЙ"))  # уже был заказ
        with self.assertRaises(OrderError):
            place(self.db, shop, draft(promo="нетТакого"))
        # скидка может сделать доставку платной: порог считается после скидки
        shop2 = dataclasses.replace(self.shop, promos=(Promo("X", percent=10),))
        fill(self.db, ("mini", 4), user=OTHER)
        d = draft(user_id=OTHER, method="delivery", zone=self.shop.zones[0].name, address="Москва, Арбат 10",
                  slot="17:00–19:00", promo="X")
        order = orders.get_order(self.db, place(self.db, shop2, d))
        self.assertEqual((order.goods, order.discount, order.delivery), (3960, 396, 0))

    def test_order_belongs_to_its_owner(self):
        fill(self.db, ("mini", 1))
        oid = place(self.db, self.shop)
        self.assertIsNotNone(orders.get_user_order(self.db, oid, CUSTOMER))
        self.assertIsNone(orders.get_user_order(self.db, oid, OTHER), "чужой заказ не отдаётся")


class CertificateInOrderTests(unittest.TestCase):
    def setUp(self):
        self.db = make_db()
        self.shop = real_shop()

    def test_partial_certificate_and_restore_on_cancel(self):
        cert = certs.issue(self.db, self.shop, 1000, buyer_id=OTHER)
        fill(self.db, ("classic", 1))
        order = orders.get_order(self.db, place(self.db, self.shop, draft(cert_code=cert.code.lower())))
        self.assertEqual((order.total, order.cert_amount, order.to_pay), (1990, 1000, 990))
        self.assertEqual(certs.get_by_code(self.db, cert.code).balance, 0)
        self.assertEqual(certs.get_by_code(self.db, cert.code).status, "used")
        orders.set_status(self.db, order.id, "cancelled")
        self.assertEqual(certs.get_by_code(self.db, cert.code).balance, 1000)
        self.assertEqual(certs.get_by_code(self.db, cert.code).status, "active")
        self.assertEqual(certs.release_for_order(self.db, order.id), 0, "повторный возврат ничего не меняет")
        self.assertEqual(certs.get_by_code(self.db, cert.code).balance, 1000)

    def test_certificate_covers_everything(self):
        cert = certs.issue(self.db, self.shop, 3000)
        fill(self.db, ("mini", 1))
        with self.assertRaises(OrderError):
            place(self.db, self.shop, draft(cert_code=cert.code, pay_method="cash"))  # платить нечем-нечего: только «сертификат»
        order = orders.get_order(self.db, place(self.db, self.shop, draft(cert_code=cert.code, pay_method="cert")))
        self.assertEqual((order.to_pay, order.pay_status), (0, "paid"))
        self.assertEqual(certs.get_by_code(self.db, cert.code).balance, 2010)

    def test_cert_method_without_cert_is_rejected(self):
        fill(self.db, ("mini", 1))
        with self.assertRaises(OrderError):
            place(self.db, self.shop, draft(pay_method="cert"))

    def test_bad_certificates(self):
        fill(self.db, ("mini", 1))
        with self.assertRaises(OrderError):
            place(self.db, self.shop, draft(cert_code="AAAA-BBBB-CCCC"))
        cert = certs.issue(self.db, self.shop, 500)
        later = NOW + timedelta(days=400)
        self.db.clock = lambda: later
        with self.assertRaises(OrderError) as ctx:
            orders.place_order(self.db, self.shop, draft(cert_code=cert.code, date=later.date() + timedelta(days=1),
                                                         slot="09:00–11:00", pay_method="transfer"),
                               now_utc=later, online_enabled=False)
        self.assertIn("Срок", str(ctx.exception))

    def test_failure_inside_transaction_rolls_everything_back(self):
        cert = certs.issue(self.db, self.shop, 500)
        fill(self.db, ("mini", 1))
        with mock.patch.object(certs, "redeem", side_effect=certs.CertError("сбой")):
            with self.assertRaises(OrderError):
                place(self.db, self.shop, draft(cert_code=cert.code))
        self.assertEqual(self.db.scalar("SELECT COUNT(*) FROM orders"), 0)
        self.assertEqual(self.db.scalar("SELECT COUNT(*) FROM order_items"), 0)
        self.assertEqual(len(catalog.cart_lines(self.db, CUSTOMER)), 1)
        self.assertEqual(certs.get_by_code(self.db, cert.code).balance, 500)


class StatusAndPaymentTests(unittest.TestCase):
    def setUp(self):
        self.db = make_db()
        self.shop = real_shop()
        fill(self.db, ("classic", 1))
        self.oid = place(self.db, self.shop)

    def test_happy_path(self):
        for status in ("confirmed", "assembling", "ready", "done"):
            self.assertEqual(orders.set_status(self.db, self.oid, status, by_user=1).status, status)
        self.assertEqual([r["status"] for r in self.db.all("SELECT status FROM order_events ORDER BY id")],
                         ["new", "confirmed", "assembling", "ready", "done"])

    def test_forbidden_jumps(self):
        self.assertIsNone(orders.set_status(self.db, self.oid, "done"), "нельзя выполнить неподтверждённый")
        self.assertIsNone(orders.set_status(self.db, self.oid, "on_way"))
        self.assertIsNone(orders.set_status(self.db, self.oid, "nonsense"))
        self.assertIsNone(orders.set_status(self.db, 999, "confirmed"))
        orders.set_status(self.db, self.oid, "cancelled")
        for status in orders.STATUS:
            self.assertIsNone(orders.set_status(self.db, self.oid, status), "отменённый заказ не оживает")

    def test_mark_paid_only_once(self):
        self.assertTrue(orders.mark_paid(self.db, self.oid))
        self.assertFalse(orders.mark_paid(self.db, self.oid))
        orders.set_pay_status(self.db, self.oid, "failed")
        self.assertEqual(orders.get_order(self.db, self.oid).pay_status, "paid", "оплаченный не становится «не прошёл»")

    def test_payment_finishes_once(self):
        orders.add_payment(self.db, "pay-1", "order", CUSTOMER, 1990, order_id=self.oid)
        orders.add_payment(self.db, "pay-1", "order", CUSTOMER, 1990, order_id=self.oid)
        self.assertEqual(len(orders.pending_payments(self.db)), 1)
        self.assertTrue(orders.finish_payment(self.db, "pay-1", "succeeded"))
        self.assertFalse(orders.finish_payment(self.db, "pay-1", "succeeded"), "второй раз — не выполняем заказ повторно")
        self.assertFalse(orders.finish_payment(self.db, "pay-1", "canceled"))
        self.assertEqual(orders.pending_payments(self.db), [])

    def test_stats_and_lists(self):
        fill(self.db, ("mini", 3))
        oid2 = place(self.db, self.shop)
        orders.set_status(self.db, oid2, "cancelled")
        fill(self.db, ("classic", 1), ("mini", 1))
        place(self.db, self.shop)
        s = orders.stats(self.db, "2000-01-01 00:00:00")
        self.assertEqual((s["orders"], s["revenue"], s["average"], s["cancelled"]), (2, 4970, 2485, 1))
        self.assertEqual(s["top"][0], ("Классика", 2))
        self.assertEqual(len(orders.list_orders(self.db, user_id=CUSTOMER)), 3)
        self.assertEqual(len(orders.list_orders(self.db, statuses=("new",), day=TODAY)), 2)
        self.assertEqual(orders.list_orders(self.db, user_id=OTHER), [])


class CertificateTests(unittest.TestCase):
    def setUp(self):
        self.db = make_db()
        self.shop = real_shop()

    def test_codes(self):
        code = certs.new_code()
        self.assertRegex(code, r"^[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}$")
        self.assertEqual(len({certs.new_code() for _ in range(200)}), 200)
        self.assertEqual(certs.normalize_code(" abcd efgh-jklm "), "ABCD-EFGH-JKLM")
        for bad in ("", "ABCD", "ABCD-EFGH-JKL0", "ABCD-EFGH-JKLI", None, "ABCD-EFGH-JKLM-X"):
            self.assertIsNone(certs.normalize_code(bad), bad)

    def test_issue_is_idempotent_per_payment(self):
        a = certs.issue(self.db, self.shop, 3000, buyer_id=CUSTOMER, payment_id="pay-A")
        b = certs.issue(self.db, self.shop, 3000, buyer_id=CUSTOMER, payment_id="pay-A")
        self.assertEqual(a.code, b.code)
        self.assertEqual(self.db.scalar("SELECT COUNT(*) FROM certificates"), 1, "один платёж — один сертификат")
        self.assertEqual((a.amount, a.balance, a.status), (3000, 3000, "active"))
        self.assertEqual((a.expires - NOW).days, 365)
        with self.assertRaises(certs.CertError):
            certs.issue(self.db, self.shop, 0)

    def test_bruteforce_protection(self):
        cert = certs.issue(self.db, self.shop, 1000)
        for i in range(4):
            found, message = certs.check_code(self.db, CUSTOMER, f"AAAA-BBBB-CCC{i + 2}")
            self.assertIsNone(found)
            self.assertIn("не найден", message)
        found, message = certs.check_code(self.db, CUSTOMER, "AAAA-BBBB-CCC9")
        self.assertIsNone(found)
        self.assertIn("30 мин", message)
        # пока блок — даже верный код не принимается
        found, message = certs.check_code(self.db, CUSTOMER, cert.code)
        self.assertIsNone(found)
        self.assertIn("Слишком много", message)
        # другой клиент не заблокирован
        self.assertEqual(certs.check_code(self.db, OTHER, cert.code)[0].code, cert.code)
        # через 31 минуту можно снова
        later = NOW + timedelta(minutes=31)
        self.db.clock = lambda: later
        found, message = certs.check_code(self.db, CUSTOMER, cert.code)
        self.assertEqual((found.code, message), (cert.code, ""))

    def test_success_resets_counter(self):
        cert = certs.issue(self.db, self.shop, 1000)
        for _ in range(3):
            certs.check_code(self.db, CUSTOMER, "ZZZZ-ZZZZ-ZZZZ")
        self.assertIsNotNone(certs.check_code(self.db, CUSTOMER, cert.code)[0])
        for _ in range(4):
            certs.check_code(self.db, CUSTOMER, "ZZZZ-ZZZZ-ZZZZ")
        self.assertIsNotNone(certs.check_code(self.db, CUSTOMER, cert.code)[0], "счётчик обнулился после успеха")

    def test_redeem_rules(self):
        cert = certs.issue(self.db, self.shop, 1000)
        with self.assertRaises(certs.CertError):
            certs.redeem(self.db, cert.code, 1001)
        with self.assertRaises(certs.CertError):
            certs.redeem(self.db, cert.code, 0)
        with self.assertRaises(certs.CertError):
            certs.redeem(self.db, "AAAA-BBBB-CCCC", 10)
        self.assertEqual(certs.get_by_code(self.db, cert.code).balance, 1000)
        self.assertEqual(certs.redeem(self.db, cert.code, 400, note="в магазине").balance, 600)
        self.assertEqual(certs.redeem(self.db, cert.code, 600).status, "used")
        with self.assertRaises(certs.CertError):
            certs.redeem(self.db, cert.code, 1)
        self.assertEqual(self.db.scalar("SELECT SUM(delta) FROM cert_ledger"), 0, "журнал сходится с балансом")

    def test_expired(self):
        cert = certs.issue(self.db, self.shop, 1000)
        self.db.clock = lambda: NOW + timedelta(days=366)
        found, message = certs.check_code(self.db, CUSTOMER, cert.code)
        self.assertIn("Срок", message)
        with self.assertRaises(certs.CertError):
            certs.redeem(self.db, cert.code, 100)

    def test_picture(self):
        import io
        cert = certs.issue(self.db, self.shop, 3000)
        data = certs.render_png(cert, self.shop)
        self.assertTrue(data.startswith(b"\x89PNG"))
        image = Image.open(io.BytesIO(data))
        self.assertEqual(image.size, (1200, 760))
        self.assertGreater(len(data), 20_000, "картинка не пустая")


class YooKassaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.seen = []
        self.reply = None
        app = web.Application()
        app.router.add_post("/v3/payments", self.create)
        app.router.add_get("/v3/payments/{pid}", self.get)
        self.server = TestServer(app)
        await self.server.start_server()
        self.client = YooKassaClient("123456", "test_" + "A" * 30, base_url=str(self.server.make_url("/v3")))

    async def asyncTearDown(self):
        await self.client.close()
        await self.server.close()

    async def create(self, request):
        body = await request.json()
        self.seen.append((request.headers, body))
        if self.reply:
            return self.reply
        return web.json_response({"id": "2f0a1c3b-000f-5000-8000-1d0d5b9e0001", "status": "pending", "paid": False,
                                  "amount": body["amount"], "metadata": body["metadata"],
                                  "confirmation": {"type": "redirect", "confirmation_url": "https://pay.example/x"}})

    async def get(self, request):
        return web.json_response({"id": request.match_info["pid"], "status": "succeeded", "paid": True,
                                  "amount": {"value": "1990.00", "currency": "RUB"}, "metadata": {"order_id": "7"}})

    async def test_create_payment_request_is_correct(self):
        items = [ReceiptItem("Классика", 1990)]
        info = await self.client.create_payment(amount=1990, description="Заказ №7", return_url="https://t.me/bot",
                                                metadata={"order_id": 7}, idempotence_key="order-7-1",
                                                customer_phone="+7 917 549-49-41", items=items)
        headers, body = self.seen[0]
        expected = "Basic " + base64.b64encode(b"123456:test_" + b"A" * 30).decode()
        self.assertEqual(headers["Authorization"], expected)
        self.assertEqual(headers["Idempotence-Key"], "order-7-1")
        self.assertEqual(body["amount"], {"value": "1990.00", "currency": "RUB"})
        self.assertTrue(body["capture"])
        self.assertEqual(body["confirmation"], {"type": "redirect", "return_url": "https://t.me/bot"})
        self.assertEqual(body["metadata"], {"order_id": "7"})
        self.assertEqual(body["receipt"]["customer"], {"phone": "79175494941"})
        self.assertEqual(body["receipt"]["items"][0]["amount"]["value"], "1990.00")
        self.assertEqual(body["receipt"]["items"][0]["vat_code"], 1)
        self.assertEqual((info.id[:8], info.status, info.paid, info.amount, info.confirmation_url),
                         ("2f0a1c3b", "pending", False, 1990, "https://pay.example/x"))

    async def test_receipt_can_be_disabled(self):
        client = YooKassaClient("123456", "test_" + "A" * 30, base_url=str(self.server.make_url("/v3")), receipt=False)
        try:
            await client.create_payment(amount=500, description="x", return_url="https://t.me/b", metadata={},
                                        idempotence_key="k", items=[ReceiptItem("x", 500)], customer_phone="+79175494941")
        finally:
            await client.close()
        self.assertNotIn("receipt", self.seen[0][1])

    async def test_receipt_must_match_payment_amount(self):
        with self.assertRaises(ValueError):
            await self.client.create_payment(amount=1000, description="x", return_url="u", metadata={},
                                             idempotence_key="k", items=[ReceiptItem("x", 999)], customer_phone="+79175494941")
        with self.assertRaises(ValueError):
            await self.client.create_payment(amount=0, description="x", return_url="u", metadata={}, idempotence_key="k")
        self.assertEqual(self.seen, [], "неверный запрос в банк не уходит")

    async def test_api_errors_become_yookassa_error(self):
        self.reply = web.json_response({"type": "error", "code": "invalid_credentials", "description": "Неверные данные"},
                                       status=401)
        with self.assertRaises(YooKassaError) as ctx:
            await self.client.create_payment(amount=100, description="x", return_url="u", metadata={}, idempotence_key="k")
        self.assertIn("401", str(ctx.exception))
        self.assertNotIn("test_AAAA", str(ctx.exception), "секрет не попадает в текст ошибки")
        self.reply = web.json_response({"id": "x"})
        with self.assertRaises(YooKassaError):
            await self.client.create_payment(amount=100, description="x", return_url="u", metadata={}, idempotence_key="k2")

    async def test_no_connection(self):
        client = YooKassaClient("123456", "test_" + "A" * 30, base_url="http://127.0.0.1:9/v3", timeout=2)
        try:
            with self.assertRaises(YooKassaError):
                await client.get_payment("2f0a1c3b-000f-5000-8000-1d0d5b9e0001")
        finally:
            await client.close()

    async def test_get_payment_and_id_validation(self):
        info = await self.client.get_payment("2f0a1c3b-000f-5000-8000-1d0d5b9e0001")
        self.assertTrue(info.paid and info.status == "succeeded")
        self.assertEqual(info.metadata, {"order_id": "7"})
        for bad in ("../../x", "a b", "", "x" * 100):
            with self.subTest(bad), self.assertRaises(YooKassaError):
                await self.client.get_payment(bad)


class ReceiptTests(unittest.TestCase):
    def test_receipt_sums_to_amount_to_pay(self):
        db, shop = make_db(), real_shop()
        shop = dataclasses.replace(shop, promos=(Promo("P", percent=7),))
        cert = certs.issue(db, shop, 777)
        fill(db, ("classic", 1), ("mini", 1), ("roses15", 1))
        d = draft(method="delivery", zone=shop.zones[0].name, address="Москва, Арбат 10", slot="17:00–19:00",
                  promo="P", cert_code=cert.code, pay_method="online")
        order = orders.get_order(db, place(db, shop, d, online=True))
        items = order_receipt_items(order)
        self.assertEqual(sum(i.amount for i in items), order.to_pay)
        self.assertTrue(all(i.amount > 0 for i in items))
        self.assertEqual({i.subject for i in items} - {"commodity", "service"}, set())

    def test_delivery_line_is_service(self):
        db, shop = make_db(), real_shop()
        fill(db, ("mini", 1))
        order = orders.get_order(db, place(db, shop, draft(method="delivery", zone=shop.zones[0].name,
                                                           address="Москва, Арбат 10", slot="17:00–19:00"), online=True))
        items = order_receipt_items(order)
        self.assertEqual([(i.description, i.amount, i.subject) for i in items],
                         [("Мини", 990, "commodity"), ("Доставка", 300, "service")])

    def test_certificate_receipt(self):
        (item,) = certificate_receipt_items(3000)
        self.assertEqual((item.amount, item.subject, item.mode), (3000, "payment", "advance"))
        self.assertNotIn(" ", item.description)


if __name__ == "__main__":
    unittest.main()
