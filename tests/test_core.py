"""Настройки, служебные функции, каталог, расписание и цены."""
from __future__ import annotations

import dataclasses
import random
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from flowerbot import catalog, pricing, schedule
from flowerbot.config import ConfigError, ROOT, Promo, load_settings, parse_shop, read_toml
from flowerbot.util import clean_text, day_label, format_phone, normalize_phone, parse_user_date, rub, season_text

from .helpers import ADMIN, CUSTOMER, NOW, TODAY, VALID_TOKEN, make_db, make_settings, real_shop


class SettingsTests(unittest.TestCase):
    def test_valid(self):
        s = make_settings(ADMIN_IDS="1000000001, 2000000002;3000000003", ORDERS_CHAT_ID="-1001234567890")
        self.assertEqual(s.admin_ids, (1000000001, 2000000002, 3000000003))
        self.assertEqual(s.orders_chat_id, -1001234567890)
        self.assertFalse(s.yookassa_enabled)

    def test_missing_token_explains_what_to_do(self):
        with self.assertRaises(ConfigError) as ctx:
            load_settings(ROOT, environ={})
        self.assertIn("BOT_TOKEN", str(ctx.exception))
        self.assertIn("BotFather", str(ctx.exception))

    def test_bad_token_rejected(self):
        for bad in ("вставьте-токен", "12345", "abc:def", VALID_TOKEN + " лишнее"):
            with self.subTest(bad), self.assertRaises(ConfigError):
                make_settings(BOT_TOKEN=bad)

    def test_admin_ids_must_be_numbers(self):
        with self.assertRaises(ConfigError):
            make_settings(ADMIN_IDS="@ivan")

    def test_empty_admins_allowed_for_first_run(self):
        self.assertEqual(make_settings(ADMIN_IDS="").admin_ids, ())

    def test_yookassa_needs_both_values(self):
        with self.assertRaises(ConfigError):
            make_settings(YOOKASSA_SHOP_ID="123456")
        s = make_settings(YOOKASSA_SHOP_ID="123456", YOOKASSA_SECRET_KEY="test_" + "A" * 30)
        self.assertTrue(s.yookassa_enabled)
        with self.assertRaises(ConfigError):
            make_settings(YOOKASSA_SHOP_ID="123456", YOOKASSA_SECRET_KEY="просто пароль")

    def test_proxy_format(self):
        make_settings(PROXY_URL="socks5://127.0.0.1:1080")
        with self.assertRaises(ConfigError):
            make_settings(PROXY_URL="127.0.0.1:1080")

    def test_env_example_parses_after_filling_the_token(self):
        """Файл-образец, который владелец получает при первом запуске, должен читаться без ошибок."""
        import os
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            text = (ROOT / ".env.example").read_text(encoding="utf-8").replace("BOT_TOKEN=", f"BOT_TOKEN={VALID_TOKEN}", 1)
            (Path(tmp) / ".env").write_text(text, encoding="utf-8")
            with mock.patch.dict(os.environ, {}, clear=True):  # переменные самого компьютера не должны мешать
                settings = load_settings(Path(tmp))
        self.assertEqual(settings.bot_token, VALID_TOKEN)
        self.assertEqual((settings.admin_ids, settings.orders_chat_id, settings.proxy_url, settings.api_url), ((), None, "", ""))
        self.assertFalse(settings.yookassa_enabled)
        self.assertTrue(settings.yookassa_receipt)

    def test_start_bat_keeps_windows_line_endings(self):
        """С «Unix-переводами строк» cmd.exe путает метки (goto :eof) и ломает запуск — проверено на себе."""
        data = (ROOT / "start.bat").read_bytes()
        self.assertIn(b"\r\n", data)
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""), "в start.bat остались одиночные LF")
        self.assertNotIn(b"\xef\xbb\xbf", data[:3], "BOM в начале файла ломает первую строку")

    def test_no_secrets_in_repo_files(self):
        """В .env.example и коде не должно быть настоящих ключей (защита от повторения истории с утечкой)."""
        import re
        pattern = re.compile(r"\d{8,10}:AA[A-Za-z0-9_-]{30,}|live_[A-Za-z0-9_-]{30,}")
        for path in list(ROOT.glob("*.example")) + list(ROOT.glob("*.toml")) + list((ROOT / "flowerbot").rglob("*.py")):
            self.assertIsNone(pattern.search(path.read_text(encoding="utf-8")), str(path))


