from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.domain import STATUS_COMPLETED, STATUS_OPEN
from app.services.ticket_service import TicketService
from app.views.menus import main_menu

from .helpers import edit_text_if_changed, telegram_display_name


router = Router(name="common")


def _menu(service: TicketService, user_id: int):
    open_count = service.list_user_tickets(user_id, STATUS_OPEN, page_size=1).total
    closed_count = service.list_user_tickets(user_id, STATUS_COMPLETED, page_size=1).total
    return main_menu(open_count, closed_count, service.is_technician(user_id))


@router.message(Command("start"))
async def start(message: Message, state: FSMContext, ticket_service: TicketService):
    ticket_service.register_user(
        tg_id=message.from_user.id,
        display_name=telegram_display_name(message.from_user),
        registered_at=message.date,
    )
    await state.clear()
    text, keyboard = _menu(ticket_service, message.from_user.id)
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == "main_menu")
async def show_main_menu(
    query: CallbackQuery,
    state: FSMContext,
    ticket_service: TicketService,
):
    await state.clear()
    await query.answer()
    text, keyboard = _menu(ticket_service, query.from_user.id)
    await edit_text_if_changed(
        query.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
