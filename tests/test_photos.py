"""Карточки товаров с фото: показ в одном сообщении, запоминание file_id, переходы фото ↔ текст, запасной вариант."""
from __future__ import annotations

import unittest
from pathlib import Path

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import DeleteMessage, EditMessageMedia, EditMessageReplyMarkup, SendMessage, SendPhoto

from flowerbot import catalog, texts
from flowerbot.callbacks import Prod

from .harness import Harness
from .helpers import CUSTOMER


class PhotoCardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = Harness()
        self.addCleanup(self.h.close)
        self.folder = Path(self.h.ctx.settings.photos_dir)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.give_photo("mini")

    def give_photo(self, sku: str) -> None:
        (self.folder / f"{sku}.jpg").write_bytes(b"\xff\xd8 fake image")
        catalog.refresh_photo(self.h.db, self.folder, sku)

    async def open_mini(self):
        await self.h.say(CUSTOMER, texts.BTN_CATALOG)
        return await self.h.press(CUSTOMER, "Мини ·", prefix=True)

    async def test_card_goes_out_as_photo_and_file_id_is_remembered(self):
        calls = await self.open_mini()
        photos = [c for c in calls if isinstance(c, SendPhoto)]
        self.assertEqual(len(photos), 1)
        self.assertIn("Мини", photos[0].caption)
        self.assertTrue(photos[0].reply_markup is not None, "кнопки остались")
        self.assertEqual(self.h.count(DeleteMessage, calls), 1, "текстовый список заменён карточкой с фото")
        self.assertEqual(catalog.get_product(self.h.db, "mini").photo_file_id, "CACHED_FILE_ID")
        # второй раз отправляем по запомненному номеру, а не файлом
        calls = await self.open_mini()
        photo = next(c for c in calls if isinstance(c, SendPhoto))
        self.assertEqual(photo.photo, "CACHED_FILE_ID")

    async def test_photo_card_to_photo_card_is_edited_in_place(self):
        self.give_photo("classic")
        await self.open_mini()
        calls = await self.h.press(CUSTOMER, "▶", on_photo=True)
        self.assertEqual(self.h.count(EditMessageMedia, calls), 1)
        for kind in (SendPhoto, SendMessage, DeleteMessage):
            self.assertEqual(self.h.count(kind, calls), 0, kind.__name__)
        self.assertIn("Классика", self.h.last_text(calls))

    async def test_photo_card_to_text_card_replaces_the_message(self):
        await self.open_mini()
        calls = await self.h.press(CUSTOMER, "▶", on_photo=True)  # у «Классики» фото нет
        self.assertEqual((self.h.count(DeleteMessage, calls), self.h.count(SendMessage, calls), self.h.count(SendPhoto, calls)),
                         (1, 1, 0))
        self.assertIn("Классика", self.h.last_text(calls))
        self.assertTrue(self.h.has_button("🛒 В корзину", calls))

    async def test_back_to_the_list_from_a_photo_card(self):
        await self.open_mini()
        calls = await self.h.press(CUSTOMER, "⬅️ Назад к списку", on_photo=True)
        self.assertEqual((self.h.count(DeleteMessage, calls), self.h.count(SendMessage, calls)), (1, 1))
        self.assertEqual(len(self.h.find("p:open:", calls)), 8)

    async def test_adding_to_the_cart_keeps_the_photo(self):
        await self.open_mini()
        calls = await self.h.press(CUSTOMER, "🛒 В корзину", on_photo=True)
        self.assertEqual(self.h.count(EditMessageReplyMarkup, calls), 1)
        for kind in (SendPhoto, SendMessage, DeleteMessage):
            self.assertEqual(self.h.count(kind, calls), 0, kind.__name__)
        self.assertTrue(self.h.has_button("🛒 Корзина · 1 ·", calls, prefix=True))

    async def test_telegram_rejects_old_file_id_then_we_fall_back_to_text(self):
        catalog.set_photo_file_id(self.h.db, "mini", "STALE_ID")
        self.h.session.failures[SendPhoto] = TelegramBadRequest(method=SendPhoto(chat_id=1, photo="x"),
                                                                message="wrong file identifier")
        calls = await self.open_mini()
        cards = [c.text for c in calls if isinstance(c, SendMessage) and "Мини" in c.text]
        self.assertEqual(len(cards), 1, "карточка всё равно показана текстом, пусть и без фото")
        self.assertIsNone(catalog.get_product(self.h.db, "mini").photo_file_id, "негодный номер забыт")

    async def test_long_text_is_cut_to_fit_under_the_photo(self):
        long_text = "очень длинное описание " * 60
        self.h.db.run("UPDATE products SET description = ? WHERE sku = 'mini'", (long_text,))
        await self.h.say(CUSTOMER, texts.BTN_CATALOG)
        calls = await self.h.tap(CUSTOMER, Prod(act="more", sku="mini").pack())
        photos = [c for c in calls if isinstance(c, SendPhoto)]
        self.assertEqual(len(photos), 1)
        self.assertLessEqual(len(photos[0].caption), 1024, "подпись длиннее 1024 знаков Telegram не примет")
        self.assertTrue(photos[0].caption.endswith("…"))
        self.assertTrue(self.h.has_button("📖 Свернуть", calls))

    async def test_old_style_info_button_still_works(self):
        await self.h.say(CUSTOMER, texts.BTN_CATALOG)
        calls = await self.h.tap(CUSTOMER, Prod(act="info", sku="mini").pack())
        self.assertIn("Состав", self.h.last_text(calls))

    async def test_path_outside_photos_folder_is_never_opened(self):
        self.h.db.run("UPDATE products SET photo = '../shop.toml', photo_file_id = NULL WHERE sku = 'mini'")
        calls = await self.open_mini()
        self.assertEqual([c for c in calls if isinstance(c, SendPhoto)], [])
        self.assertTrue(any("Мини" in t for t in self.h.texts(calls)))


if __name__ == "__main__":
    unittest.main()
