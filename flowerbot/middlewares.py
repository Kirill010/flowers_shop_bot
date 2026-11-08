"""Защитные «прослойки»: запоминание клиента, ограничение частоты запросов, сброс диалога при нажатии на меню."""
from __future__ import annotations

import time
from collections import deque
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from . import crm
from .ctx import Ctx
from .keyboards import BTN_CANCEL
from .texts import MENU_BUTTONS


class UserMiddleware(BaseMiddleware):
    """Запоминает, кто пишет боту (имя, ник) — для истории заказов и уведомлений."""

    async def __call__(self, handler: Callable[[TelegramObject, dict], Awaitable[Any]], event: TelegramObject,
                       data: dict[str, Any]) -> Any:
        user, ctx = data.get("event_from_user"), data.get("ctx")
        if user is not None and ctx is not None and not user.is_bot:
            crm.upsert_user(ctx.db, user.id, user.first_name, user.last_name, user.username)
        return await handler(event, data)


class Throttle(BaseMiddleware):
    """Не больше `limit` действий за `window` секунд от одного человека. Защита от спама и подбора кодов."""

    def __init__(self, limit: int = 12, window: float = 10.0, clock: Callable[[], float] = time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self.hits: dict[int, deque[float]] = {}
        self.warned: dict[int, float] = {}

    async def __call__(self, handler: Callable[[TelegramObject, dict], Awaitable[Any]], event: TelegramObject,
                       data: dict[str, Any]) -> Any:
        user, ctx = data.get("event_from_user"), data.get("ctx")
        if user is None or (isinstance(ctx, Ctx) and ctx.is_admin(user.id)):
            return await handler(event, data)
        now = self.clock()
        queue = self.hits.setdefault(user.id, deque())
        while queue and now - queue[0] > self.window:
            queue.popleft()
        if len(queue) >= self.limit:
            if isinstance(event, CallbackQuery):
                await event.answer("Не так быстро 🙂", show_alert=False)
            elif isinstance(event, Message) and now - self.warned.get(user.id, -1e9) > self.window:
                self.warned[user.id] = now
                await event.answer("Слишком много сообщений подряд. Подождите несколько секунд и попробуйте снова 🙂")
            return None
        queue.append(now)
        if len(self.hits) > 5000:  # чистим память от тех, кто давно не писал
            for uid in [u for u, q in self.hits.items() if not q or now - q[-1] > self.window]:
                self.hits.pop(uid, None)
                self.warned.pop(uid, None)
        return await handler(event, data)


class MenuInterrupt(BaseMiddleware):
    """Нажали кнопку меню или команду посреди диалога («введите имя…») — диалог сбрасывается.
    Иначе название кнопки записалось бы как имя или адрес."""

    async def __call__(self, handler: Callable[[Message, dict], Awaitable[Any]], event: Message,
                       data: dict[str, Any]) -> Any:
        text = event.text or ""
        if text in MENU_BUTTONS or text == BTN_CANCEL or text.startswith("/"):
            state = data.get("state")
            if state is not None:
                await state.clear()
                data["raw_state"] = None
        return await handler(event, data)
