"""Карточки товаров с фото: отправка файла, запоминание file_id, запасной вариант при ошибке Telegram."""
from __future__ import annotations

import unittest
from pathlib import Path

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage, SendPhoto

from flowerbot import catalog, texts
from flowerbot.callbacks import Prod

from .harness import Harness
from .helpers import CUSTOMER


class PhotoCardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = Harness()
        self.addCleanup(self.h.close)
        folder = Path(self.h.ctx.settings.photos_dir)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "mini.jpg").write_bytes(b"\xff\xd8 fake image")
        catalog.refresh_photo(self.h.db, folder, "mini")

    async def test_card_goes_out_as_photo_and_file_id_is_remembered(self):
        await self.h.say(CUSTOMER, texts.BTN_CATALOG)
        calls = await self.h.press(CUSTOMER, "💐 Букеты")
        photos = [c for c in calls if isinstance(c, SendPhoto)]
        self.assertEqual(len(photos), 1, "фото есть только у одного товара")
        self.assertIn("Мини", photos[0].caption)
        self.assertTrue(photos[0].reply_markup is not None, "кнопки остались")
        self.assertEqual(catalog.get_product(self.h.db, "mini").photo_file_id, "CACHED_FILE_ID")
        # второй раз отправляем по запомненному номеру, а не файлом
        await self.h.say(CUSTOMER, texts.BTN_CATALOG)
        calls = await self.h.press(CUSTOMER, "💐 Букеты")
        photo = next(c for c in calls if isinstance(c, SendPhoto))
        self.assertEqual(photo.photo, "CACHED_FILE_ID")

    async def test_telegram_rejects_old_file_id_then_we_fall_back_to_text(self):
        catalog.set_photo_file_id(self.h.db, "mini", "STALE_ID")
        self.h.session.failures[SendPhoto] = TelegramBadRequest(method=SendPhoto(chat_id=1, photo="x"), message="wrong file identifier")
        await self.h.say(CUSTOMER, texts.BTN_CATALOG)
        calls = await self.h.press(CUSTOMER, "💐 Букеты")
        cards = [c.text for c in calls if isinstance(c, SendMessage) and "Мини" in c.text]
        self.assertEqual(len(cards), 1, "карточка всё равно показана текстом, пусть и без фото")
        self.assertIsNone(catalog.get_product(self.h.db, "mini").photo_file_id, "негодный номер забыт")

    async def test_long_description_is_sent_after_the_photo(self):
        long_text = "очень длинное описание " * 60
        self.h.db.run("UPDATE products SET description = ? WHERE sku = 'mini'", (long_text,))
        await self.h.say(CUSTOMER, texts.BTN_CATALOG)
        await self.h.press(CUSTOMER, "💐 Букеты")
        calls = await self.h.tap(CUSTOMER, Prod(act="info", sku="mini").pack())
        photos = [c for c in calls if isinstance(c, SendPhoto)]
        self.assertEqual(len(photos), 1)
        self.assertIsNone(photos[0].caption, "подпись длиннее 1024 знаков Telegram не примет")
        self.assertIn("очень длинное описание", self.h.last_text(calls))
        self.assertTrue(self.h.has_button("🛒 Добавить в корзину", calls))

    async def test_path_outside_photos_folder_is_never_opened(self):
        self.h.db.run("UPDATE products SET photo = '../shop.toml', photo_file_id = NULL WHERE sku = 'mini'")
        await self.h.say(CUSTOMER, texts.BTN_CATALOG)
        calls = await self.h.press(CUSTOMER, "💐 Букеты")
        self.assertEqual([c for c in calls if isinstance(c, SendPhoto)], [])
        self.assertTrue(any("Мини" in t for t in self.h.texts(calls)))


if __name__ == "__main__":
    unittest.main()