class ShopFileTests(unittest.TestCase):
    def test_real_shop_file(self):
        shop = real_shop()
        self.assertEqual(shop.name, "MuzFlowers")
        self.assertEqual(shop.hours, "Ежедневно, 8:00–21:00")
        self.assertEqual(len(shop.slots), 6)
        self.assertEqual(shop.zones[0].price, 300)
        self.assertEqual(shop.free_from, 3000)
        self.assertTrue(shop.prepay_future_orders)
        self.assertEqual(shop.promos, ())
        self.assertTrue(shop.google_maps_url.startswith("https://www.google.com/maps/search/"))

    def test_broken_toml_gives_human_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shop.toml"
            path.write_text('[shop\nname = "x"', encoding="utf-8")
            with self.assertRaises(ConfigError) as ctx:
                read_toml(path)
            self.assertIn("shop.toml", str(ctx.exception))

    def test_bom_file_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.toml"
            path.write_bytes(b"\xef\xbb\xbf" + 'a = 1'.encode())
            self.assertEqual(read_toml(path), {"a": 1})

    def test_bad_values_explained(self):
        for data in ({"orders": {"slots": ["25:00-26:00"]}}, {"orders": {"slots": ["12:00-10:00"]}},
                     {"delivery": {"zones": [{"name": "X"}]}}, {"shop": {"open": "22:00", "close": "08:00"}},
                     {"shop": {"timezone": "Mars/Base"}}, {"orders": {"min_order": "много"}},
                     {"promo": [{"code": "A", "percent": 10, "amount": 100}]}, {"promo": [{"code": "A"}]}):
            with self.subTest(data), self.assertRaises(ConfigError):
                parse_shop(data)

    def test_promo_parsed(self):
        shop = parse_shop({"promo": [{"code": "Весна", "percent": 10, "until": date(2026, 12, 31)}]})
        self.assertEqual(shop.promo("весна").percent, 10)
        self.assertIsNone(shop.promo("нет"))


