from __future__ import annotations

from datetime import datetime

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.types import Message

from app.services.ticket_service import TicketService

from .helpers import telegram_display_name


router = Router(name="drafts")


def forwarded_origin_metadata(origin) -> tuple[str | None, str | None, datetime | None]:
    if origin is None:
        return None, None, None
    origin_type = str(getattr(origin, "type", None) or type(origin).__name__)
    date = getattr(origin, "date", None)
    sender_user = getattr(origin, "sender_user", None)
    chat = getattr(origin, "chat", None)
    if sender_user is not None:
        name = telegram_display_name(sender_user)
    elif getattr(origin, "sender_user_name", None):
        name = str(origin.sender_user_name)
    elif chat is not None:
        name = str(getattr(chat, "title", None) or getattr(chat, "username", None) or "")
    else:
        name = str(getattr(origin, "author_signature", None) or "")
    return origin_type, name or None, date


@router.message(F.forward_origin, StateFilter(None))
async def create_draft_from_forward(
    message: Message,
    ticket_service: TicketService,
) -> None:
    if message.from_user is None:
        return
    source_body = (message.text or message.caption or "").strip()
    if not source_body:
        await message.answer(
            "Пока черновик можно создать только из пересланного текста или подписи к файлу."
        )
        return
    ticket_service.register_user(
        tg_id=message.from_user.id,
        display_name=telegram_display_name(message.from_user),
        registered_at=message.date,
    )
    origin_type, origin_name, origin_date = forwarded_origin_metadata(
        message.forward_origin
    )
    draft = ticket_service.create_forwarded_draft(
        author_id=message.from_user.id,
        source_chat_id=message.chat.id,
        source_message_id=message.message_id,
        source_body=source_body,
        created_at=message.date,
        forward_origin_type=origin_type,
        forward_origin_name=origin_name,
        forward_origin_date=origin_date,
    )
    await message.answer(
        f"Черновик #{draft.draft_id} создан. "
        "Откройте Mini App → Создать, проверьте текст, выберите advertiser "
        "и укажите cap."
    )


@router.message(F.text, StateFilter(None))
async def remember_forward_comment(
    message: Message,
    ticket_service: TicketService,
) -> None:
    if message.from_user is None or not message.text:
        return
    body = message.text.strip()
    if not body or body.startswith("/"):
        return
    ticket_service.register_user(
        tg_id=message.from_user.id,
        display_name=telegram_display_name(message.from_user),
        registered_at=message.date,
    )
    ticket_service.save_draft_intake(
        author_id=message.from_user.id,
        chat_id=message.chat.id,
        message_id=message.message_id,
        body=body,
        received_at=message.date,
    )
