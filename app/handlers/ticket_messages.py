from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.domain import STATUS_OPEN
from app.services.notification_service import NotificationService
from app.services.ticket_service import (
    AccessDeniedError,
    TicketNotFoundError,
    TicketService,
    TicketStateError,
)
from app.states import UserStates
from app.views.tickets import public_message_prompt, public_message_saved

from .helpers import edit_text_if_changed


router = Router(name="ticket_messages")


@router.callback_query(F.data.startswith("public_message_"))
async def public_message_start(
    query: CallbackQuery,
    state: FSMContext,
    ticket_service: TicketService,
):
    ticket_id = int(query.data.rsplit("_", 1)[1])
    try:
        ticket = ticket_service.get_ticket(ticket_id)
        ticket_service.require_view_access(query.from_user.id, ticket)
    except TicketNotFoundError:
        await query.answer("Задача не найдена", show_alert=True)
        return
    except AccessDeniedError:
        await query.answer("Нельзя открыть чужую задачу", show_alert=True)
        return
    if ticket.status != STATUS_OPEN:
        await query.answer("Сначала верните задачу в работу", show_alert=True)
        return

    admin_view = ticket_service.is_technician(query.from_user.id)
    await state.set_state(UserStates.waiting_for_ticket_public_message)
    await state.update_data(ticket_id=ticket_id, admin_view=admin_view)
    text, keyboard = public_message_prompt(ticket_id, admin_view=admin_view)
    await query.answer()
    await edit_text_if_changed(
        query.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.message(UserStates.waiting_for_ticket_public_message)
async def public_message_save(
    message: Message,
    state: FSMContext,
    ticket_service: TicketService,
    notification_service: NotificationService,
):
    state_data = await state.get_data()
    ticket_id = state_data.get("ticket_id")
    admin_view = bool(state_data.get("admin_view"))
    text = (message.text or "").strip()
    if not ticket_id or not text:
        await message.reply("Сообщение не может быть пустым.")
        return
    try:
        ticket = ticket_service.add_public_message(
            ticket_id,
            message.from_user.id,
            text,
            message.date,
            idempotency_key=f"telegram-message:{message.chat.id}:{message.message_id}",
        )
    except AccessDeniedError:
        await state.clear()
        await message.reply("Нельзя писать в чужую задачу.")
        return
    except (TicketNotFoundError, TicketStateError):
        await state.clear()
        await message.reply("Задача закрыта или недоступна.")
        return

    await notification_service.public_message(ticket, message.from_user.id, text)
    response, keyboard = public_message_saved(ticket_id, admin_view=admin_view)
    await message.reply(response, reply_markup=keyboard, parse_mode="HTML")
    await state.clear()
