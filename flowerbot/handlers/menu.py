"""Старт, главное меню, справка, отмена."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from .. import keyboards as kb
from .. import texts
from ..ctx import Ctx

router = Router(name="menu")


@router.message(CommandStart())
@router.message(Command("menu"))
async def start(message: Message, state: FSMContext, ctx: Ctx) -> None:
    await state.clear()
    text = texts.greeting(ctx.shop)
    if not ctx.staff_ready:
        text += f"\n\n⚙️ Бот ещё настраивается: заказы в нём пока не принимаются. Позвоните нам: {ctx.shop.phone}"
    await message.answer(text, reply_markup=kb.main_menu())


@router.message(Command("help"))
async def help_(message: Message, ctx: Ctx) -> None:
    await message.answer(texts.help_text(ctx.shop, ctx.is_admin(message.from_user.id)), reply_markup=kb.main_menu())


@router.message(Command("myid"))
async def my_id(message: Message, ctx: Ctx) -> None:
    uid = message.from_user.id
    text = f"🆔 Ваш номер в Telegram: <code>{uid}</code>"
    if ctx.is_admin(uid):
        text += "\n👑 Вы — администратор этого бота."
    elif not ctx.settings.admin_ids:
        text += ("\n\nЧтобы получать заказы, впишите этот номер в файл <code>.env</code> в строку "
                 f"<code>ADMIN_IDS={uid}</code> и перезапустите бота.")
    await message.answer(text)


@router.message(Command("cancel"))
@router.message(F.text == kb.BTN_CANCEL)
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено. Выберите раздел в меню 👇", reply_markup=kb.main_menu())
