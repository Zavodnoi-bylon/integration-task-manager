from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message, User

from app.domain import TicketRecord
from app.services.ticket_service import TicketService
from app.views.tickets import ticket_card


async def edit_text_if_changed(
    message: Message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = "HTML",
) -> bool:
    """Edit a bot message and ignore Telegram's harmless no-op response."""
    try:
        await message.edit_text(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
    except TelegramBadRequest as exc:
        if "message is not modified" in exc.message.lower():
            return False
        raise
    return True


def telegram_display_name(user: User) -> str:
    return f"@{user.username}" if user.username else user.full_name


def build_ticket_card(
    service: TicketService,
    ticket: TicketRecord,
    viewer_id: int,
    *,
    admin_view: bool,
):
    owner = service.get_user(ticket.owner_id)
    owner_name = owner.display_name if owner else f"ID {ticket.owner_id}"
    all_events = service.get_events(ticket.ticket_id)
    events = all_events if admin_view else tuple(
        event for event in all_events if event.event_type == "public_message"
    )
    actor_names: dict[int, str] = {}
    for event in events:
        actor = service.get_user(event.actor_id)
        actor_names[event.actor_id] = (
            actor.display_name if actor else f"ID {event.actor_id}"
        )
    return ticket_card(
        ticket,
        owner_name,
        admin_view=admin_view,
        events=events,
        actor_names=actor_names,
        viewer_id=viewer_id,
    )
