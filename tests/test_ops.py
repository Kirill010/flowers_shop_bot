"""Надёжность в работе: копии базы, напоминания сотрудникам, «магазин закрыт», перезапуск, обрыв связи."""
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import io
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage

from flowerbot import app, backup, catalog, crm, orders, reminders, texts
from flowerbot.config import ROOT, ConfigError
from flowerbot.ctx import Ctx
from flowerbot.db import Database
from flowerbot.orders import Draft

from .harness import Harness
from .helpers import ADMIN, CUSTOMER, NOW, make_settings, real_shop


def place(h: Harness, *, uid: int = CUSTOMER, sku: str = "classic", pay: str | None = None, days_ahead: int = 0) -> int:
    """Оформляет заказ напрямую, в обход диалога. По умолчанию — самовывоз «как можно скорее» с оплатой при получении;
    на другой день — предоплата, как требует правило магазина."""
    catalog.cart_add(h.db, uid, sku)
    local = h.ctx.local_now()
    draft = Draft(user_id=uid, name="Мария", phone="+79175494941", method="pickup", zone="", address="",
                  recipient_name="", recipient_phone="", date=local.date() + timedelta(days=days_ahead),
                  slot="Как можно скорее" if days_ahead == 0 else "09:00–11:00", comment="",
                  pay_method=pay or ("cash" if days_ahead == 0 else "transfer"))
    return orders.place_order(h.db, h.ctx.shop, draft, now_utc=h.now, online_enabled=h.ctx.online_enabled)


def utc(day: int, hour: int, minute: int = 0) -> datetime:
    """Ноябрь 2026 по Гринвичу. Москва — на 3 часа вперёд."""
    return datetime(2026, 11, day, hour, minute, tzinfo=timezone.utc)


class WaitingTextTests(unittest.TestCase):
    def test_waiting_is_written_the_way_people_say_it(self):
        self.assertEqual(texts.waiting(0), "1 мин")
        self.assertEqual(texts.waiting(16), "16 мин")
        self.assertEqual(texts.waiting(60), "1 ч")
        self.assertEqual(texts.waiting(75), "1 ч 15 мин")
        self.assertEqual(texts.waiting(600), "10 ч")
        self.assertEqual(texts.waiting(24 * 60), "более суток")

    def test_clock(self):
        shop = real_shop()
        self.assertEqual(texts.clock(shop.open), "8:00")
        self.assertEqual(texts.clock(shop.close), "21:00")


class ReminderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = Harness()
        self.addCleanup(self.h.close)

    async def check(self):
        start = len(self.h.session.calls)
        n = await reminders.remind_once(self.h.ctx, self.h.bot)
        return n, self.h.session.calls[start:]

    def later(self, **delta) -> None:
        self.h.now += timedelta(**delta)

    async def test_nothing_before_the_promised_time(self):
        place(self.h)
        self.assertEqual((await self.check())[0], 0)
        self.later(minutes=14)
        n, calls = await self.check()
        self.assertEqual((n, calls), (0, []))

    async def test_first_reminder_comes_with_the_accept_button_and_is_not_repeated(self):
        place(self.h)
        self.later(minutes=16)
        n, calls = await self.check()
        self.assertEqual(n, 1)
        text = self.h.sent_to(ADMIN, calls)[0]
        for needle in ("⏰", "Заказ №1 ждёт подтверждения уже 16 мин", "Классика", "Мария"):
            self.assertIn(needle, text)
        self.assertTrue(self.h.has_button("✅ Принять", calls))
        n, calls = await self.check()
        self.assertEqual((n, calls), (0, []), "второй раз подряд не напоминаем")

    async def test_second_reminder_after_an_hour_and_then_silence(self):
        place(self.h)
        self.later(minutes=16)
        await self.check()
        self.later(minutes=46)
        n, calls = await self.check()
        self.assertEqual(n, 1)
        self.assertIn("уже 1 ч 2 мин", self.h.sent_to(ADMIN, calls)[0])
        self.later(hours=5)
        self.assertEqual((await self.check())[0], 0, "больше двух напоминаний на одно дело не бывает")

    async def test_accepted_order_is_forgotten(self):
        place(self.h)
        self.later(minutes=16)
        await self.check()
        self.assertEqual(len(self.h.ctx.nudged), 1)
        orders.set_status(self.h.db, 1, "confirmed")
        n, calls = await self.check()
        self.assertEqual((n, calls), (0, []))
        self.assertEqual(self.h.ctx.nudged, {}, "про принятый заказ не помним")

    async def test_night_orders_get_one_reminder_when_the_shop_opens(self):
        self.h.now = utc(9, 19)  # 22:00 по Москве, магазин закрыт
        place(self.h, days_ahead=1)
        self.h.now = utc(9, 23)  # 02:00
        self.assertEqual((await self.check())[0], 0, "ночью не будим")
        self.h.now = utc(10, 4, 59)  # 07:59 — ещё закрыто
        self.assertEqual((await self.check())[0], 0)
        self.h.now = utc(10, 5)  # 08:00 — открылись
        n, calls = await self.check()
        self.assertEqual(n, 1)
        self.assertIn("ждёт подтверждения уже 10 ч", self.h.sent_to(ADMIN, calls)[0])
        self.assertEqual((await self.check())[0], 0, "ровно одно напоминание, а не серия")

    async def test_several_waiting_things_come_as_one_message(self):
        place(self.h)
        place(self.h)
        crm.add_lead(self.h.db, CUSTOMER, "question", "Есть ли пионы?", name="Мария")
        self.later(minutes=16)
        n, calls = await self.check()
        self.assertEqual(n, 3)
        messages = self.h.sent_to(ADMIN, calls)
        self.assertEqual(len(messages), 1, "одно сообщение со списком, а не россыпь")
        for needle in ("Ждут ответа", "заказов: 2", "обращений: 1", "Дольше всех — 16 мин"):
            self.assertIn(needle, messages[0])
        self.assertEqual(self.h.screen_labels(calls), ["📦 Открыть заказы", "📬 Открыть обращения"])
        calls = await self.h.press(ADMIN, "📦 Открыть заказы")
        self.assertIn("Новые заказы</b> — 2", self.h.last_text(calls))

    async def test_urgent_request_is_reminded_after_five_minutes_and_a_plain_question_is_not(self):
        crm.add_lead(self.h.db, CUSTOMER, "urgent", "Нужен букет к 18:00", name="Мария", phone="+79175494941")
        crm.add_lead(self.h.db, CUSTOMER, "question", "Есть ли пионы?", name="Мария")
        self.later(minutes=4)
        self.assertEqual((await self.check())[0], 0)
        self.later(minutes=2)
        n, calls = await self.check()
        self.assertEqual(n, 1, "через 6 минут — только срочное")
        text = self.h.sent_to(ADMIN, calls)[0]
        self.assertIn("Обращение ждёт ответа уже 6 мин", text)
        self.assertIn("Нужен букет к 18:00", text)
        self.assertTrue(self.h.has_button("↩️ Ответить", calls) and self.h.has_button("✅ Готово", calls))

    async def test_handled_request_stops_reminders(self):
        crm.add_lead(self.h.db, CUSTOMER, "urgent", "Срочно", name="Мария")
        crm.set_lead_status(self.h.db, 1, "done")
        self.later(minutes=30)
        self.assertEqual((await self.check())[0], 0)

    async def test_unpaid_online_order_is_not_nagged_until_it_is_paid(self):
        h = Harness(yookassa=True)
        self.addCleanup(h.close)
        self.h = h
        place(h, pay="online")
        self.later(minutes=30)
        self.assertEqual((await self.check())[0], 0, "клиент платит по ссылке — сотруднику делать нечего")
        orders.mark_paid(h.db, 1)
        n, calls = await self.check()
        self.assertEqual(n, 1, "оплачен, но не принят — вот теперь напоминаем")
        self.assertIn("Заказ №1", self.h.sent_to(ADMIN, calls)[0])

    async def test_unreachable_staff_do_not_cause_a_flood(self):
        place(self.h)
        self.later(minutes=16)
        self.h.session.failures[SendMessage] = TelegramBadRequest(
            method=SendMessage(chat_id=1, text="x"), message="Forbidden: bot was blocked by the user")
        with self.assertLogs("flowerbot.notify", level="ERROR"):
            n, _ = await self.check()
        self.assertEqual(n, 1)
        n, calls = await self.check()
        self.assertEqual((n, calls), (0, []), "не пробуем заново каждую минуту")

    async def test_loop_survives_an_error(self):
        runs = {"n": 0}

        async def flaky(ctx, bot):
            runs["n"] += 1
            if runs["n"] == 1:
                raise RuntimeError("случайный сбой")
            return 0

        with mock.patch.object(reminders, "remind_once", flaky), self.assertLogs("flowerbot.reminders", level="ERROR"):
            task = asyncio.create_task(reminders.reminders_loop(self.h.ctx, self.h.bot, interval=0.01))
            await asyncio.sleep(0.2)
            self.assertFalse(task.done(), "после ошибки задача продолжает работать")
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.assertGreaterEqual(runs["n"], 3)


class BackupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = Harness()
        self.addCleanup(self.h.close)
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.folder = self.root / "backups"

    async def test_copy_is_a_real_working_database(self):
        place(self.h)
        path = backup.make_backup(self.h.ctx, self.folder)
        self.assertEqual(path.name, "shop-2026-11-09.db")
        copy = sqlite3.connect(path)
        try:
            self.assertEqual(copy.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 1)
            self.assertEqual(copy.execute("SELECT COUNT(*) FROM products").fetchone()[0], 20)
            self.assertEqual(copy.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            copy.close()
        self.assertEqual([p.name for p in self.folder.iterdir()], [path.name], "лишних и недокачанных файлов нет")

    async def test_one_copy_a_day(self):
        self.assertIsNotNone(backup.make_backup(self.h.ctx, self.folder))
        self.assertIsNone(backup.make_backup(self.h.ctx, self.folder), "на сегодня копия уже есть")
        self.h.now += timedelta(days=1)
        second = backup.make_backup(self.h.ctx, self.folder)
        self.assertEqual(second.name, "shop-2026-11-10.db")
        self.assertEqual(len(list(self.folder.glob("shop-*.db"))), 2)

    async def test_only_the_last_fourteen_copies_are_kept(self):
        for _ in range(20):
            backup.make_backup(self.h.ctx, self.folder)
            self.h.now += timedelta(days=1)
        names = sorted(p.name for p in self.folder.glob("shop-*.db"))
        self.assertEqual(len(names), backup.KEEP)
        self.assertEqual((names[0], names[-1]), ("shop-2026-11-15.db", "shop-2026-11-28.db"))

    async def test_other_files_in_the_folder_are_never_touched(self):
        self.folder.mkdir()
        (self.folder / "заметка.txt").write_text("не трогать", encoding="utf-8")
        (self.folder / "shop-old.db").write_bytes(b"x")
        for day in range(3):
            backup.make_backup(self.h.ctx, self.folder, keep=1)
            self.h.now += timedelta(days=1)
        self.assertTrue((self.folder / "заметка.txt").exists() and (self.folder / "shop-old.db").exists())
        self.assertEqual(len(list(self.folder.glob("shop-????-??-??.db"))), 1)

    async def test_copy_of_a_file_database_includes_fresh_rows_from_the_journal(self):
        db = Database(self.root / "data" / "shop.db", clock=lambda: NOW)
        try:
            db.run("INSERT INTO users (user_id, name, created_at, last_seen) VALUES (5, 'Мария', 'x', 'x')")
            ctx = Ctx(make_settings(self.root), db, real_shop())
            path = backup.make_backup(ctx, self.folder)
            copy = sqlite3.connect(path)
            try:
                self.assertEqual(copy.execute("SELECT name FROM users WHERE user_id = 5").fetchone()[0], "Мария")
            finally:
                copy.close()
        finally:
            db.close()

    async def test_loop_makes_todays_copy_at_start(self):
        ctx = Ctx(make_settings(self.root), self.h.db, real_shop())
        with self.assertLogs("flowerbot.backup", level="INFO") as logs:
            task = asyncio.create_task(backup.backup_loop(ctx, interval=0.01))
            await asyncio.sleep(0.1)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.assertTrue((self.root / "data" / "backups" / "shop-2026-11-09.db").exists())
        self.assertTrue(any("Сделана резервная копия" in line for line in logs.output))

    async def test_a_failed_copy_is_only_logged_and_the_loop_goes_on(self):
        (self.root / "data").mkdir()
        (self.root / "data" / "backups").write_text("я файл, а не папка")
        ctx = Ctx(make_settings(self.root), self.h.db, real_shop())
        with self.assertLogs("flowerbot.backup", level="ERROR"):
            task = asyncio.create_task(backup.backup_loop(ctx, interval=0.01))
            await asyncio.sleep(0.1)
            self.assertFalse(task.done())
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


class ClosedShopTests(unittest.IsolatedAsyncioTestCase):
    """Ночью бот честно говорит, когда ответят, и не обещает «через 15 минут»."""

    async def open_harness(self, now: datetime) -> Harness:
        h = Harness(now=now)
        self.addCleanup(h.close)
        return h

    async def test_shop_hours_boundaries(self):
        for now, expected in ((utc(9, 4, 59), False), (utc(9, 5), True), (utc(9, 17, 59), True), (utc(9, 18), False)):
            h = await self.open_harness(now)  # 07:59 закрыто, 08:00 открыто, 20:59 открыто, 21:00 закрыто
            self.assertEqual(h.ctx.is_open, expected, now)

    async def test_greeting_mentions_it_only_when_closed(self):
        night = await self.open_harness(utc(9, 20, 30))  # 23:30
        text = night.last_text(await night.say(CUSTOMER, "/start"))
        self.assertIn("Сейчас магазин закрыт", text)
        self.assertIn("с 8:00", text)
        day = await self.open_harness(NOW)
        self.assertNotIn("закрыт", day.last_text(await day.say(CUSTOMER, "/start")))

    async def test_manager_screen(self):
        night = await self.open_harness(utc(9, 20, 30))
        text = night.last_text(await night.say(CUSTOMER, texts.BTN_MANAGER))
        self.assertIn("Сейчас магазин закрыт — ответим с 8:00", text)
        self.assertNotIn("в течение 15 минут", text)

    async def test_question_at_night_gets_an_honest_answer(self):
        h = await self.open_harness(utc(9, 20, 30))
        await h.say(CUSTOMER, texts.BTN_MANAGER)
        await h.press(CUSTOMER, "❓ Задать вопрос")
        calls = await h.say(CUSTOMER, "Есть ли пионы?")
        answer = h.last_text(calls)
        self.assertIn("магазин закрыт", answer)
        self.assertIn("с 8:00", answer)
        self.assertNotIn("Позвоните", answer, "ночью звонить бессмысленно")
        self.assertIn("пионы", h.sent_to(ADMIN, calls)[0], "сотрудникам вопрос всё равно приходит")

    async def test_callback_request_at_night(self):
        h = await self.open_harness(utc(9, 20, 30))
        await h.say(CUSTOMER, texts.BTN_MANAGER)
        await h.press(CUSTOMER, "📞 Оставить номер")
        calls = await h.contact(CUSTOMER, "79175494941")
        self.assertIn("Перезвоним с 8:00", h.last_text(calls))

    async def test_order_at_night_promises_the_morning(self):
        h = await self.open_harness(utc(9, 20, 30))
        await h.tap(CUSTOMER, "p:add:classic")
        await h.say(CUSTOMER, texts.BTN_CART)
        await h.press(CUSTOMER, "✅ Оформить заказ")
        await h.contact(CUSTOMER, "79175494941", first_name="Мария")
        calls = await h.press(CUSTOMER, "🛍 Самовывоз")
        self.assertFalse(h.has_button("⚡ Как можно скорее", calls), "ночью «как можно скорее» не предлагаем")
        await h.tap(CUSTOMER, h.find("co:when:", calls)[0].callback_data)
        calls = await h.press(CUSTOMER, "🔗 Оплатить переводом")  # на другой день — только предоплата
        answer = h.sent_to(CUSTOMER, calls)[0]
        self.assertIn("магазин сейчас закрыт — подтвердим заказ с 8:00".lower(), answer.lower())
        self.assertNotIn("в течение 15 минут", answer)

    async def test_daytime_order_still_promises_fifteen_minutes(self):
        h = await self.open_harness(NOW)
        await h.tap(CUSTOMER, "p:add:classic")
        await h.say(CUSTOMER, texts.BTN_CART)
        await h.press(CUSTOMER, "✅ Оформить заказ")
        await h.contact(CUSTOMER, "79175494941", first_name="Мария")
        await h.press(CUSTOMER, "🛍 Самовывоз")
        await h.press(CUSTOMER, "⚡ Как можно скорее")
        calls = await h.press(CUSTOMER, "💵 Оплата при получении")
        self.assertIn("в течение 15 минут", h.sent_to(CUSTOMER, calls)[0])


class StartAnnouncementTests(unittest.TestCase):
    def test_restart_loop_does_not_flood_the_staff(self):
        db = Database(":memory:")
        self.assertTrue(app.should_announce_start(db, utc(9, 9)), "первый запуск — сообщаем")
        self.assertFalse(app.should_announce_start(db, utc(9, 9, 1)), "перезапуск через минуту — молчим")
        self.assertFalse(app.should_announce_start(db, utc(9, 9, 9)))
        self.assertTrue(app.should_announce_start(db, utc(9, 9, 20)), "прошло больше 10 минут с прошлого запуска")


class ExitCodeTests(unittest.TestCase):
    """start.bat по коду выхода решает, что делать: показать ошибку настроек или попробовать снова."""

    def run_main(self, failure) -> int:
        async def fake_run(settings):
            if failure is not None:
                raise failure

        with mock.patch.object(app, "load_settings", return_value=object()), \
                mock.patch.object(app, "setup_logging"), mock.patch.object(app, "run", fake_run), \
                contextlib.redirect_stdout(io.StringIO()):
            return app.main()

    def test_codes(self):
        self.assertEqual(self.run_main(app.ConnectionProblem("нет связи")), app.EXIT_NO_CONNECTION)
        self.assertEqual(self.run_main(ConfigError("неверный ключ")), app.EXIT_SETTINGS)
        self.assertEqual(self.run_main(None), 0)
        self.assertEqual(self.run_main(KeyboardInterrupt()), 0)
        self.assertNotEqual(app.EXIT_NO_CONNECTION, app.EXIT_SETTINGS)

    def test_no_connection_is_a_kind_of_config_error_for_old_callers(self):
        self.assertTrue(issubclass(app.ConnectionProblem, ConfigError))


class PrivacyLinkTests(unittest.IsolatedAsyncioTestCase):
    """Необязательная ссылка на политику конфиденциальности (shop.toml → privacy_url)."""

    async def contact_prompt(self, **changes) -> str:
        h = Harness(shop=dataclasses.replace(real_shop(), **changes))
        self.addCleanup(h.close)
        await h.tap(CUSTOMER, "p:add:mini")
        await h.say(CUSTOMER, texts.BTN_CART)
        return h.last_text(await h.press(CUSTOMER, "✅ Оформить заказ"))

    async def test_link_is_shown_when_set_and_absent_otherwise(self):
        text = await self.contact_prompt(privacy_url="https://example.com/privacy/")
        self.assertIn('<a href="https://example.com/privacy/">обработку персональных данных</a>', text)
        self.assertIn("соглашаетесь", text)
        self.assertNotIn("соглашаетесь", await self.contact_prompt())

    async def test_link_cannot_break_the_markup(self):
        text = await self.contact_prompt(privacy_url='https://example.com/p?a=1&b="2"')
        self.assertIn("a=1&amp;b=&quot;2&quot;", text)

    def test_setting_is_validated(self):
        from flowerbot.config import parse_shop, read_toml
        data = read_toml(ROOT / "shop.toml")
        self.assertEqual(parse_shop(data).privacy_url, "", "в поставляемом shop.toml ссылки нет")
        data["shop"]["privacy_url"] = "https://example.com/privacy/"
        self.assertEqual(parse_shop(data).privacy_url, "https://example.com/privacy/")
        data["shop"]["privacy_url"] = "example.com/privacy"
        with self.assertRaises(ConfigError) as caught:
            parse_shop(data)
        self.assertIn("privacy_url", str(caught.exception))


START_BAT = ROOT / "start.bat"


class StartBatTests(unittest.TestCase):
    def test_file_is_windows_text_with_a_restart_loop(self):
        data = START_BAT.read_bytes()
        self.assertEqual(data.count(b"\n"), data.count(b"\r\n"), "только CRLF: с LF cmd.exe путает метки")
        text = data.decode("utf-8")
        for needle in (":run", "goto run", ":no_connection", ":settings", 'if "%CODE%"=="2" goto settings',
                       'if "%CODE%"=="3" goto no_connection', "gtr 300"):
            self.assertIn(needle, text)

    @unittest.skipUnless(sys.platform == "win32", "start.bat запускается только в Windows")
    def test_restart_logic_in_the_real_cmd(self):
        """Берём кусок настоящего start.bat, вместо бота ставим «пустышку» с заданными кодами выхода и смотрим, что происходит."""
        text = START_BAT.read_bytes().decode("utf-8")
        block = text[text.index('set "WAIT=15"'):text.index(":no_python")]
        block = (block.replace('"%VPY%" -m flowerbot', 'call "%~dp0fake.bat"').replace('set "WAIT=15"', 'set "WAIT=1"')
                 .replace("ping -n 16", "ping -n 2"))

        def run(codes: list[int]) -> str:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                folder = Path(tmp)
                fake = ["@echo off", "if not exist counter.txt >counter.txt echo 0", "set /p N=<counter.txt",
                        "set /a N=N+1", ">counter.txt echo %N%"]
                fake += [f"if %N%=={i} exit /b {code}" for i, code in enumerate(codes, 1)] + ["exit /b 0"]
                (folder / "fake.bat").write_bytes(("\r\n".join(fake) + "\r\n").encode("ascii"))
                script = "@echo off\r\nchcp 65001 >nul\r\n" + block
                (folder / "t.bat").write_bytes(script.encode("utf-8"))
                done = subprocess.run(["cmd", "/c", str(folder / "t.bat")], cwd=folder, stdin=subprocess.DEVNULL,
                                      capture_output=True, timeout=60)
                return done.stdout.decode("utf-8", "replace")

        out = run([1, 3, 0])  # сбой → нет связи → нормальный выход
        self.assertIn("Запускаю заново через 1 сек", out)
        self.assertIn("Нет связи с Telegram", out)
        self.assertNotIn("Исправьте то, что написано", out)
        out = run([1, 1, 0])  # пауза между сбоями растёт
        self.assertIn("через 1 сек", out)
        self.assertIn("через 2 сек", out)
        out = run([2])  # неверные настройки: подсказка и остановка, без перезапуска
        self.assertIn("Исправьте то, что написано", out)
        self.assertNotIn("Запускаю заново", out)
        self.assertNotIn("Бот остановился", run([0]), "нормальный выход — ничего лишнего не печатаем")


if __name__ == "__main__":
    unittest.main()
