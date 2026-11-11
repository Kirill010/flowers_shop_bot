"""Всё, что не подошло ни под один раздел. Тупиков нет: непонятное сообщение, фото или файл можно одним нажатием
передать менеджеру."""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import keyboards as kb
from ..ctx import Ctx
from ..ui import menu_for
from ..util import clean_text

router = Router(name="fallback")

MEDIA_NAMES = {"photo": "фото", "document": "файл", "video": "видео", "voice": "голосовое сообщение",
               "audio": "аудио", "video_note": "видеосообщение"}
ALBUM_LIMIT = 10  # столько фото из одного альбома передаём за раз


@router.message(F.text.startswith("/"))
async def unknown_command(message: Message, ctx: Ctx) -> None:
    await message.answer("Такой команды нет. Меню — кнопки внизу или /menu.", reply_markup=menu_for(ctx, message.from_user.id))


@router.message(F.text)
async def unknown_text(message: Message, state: FSMContext, ctx: Ctx) -> None:
    """Клиент написал своими словами («а розы красные есть?»). Предлагаем передать это менеджеру — одно нажатие."""
    text = clean_text(message.text, 1000)
    if len(text) < 2:  # «?» или «.» — молчать нельзя: человек решит, что бот завис
        await message.answer("Выберите раздел в меню внизу 👇 или напишите вопрос словами.",
                             reply_markup=menu_for(ctx, message.from_user.id))
        return
    await state.update_data(fwd=text, fwd_msgs=None, fwd_group=None)
    await message.answer("Не нашёл такого раздела 🤔 Это вопрос для менеджера? Отправить ему ваше сообщение?",
                         reply_markup=kb.forward_question())


@router.message(F.photo | F.document | F.video | F.voice | F.audio | F.video_note)
async def unknown_media(message: Message, state: FSMContext, ctx: Ctx) -> None:
    """Клиент прислал фото букета «хочу такой» или голосовое. Это тоже вопрос менеджеру — спрашиваем одним нажатием.
    Сотруднику, который прислал фото просто так, подсказываем, как добавить фото товара."""
    if ctx.is_admin(message.from_user.id):
        await message.answer("📷 Чтобы добавить фото товара: «👑 Панель» → «⚙️ Ещё» → «📷 Фото товаров».",
                             reply_markup=menu_for(ctx, message.from_user.id))
        return
    data = await state.get_data()
    group = message.media_group_id
    if group and data.get("fwd_group") == group:  # следующее фото того же альбома: молча добавляем, второй раз не спрашиваем
        ids = list(data.get("fwd_msgs") or [])
        if len(ids) < ALBUM_LIMIT:
            ids.append(message.message_id)
        await state.update_data(fwd_msgs=ids)
        return
    kind = next(name for name in MEDIA_NAMES if getattr(message, name, None))
    caption = clean_text(message.caption or "", 500)
    label = f"📎 Клиент прислал {MEDIA_NAMES[kind]}" + (f": {caption}" if caption else "")
    await state.update_data(fwd=label, fwd_msgs=[message.message_id], fwd_group=group)
    await message.answer("Это для менеджера? Отправить ему ваше сообщение?", reply_markup=kb.forward_question())


@router.message()
async def other_content(message: Message, ctx: Ctx) -> None:
    await message.answer("Я понимаю текст, фото и кнопки меню 🙂 Выберите раздел внизу.",
                         reply_markup=menu_for(ctx, message.from_user.id))


@router.callback_query()
async def stale_button(call: CallbackQuery) -> None:
    await call.answer("Эта кнопка устарела. Откройте меню заново 🙂", show_alert=True)
