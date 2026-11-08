"""Всё, что не подошло ни под один раздел: вежливый ответ вместо тишины."""
from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from .. import keyboards as kb
from ..ctx import Ctx

router = Router(name="fallback")


@router.message(F.text)
async def unknown_text(message: Message, ctx: Ctx) -> None:
    await message.answer("Не совсем понял 🤔 Выберите раздел в меню внизу. Если нужно спросить что-то своими словами — "
                         "нажмите «💬 Менеджер».", reply_markup=kb.main_menu())


@router.message()
async def other_content(message: Message) -> None:
    await message.answer("Я понимаю только текст и кнопки меню 🙂 Выберите раздел внизу.", reply_markup=kb.main_menu())


@router.callback_query()
async def stale_button(call: CallbackQuery) -> None:
    await call.answer("Эта кнопка устарела. Откройте меню заново 🙂", show_alert=True)
