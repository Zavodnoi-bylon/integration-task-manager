from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.domain import STATUS_COMPLETED, STATUS_OPEN
from app.services.notification_service import NotificationService
from app.services.ticket_service import (
    AccessDeniedError,
    TicketNotFoundError,
    TicketService,
    TicketStateError,
)
from app.states import UserStates
from app.views.tickets import (
    comment_prompt,
    comment_saved,
    technician_ticket_list,
)

from .helpers import build_ticket_card, edit_text_if_changed


router = Router(name="technician_tickets")


async def _require_technician(query: CallbackQuery, service: TicketService) -> bool:
    if service.is_technician(query.from_user.id):
        return True
    await query.answer("Недостаточно прав", show_alert=True)
    return False


def _open_list(service: TicketService, page: int = 1):
    tickets = service.list_technician_tickets(STATUS_OPEN, page=page)
    completed_total = service.list_technician_tickets(
        STATUS_COMPLETED,
        page=1,
        page_size=1,
    ).total
    return technician_ticket_list(tickets, completed=False, other_total=completed_total)


def _completed_list(service: TicketService, page: int = 1):
    tickets = service.list_technician_tickets(STATUS_COMPLETED, page=page)
    return technician_ticket_list(tickets, completed=True, other_total=0)


@router.callback_query(F.data == "admin_panel")
async def admin_panel(
    query: CallbackQuery,
    state: FSMContext,
    ticket_service: TicketService,
):
    if not await _require_technician(query, ticket_service):
        return
    await state.clear()
    await query.answer()
    text, keyboard = _open_list(ticket_service)
    await edit_text_if_changed(query.message, text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == "completed_tickets")
async def completed_tickets(
    query: CallbackQuery,
    state: FSMContext,
    ticket_service: TicketService,
):
    if not await _require_technician(query, ticket_service):
        return
    await state.clear()
    await query.answer()
    text, keyboard = _completed_list(ticket_service)
    await edit_text_if_changed(query.message, text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data.startswith("ticket_"))
async def open_ticket(
    query: CallbackQuery,
    state: FSMContext,
    ticket_service: TicketService,
):
    if not await _require_technician(query, ticket_service):
        return
    ticket_id = int(query.data.rsplit("_", 1)[1])
    try:
        ticket = ticket_service.get_ticket(ticket_id)
    except TicketNotFoundError:
        await query.answer("Задача не найдена", show_alert=True)
        return
    await state.clear()
    text, keyboard = build_ticket_card(
        ticket_service,
        ticket,
        query.from_user.id,
        admin_view=True,
    )
    await query.answer()
    await edit_text_if_changed(query.message, text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data.startswith("completed_ticket_"))
async def open_completed_ticket(
    query: CallbackQuery,
    state: FSMContext,
    ticket_service: TicketService,
):
    if not await _require_technician(query, ticket_service):
        return
    ticket_id = int(query.data.rsplit("_", 1)[1])
    try:
        ticket = ticket_service.get_ticket(ticket_id)
    except TicketNotFoundError:
        await query.answer("Задача не найдена", show_alert=True)
        return
    if ticket.status != STATUS_COMPLETED:
        await query.answer("Завершённая задача не найдена", show_alert=True)
        return
    await state.clear()
    text, keyboard = build_ticket_card(
        ticket_service,
        ticket,
        query.from_user.id,
        admin_view=True,
    )
    await query.answer()
    await edit_text_if_changed(query.message, text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data.startswith("admin_page_"))
async def admin_page(query: CallbackQuery, ticket_service: TicketService):
    if not await _require_technician(query, ticket_service):
        return
    page = int(query.data.rsplit("_", 1)[1])
    text, keyboard = _open_list(ticket_service, page)
    await query.answer()
    await edit_text_if_changed(query.message, text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data.startswith("completed_page_"))
async def completed_page(query: CallbackQuery, ticket_service: TicketService):
    if not await _require_technician(query, ticket_service):
        return
    page = int(query.data.rsplit("_", 1)[1])
    text, keyboard = _completed_list(ticket_service, page)
    await query.answer()
    await edit_text_if_changed(query.message, text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data.startswith("add_comment_"))
async def add_comment(
    query: CallbackQuery,
    state: FSMContext,
    ticket_service: TicketService,
):
    if not await _require_technician(query, ticket_service):
        return
    ticket_id = int(query.data.rsplit("_", 1)[1])
    try:
        ticket = ticket_service.get_ticket(ticket_id)
    except TicketNotFoundError:
        await query.answer("Задача не найдена", show_alert=True)
        return
    if ticket.status != STATUS_OPEN:
        await query.answer("Задача уже не находится в работе", show_alert=True)
        return
    await state.set_state(UserStates.waiting_for_ticket_comment)
    await state.update_data(ticket_id=ticket_id)
    await query.answer()
    text, keyboard = comment_prompt(ticket_id)
    await edit_text_if_changed(query.message, text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data.startswith("complete_"))
async def complete_ticket(
    query: CallbackQuery,
    state: FSMContext,
    ticket_service: TicketService,
    notification_service: NotificationService,
):
    if not await _require_technician(query, ticket_service):
        return
    ticket_id = int(query.data.rsplit("_", 1)[1])
    try:
        ticket = ticket_service.complete_ticket(
            ticket_id,
            query.from_user.id,
            None,
            idempotency_key=f"callback:{query.id}",
        )
    except (TicketNotFoundError, TicketStateError):
        await query.answer("Задача уже обработана", show_alert=True)
        return
    except AccessDeniedError:
        await query.answer("Недостаточно прав", show_alert=True)
        return

    latest_comment = ticket_service.latest_comment(ticket.ticket_id) or "Без комментария"
    await query.answer()
    await notification_service.completed(
        ticket,
        query.from_user.id,
        latest_comment,
    )
    await state.clear()


@router.message(UserStates.waiting_for_ticket_comment)
async def save_comment(
    message: Message,
    state: FSMContext,
    ticket_service: TicketService,
):
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    comment = (message.text or "").strip()
    if not ticket_id or not comment:
        await message.reply("Комментарий не может быть пустым.")
        return
    try:
        ticket_service.add_comment(
            ticket_id,
            message.from_user.id,
            comment,
            message.date,
            idempotency_key=f"telegram-message:{message.chat.id}:{message.message_id}",
        )
    except AccessDeniedError:
        await state.clear()
        await message.reply("Недостаточно прав.")
        return
    except (TicketNotFoundError, TicketStateError):
        await state.clear()
        await message.reply("Задача уже не находится в работе.")
        return

    text, keyboard = comment_saved(ticket_id, comment)
    await message.reply(text, reply_markup=keyboard, parse_mode="HTML")
    await state.clear()