class UtilTests(unittest.TestCase):
    def test_phones(self):
        good = {"+7 917 549-49-41": "+79175494941", "8 (917) 549-49-41": "+79175494941", "9175494941": "+79175494941",
                "7 917 549 49 41": "+79175494941", "+380 44 123 45 67": "+380441234567"}
        for raw, expected in good.items():
            self.assertEqual(normalize_phone(raw), expected, raw)
        for bad in ("", "abc", "12345", "+7 917 549", "<script>", "8 917 549-49-4", "+7 (917) 549-49-41 доб 5"):
            self.assertIsNone(normalize_phone(bad), bad)
        self.assertEqual(format_phone("+79175494941"), "+7 917 549-49-41")

    def test_clean_text_removes_invisible_tricks(self):
        self.assertEqual(clean_text("  Привет‮  мир \n\n\n\n ok  "), "Привет мир\n\nok")
        self.assertEqual(len(clean_text("я" * 1000, 50)), 50)

    def test_money_and_dates(self):
        self.assertEqual(rub(11990), "11 990 ₽")
        self.assertEqual(day_label(date(2026, 11, 9), TODAY), "Сегодня, 09.11")
        self.assertEqual(day_label(date(2026, 11, 10), TODAY), "Завтра, 10.11")
        self.assertEqual(day_label(date(2026, 11, 13), TODAY), "Пт, 13.11")
        self.assertEqual(parse_user_date("14.11", TODAY), date(2026, 11, 14))
        self.assertEqual(parse_user_date("05.01", date(2026, 12, 28)), date(2027, 1, 5))
        self.assertEqual(parse_user_date("14/11/27", TODAY), date(2027, 11, 14))
        for bad in ("31.02", "завтра", "", "14", "99.99"):
            self.assertIsNone(parse_user_date(bad, TODAY), bad)
        self.assertEqual(season_text(2, 5), "с февраля по май")


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.db = make_db()

    def test_real_catalog_matches_the_brief(self):
        products = catalog.list_products(self.db)
        self.assertEqual(len(products), 20)
        self.assertEqual(len(catalog.list_products(self.db, "bouquet")), 12)
        self.assertEqual(len(catalog.list_products(self.db, "plant")), 8)
        prices = {p.sku: p.price for p in products}
        self.assertEqual((prices["mini"], prices["classic"], prices["roses51"], prices["musical"]), (990, 1990, 11990, 2290))
        self.assertTrue(all(p.on_request for p in catalog.list_products(self.db, "plant")))
        self.assertTrue(all(p.care for p in catalog.list_products(self.db, "plant")), "у растений есть уход")
        self.assertTrue(all(p.composition for p in catalog.list_products(self.db, "bouquet")), "у букетов есть состав")

    def test_availability_rules(self):
        classic = catalog.get_product(self.db, "classic")
        self.assertTrue(classic.orderable(11))
        tulips = catalog.get_product(self.db, "tulips")
        self.assertFalse(tulips.orderable(11), "тюльпаны вне сезона")
        self.assertIn("с февраля по май", tulips.unavailable_reason(11))
        self.assertTrue(tulips.orderable(3))
        self.assertIn("менеджер", catalog.get_product(self.db, "orchid").unavailable_reason(3))
        catalog.set_in_stock(self.db, "classic", False)
        self.assertEqual(catalog.get_product(self.db, "classic").unavailable_reason(11), "сейчас нет в наличии")

    def test_season_across_new_year(self):
        p = dataclasses.replace(catalog.get_product(self.db, "classic"), season_from=11, season_to=2)
        self.assertTrue(p.in_season(12) and p.in_season(1) and p.in_season(11))
        self.assertFalse(p.in_season(6))

    def test_resync_keeps_stop_list_and_hides_removed(self):
        catalog.set_in_stock(self.db, "mini", False)
        items = catalog.load_catalog_file(ROOT / "catalog.toml")
        catalog.sync_catalog(self.db, items[:5], ROOT / "photos")
        self.assertFalse(catalog.get_product(self.db, "mini").in_stock, "«нет сегодня» не сбрасывается")
        self.assertEqual(len(catalog.list_products(self.db)), 5)
        self.assertFalse(catalog.get_product(self.db, "orchid").active)
        catalog.sync_catalog(self.db, items, ROOT / "photos")
        self.assertEqual(len(catalog.list_products(self.db)), 20)

    def test_catalog_validation(self):
        base = {"sku": "a1", "category": "bouquet", "name": "A", "price": 100}
        catalog.parse_catalog({"product": [base]})
        bad_cases = [
            {**base, "sku": "Плохой sku"}, {**base, "category": "tree"}, {**base, "price": "100"}, {**base, "price": -1},
            {**base, "season": [13, 2]}, {**base, "name": ""}, {**base, "description": "я" * 800},
        ]
        for case in bad_cases:
            with self.subTest(case), self.assertRaises(ConfigError):
                catalog.parse_catalog({"product": [case]})
        with self.assertRaises(ConfigError):
            catalog.parse_catalog({"product": [base, base]})
        with self.assertRaises(ConfigError):
            catalog.parse_catalog({})

    def test_photo_is_picked_up_by_file_name_and_cache_resets(self):
        with tempfile.TemporaryDirectory() as tmp:
            photos = Path(tmp)
            items = catalog.load_catalog_file(ROOT / "catalog.toml")
            catalog.sync_catalog(self.db, items, photos)
            self.assertIsNone(catalog.get_product(self.db, "mini").photo)
            (photos / "mini.jpg").write_bytes(b"x" * 10)
            catalog.sync_catalog(self.db, items, photos)
            self.assertEqual(catalog.get_product(self.db, "mini").photo, "mini.jpg")
            catalog.set_photo_file_id(self.db, "mini", "FILE_ID")
            catalog.sync_catalog(self.db, items, photos)
            self.assertEqual(catalog.get_product(self.db, "mini").photo_file_id, "FILE_ID", "файл не менялся")
            (photos / "mini.jpg").write_bytes(b"y" * 20)
            catalog.sync_catalog(self.db, items, photos)
            self.assertIsNone(catalog.get_product(self.db, "mini").photo_file_id, "файл заменили — старый file_id не годится")

    def test_cart(self):
        db = self.db
        self.assertIsNone(catalog.cart_add(db, CUSTOMER, "classic"))
        self.assertIsNone(catalog.cart_add(db, CUSTOMER, "classic"))
        catalog.cart_add(db, CUSTOMER, "mini")
        lines = catalog.cart_lines(db, CUSTOMER)
        self.assertEqual([(l.product.sku, l.qty) for l in lines], [("classic", 2), ("mini", 1)])
        self.assertEqual(sum(l.total for l in lines), 2 * 1990 + 990)
        catalog.cart_change(db, CUSTOMER, "classic", -1)
        catalog.cart_change(db, CUSTOMER, "mini", -1)
        self.assertEqual([(l.product.sku, l.qty) for l in catalog.cart_lines(db, CUSTOMER)], [("classic", 1)])
        self.assertEqual(catalog.cart_count(db, CUSTOMER), 1)
        catalog.cart_clear(db, CUSTOMER)
        self.assertEqual(catalog.cart_lines(db, CUSTOMER), [])

    def test_cart_limits_and_unknown_items(self):
        db = self.db
        self.assertIn("нет в каталоге", catalog.cart_add(db, CUSTOMER, "nonexistent"))
        for _ in range(catalog.MAX_QTY):
            self.assertIsNone(catalog.cart_add(db, CUSTOMER, "mini"))
        self.assertIn("не положить", catalog.cart_add(db, CUSTOMER, "mini"))
        self.assertEqual(catalog.cart_count(db, CUSTOMER), catalog.MAX_QTY)
        catalog.cart_change(db, CUSTOMER, "mini", 100)
        self.assertEqual(catalog.cart_count(db, CUSTOMER), catalog.MAX_QTY, "кнопка «+» не обходит лимит")
        # лимит разных позиций
        db2 = make_db()
        items = catalog.load_catalog_file(ROOT / "catalog.toml")
        extra = [{**items[0], "sku": f"x{i}", "sort": 100 + i} for i in range(catalog.MAX_LINES)]
        catalog.sync_catalog(db2, items + extra, ROOT / "photos")
        for i in range(catalog.MAX_LINES):
            self.assertIsNone(catalog.cart_add(db2, CUSTOMER, f"x{i}"))
        self.assertIn("максимум", catalog.cart_add(db2, CUSTOMER, "mini"))


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.shop = real_shop()

    def local(self, hour, minute=0, day=9):
        return datetime(2026, 11, day, hour, minute, tzinfo=self.shop.tz)

    def test_future_days_have_all_windows(self):
        self.assertEqual(len(schedule.slots_for(self.shop, "delivery", date(2026, 11, 10), self.local(12))), 6)

    def test_delivery_today_needs_two_hours_lead(self):
        slots = schedule.slots_for(self.shop, "delivery", date(2026, 11, 9), self.local(12))
        self.assertEqual(slots, ["15:00–17:00", "17:00–19:00", "19:00–21:00"])
        self.assertEqual(schedule.slots_for(self.shop, "delivery", date(2026, 11, 9), self.local(18, 30)), [])

    def test_pickup_today_has_asap_while_open(self):
        slots = schedule.slots_for(self.shop, "pickup", date(2026, 11, 9), self.local(12))
        self.assertEqual(slots[0], schedule.ASAP)
        self.assertIn("11:00–13:00", slots)
        self.assertNotIn("09:00–11:00", slots)
        late = schedule.slots_for(self.shop, "pickup", date(2026, 11, 9), self.local(20, 50))
        self.assertNotIn(schedule.ASAP, late, "через 20 минут магазин уже закрыт")
        early = schedule.slots_for(self.shop, "pickup", date(2026, 11, 9), self.local(7, 0))
        self.assertNotIn(schedule.ASAP, early, "магазин ещё закрыт — «как можно скорее» не предлагаем")
        self.assertEqual(early[0], "09:00–11:00", "но заказать на утро уже можно")

    def test_past_and_too_far(self):
        self.assertEqual(schedule.slots_for(self.shop, "pickup", date(2026, 11, 8), self.local(12)), [])
        self.assertEqual(schedule.slots_for(self.shop, "pickup", date(2027, 6, 1), self.local(12)), [])
        self.assertTrue(schedule.date_problem(self.shop, date(2026, 11, 8), self.local(12)))
        self.assertTrue(schedule.date_problem(self.shop, date(2027, 6, 1), self.local(12)))
        self.assertEqual(schedule.date_problem(self.shop, date(2026, 12, 20), self.local(12)), "")

    def test_date_buttons_skip_today_when_too_late(self):
        days = schedule.date_choices(self.shop, "delivery", self.local(19, 0))
        self.assertEqual(days[0], date(2026, 11, 10))
        self.assertEqual(len(days), 7)
        self.assertEqual(schedule.date_choices(self.shop, "delivery", self.local(10))[0], date(2026, 11, 9))

    def test_local_time_conversion(self):
        self.assertEqual(schedule.local_now(self.shop, NOW).hour, 12)


