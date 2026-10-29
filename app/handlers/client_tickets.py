from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.domain import STATUS_COMPLETED, STATUS_OPEN
from app.services.notification_service import NotificationService
from app.services.ticket_service import AccessDeniedError, TicketNotFoundError, TicketService
from app.states import UserStates
from app.views.menus import new_ticket_prompt, ticket_created
from app.views.tickets import user_ticket_list

from .helpers import build_ticket_card, edit_text_if_changed, telegram_display_name


router = Router(name="client_tickets")


def _open_list(service: TicketService, user_id: int, page: int = 1):
    return user_ticket_list(
        service.list_user_tickets(user_id, STATUS_OPEN, page=page),
        completed=False,
    )


def _history(service: TicketService, user_id: int, page: int = 1):
    return user_ticket_list(
        service.list_user_tickets(user_id, STATUS_COMPLETED, page=page),
        completed=True,
    )


@router.callback_query(F.data == "new_ticket")
async def new_ticket(query: CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.waiting_for_ticket_message)
    await query.answer()
    text, keyboard = new_ticket_prompt()
    await edit_text_if_changed(query.message, text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == "my_ticket")
async def my_tickets(
    query: CallbackQuery,
    state: FSMContext,
    ticket_service: TicketService,
):
    await state.clear()
    await query.answer()
    text, keyboard = _open_list(ticket_service, query.from_user.id)
    await edit_text_if_changed(query.message, text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == "my_ticket_history")
async def ticket_history(
    query: CallbackQuery,
    state: FSMContext,
    ticket_service: TicketService,
):
    await state.clear()
    await query.answer()
    text, keyboard = _history(ticket_service, query.from_user.id)
    await edit_text_if_changed(query.message, text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data.startswith("user_ticket_"))
async def user_ticket_card(
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

    await state.clear()
    admin_view = ticket_service.is_technician(query.from_user.id)
    text, keyboard = build_ticket_card(
        ticket_service,
        ticket,
        query.from_user.id,
        admin_view=admin_view,
    )
    await query.answer()
    await edit_text_if_changed(query.message, text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data.startswith("open_page_"))
async def open_page(query: CallbackQuery, ticket_service: TicketService):
    page = int(query.data.rsplit("_", 1)[1])
    text, keyboard = _open_list(ticket_service, query.from_user.id, page)
    await query.answer()
    await edit_text_if_changed(query.message, text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data.startswith("history_page_"))
@router.callback_query(F.data.startswith("my_ticket_page_"))
async def history_page(query: CallbackQuery, ticket_service: TicketService):
    page = int(query.data.rsplit("_", 1)[1])
    text, keyboard = _history(ticket_service, query.from_user.id, page)
    await query.answer()
    await edit_text_if_changed(query.message, text, reply_markup=keyboard, parse_mode="HTML")


@router.message(UserStates.waiting_for_ticket_message)
async def create_ticket(
    message: Message,
    state: FSMContext,
    ticket_service: TicketService,
    notification_service: NotificationService,
):
    body = (message.text or "").strip()
    if not body:
        await message.reply("Описание задачи не может быть пустым.")
        return

    display_name = telegram_display_name(message.from_user)
    ticket_service.register_user(
        message.from_user.id,
        display_name,
        message.date,
    )
    ticket = ticket_service.create_ticket(
        message.from_user.id,
        body,
        message.date,
        request_key=f"telegram-message:{message.chat.id}:{message.message_id}",
    )
    text, keyboard = ticket_created(ticket.ticket_id)
    await message.reply(text, reply_markup=keyboard, parse_mode="HTML")
    await notification_service.new_ticket(ticket, display_name)
    await state.clear()
