"""Онлайн-оплата через ЮKassa (карта и СБП на странице оплаты). Сервер-клиент на aiohttp, без лишних библиотек.

Бот не принимает входящих запросов от банка (никаких вебхуков и открытых портов): он сам раз в полминуты
спрашивает ЮKassa о статусе ожидающих платежей. Так на компьютере владельца ничего не нужно открывать наружу.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import aiohttp

from .orders import Order
from .pricing import allocate
from .util import rub

log = logging.getLogger(__name__)
API_URL = "https://api.yookassa.ru/v3"


class YooKassaError(Exception):
    """Не получилось связаться с ЮKassa или она отказала. Текст — для журнала, клиенту его не показываем."""


@dataclass(frozen=True)
class ReceiptItem:
    description: str
    amount: int                      # ₽ за всю позицию целиком (количество в чеке всегда 1)
    subject: str = "commodity"       # commodity — товар, service — услуга, payment — платёж (аванс)
    mode: str = "full_payment"       # full_payment — полный расчёт, advance — аванс


@dataclass(frozen=True)
class PaymentInfo:
    id: str
    status: str                      # pending | waiting_for_capture | succeeded | canceled
    paid: bool
    amount: Decimal
    confirmation_url: str | None
    metadata: dict


def order_receipt_items(order: Order) -> list[ReceiptItem]:
    """Позиции чека так, чтобы их сумма равнялась ровно сумме платежа (с учётом промокода и сертификата)."""
    labels = [f"{i.name} × {i.qty}" if i.qty > 1 else i.name for i in order.items]
    weights = [i.total for i in order.items]
    subjects = ["commodity"] * len(weights)
    if order.delivery:
        labels.append("Доставка")
        weights.append(order.delivery)
        subjects.append("service")
    shares = allocate(order.discount + order.cert_amount, weights)
    return [ReceiptItem(label[:128], weight - share, subject)
            for label, weight, share, subject in zip(labels, weights, shares, subjects) if weight - share > 0]


def certificate_receipt_items(amount: int) -> list[ReceiptItem]:
    return [ReceiptItem(f"Подарочный сертификат на {rub(amount)}".replace(" ", " "), amount, "payment", "advance")]


class YooKassaClient:
    def __init__(self, shop_id: str, secret_key: str, *, receipt: bool = True, vat_code: int = 1,
                 base_url: str = API_URL, timeout: float = 20.0):
        # Заголовок собираем вручную: так работает во всех версиях aiohttp
        token = base64.b64encode(f"{shop_id}:{secret_key}".encode()).decode()
        self._auth_header = {"Authorization": f"Basic {token}"}
        self.receipt = receipt
        self.vat_code = vat_code
        self.base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(self, method: str, path: str, *, body: dict | None = None,
                       idempotence_key: str | None = None) -> dict:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        headers = dict(self._auth_header)
        if idempotence_key:
            headers["Idempotence-Key"] = idempotence_key
        try:
            async with self._session.request(method, self.base_url + path, json=body, headers=headers) as response:
                try:
                    data = await response.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError):
                    data = {}
                if response.status >= 400:
                    code = data.get("code", "") if isinstance(data, dict) else ""
                    text = data.get("description", "") if isinstance(data, dict) else ""
                    raise YooKassaError(f"ЮKassa ответила {response.status} {code}: {text}".strip())
                if not isinstance(data, dict):
                    raise YooKassaError("ЮKassa прислала непонятный ответ")
                return data
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise YooKassaError(f"Нет связи с ЮKassa: {exc.__class__.__name__}") from exc

    @staticmethod
    def _parse(data: dict) -> PaymentInfo:
        try:
            amount = Decimal(str(data["amount"]["value"]))
            return PaymentInfo(
                id=str(data["id"]), status=str(data["status"]), paid=bool(data.get("paid", False)), amount=amount,
                confirmation_url=(data.get("confirmation") or {}).get("confirmation_url"),
                metadata=dict(data.get("metadata") or {}))
        except (KeyError, TypeError, InvalidOperation) as exc:
            raise YooKassaError("В ответе ЮKassa не хватает данных о платеже") from exc

    async def create_payment(self, *, amount: int, description: str, return_url: str, metadata: dict,
                             idempotence_key: str, customer_phone: str = "",
                             items: list[ReceiptItem] | None = None) -> PaymentInfo:
        """Создаёт платёж и возвращает ссылку для оплаты. Один и тот же idempotence_key не создаст второй платёж."""
        if amount <= 0:
            raise ValueError("Сумма платежа должна быть больше нуля")
        body: dict = {
            "amount": {"value": f"{amount}.00", "currency": "RUB"},
            "capture": True,
            "confirmation": {"type": "redirect", "return_url": return_url},
            "description": description[:128],
            "metadata": {str(k): str(v)[:500] for k, v in metadata.items()},
        }
        if self.receipt and items:
            if sum(i.amount for i in items) != amount:
                raise ValueError("Сумма позиций чека не равна сумме платежа")
            phone = re.sub(r"\D", "", customer_phone)
            if not phone:
                raise ValueError("Для чека нужен телефон покупателя")
            body["receipt"] = {
                "customer": {"phone": phone},
                "items": [{"description": i.description[:128], "quantity": "1.00",
                           "amount": {"value": f"{i.amount}.00", "currency": "RUB"},
                           "vat_code": self.vat_code, "payment_subject": i.subject, "payment_mode": i.mode}
                          for i in items],
            }
        info = self._parse(await self._request("POST", "/payments", body=body, idempotence_key=idempotence_key))
        if not info.confirmation_url:
            raise YooKassaError("ЮKassa не прислала ссылку на оплату")
        return info

    async def get_payment(self, payment_id: str) -> PaymentInfo:
        if not re.fullmatch(r"[A-Za-z0-9\-_]{10,64}", payment_id):
            raise YooKassaError("Неправильный номер платежа")
        return self._parse(await self._request("GET", f"/payments/{payment_id}"))