class PricingTests(unittest.TestCase):
    def test_allocate_always_sums_exactly(self):
        rng = random.Random(7)
        for _ in range(500):
            weights = [rng.randint(0, 20000) for _ in range(rng.randint(1, 6))]
            total = rng.randint(0, max(1, sum(weights)))
            shares = pricing.allocate(total, weights)
            self.assertEqual(len(shares), len(weights))
            if sum(weights) > 0:
                self.assertEqual(sum(shares), total, (total, weights))
                self.assertTrue(all(0 <= s <= w for s, w in zip(shares, weights)), (total, weights, shares))
        self.assertEqual(pricing.allocate(100, [0, 0]), [0, 0])
        self.assertEqual(pricing.allocate(10, [1, 1, 1]), [4, 3, 3])

    def test_delivery_free_threshold_applies_after_discount(self):
        shop = real_shop()
        zone = shop.zones[0]
        self.assertEqual(pricing.delivery_fee(shop, zone, 2999), 300)
        self.assertEqual(pricing.delivery_fee(shop, zone, 3000), 0)
        self.assertEqual(pricing.delivery_fee(dataclasses.replace(shop, free_from=0), zone, 99999), 300)

    def test_promo(self):
        pct = Promo("A", percent=10, min_order=2000)
        self.assertEqual(pricing.promo_discount(pct, 3690), 369)
        self.assertEqual(pricing.promo_discount(Promo("B", amount=500), 300), 300, "скидка не больше суммы букетов")
        self.assertIn("от", pricing.promo_problem(pct, 1990, True, TODAY))
        self.assertEqual(pricing.promo_problem(pct, 2000, True, TODAY), "")
        first = Promo("C", percent=5, first_order_only=True)
        self.assertTrue(pricing.promo_problem(first, 5000, False, TODAY))
        self.assertEqual(pricing.promo_problem(first, 5000, True, TODAY), "")
        old = Promo("D", percent=5, until=date(2026, 11, 8))
        self.assertIn("закончилась", pricing.promo_problem(old, 5000, True, TODAY))
        self.assertEqual(pricing.promo_problem(dataclasses.replace(old, until=TODAY), 5000, True, TODAY), "")


if __name__ == "__main__":
    unittest.main()
