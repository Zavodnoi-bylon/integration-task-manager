from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from app.services.notification_service import NotificationService
from app.services.ticket_service import (
    AccessDeniedError,
    TicketNotFoundError,
    TicketService,
    TicketStateError,
)
from app.views.tickets import technician_ticket_list, user_ticket_list
from app.domain import STATUS_COMPLETED, STATUS_OPEN

from .helpers import edit_text_if_changed


router = Router(name="ticket_actions")


@router.callback_query(F.data.startswith("reopen_"))
async def reopen_ticket(
    query: CallbackQuery,
    state: FSMContext,
    ticket_service: TicketService,
    notification_service: NotificationService,
):
    ticket_id = int(query.data.rsplit("_", 1)[1])
    try:
        ticket = ticket_service.reopen_ticket(
            ticket_id,
            query.from_user.id,
            None,
            idempotency_key=f"callback:{query.id}",
        )
    except TicketNotFoundError:
        await query.answer("Задача не найдена", show_alert=True)
        return
    except AccessDeniedError:
        await query.answer("Нельзя изменить чужую задачу", show_alert=True)
        return
    except TicketStateError:
        await query.answer("Задача уже находится в работе", show_alert=True)
        return

    await state.clear()
    await query.answer("Задача возвращена в работу")
    await notification_service.reopened(ticket, query.from_user.id)

    if ticket_service.is_technician(query.from_user.id):
        page = ticket_service.list_technician_tickets(STATUS_OPEN)
        completed_total = ticket_service.list_technician_tickets(
            STATUS_COMPLETED,
            page_size=1,
        ).total
        text, keyboard = technician_ticket_list(
            page,
            completed=False,
            other_total=completed_total,
        )
    else:
        page = ticket_service.list_user_tickets(query.from_user.id, STATUS_OPEN)
        text, keyboard = user_ticket_list(page, completed=False)
    await edit_text_if_changed(query.message, text, reply_markup=keyboard, parse_mode="HTML")
