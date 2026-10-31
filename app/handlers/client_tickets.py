import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.domain import STATUS_COMPLETED, STATUS_OPEN
from app.services.advertiser_catalog import AdvertiserCatalog
from app.services.destination_service import (
    DestinationFormatterError,
    DestinationService,
)
from app.services.notification_service import NotificationService
from app.services.ticket_service import AccessDeniedError, TicketNotFoundError, TicketService
from app.states import UserStates
from app.views.menus import (
    destination_advertiser_results,
    destination_advertiser_search_prompt,
    destination_advertiser_prompt,
    destination_custom_advertiser_prompt,
    destination_offer_prompt,
    new_ticket_prompt,
    ticket_created,
    ticket_type_prompt,
)
from app.views.tickets import user_ticket_list

from .helpers import build_ticket_card, edit_text_if_changed, telegram_display_name


router = Router(name="client_tickets")
logger = logging.getLogger(__name__)


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
    await state.set_state(UserStates.choosing_ticket_type)
    await query.answer()
    text, keyboard = ticket_type_prompt()
    await edit_text_if_changed(query.message, text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == "ticket_type_general")
async def choose_general_ticket(query: CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.waiting_for_ticket_message)
    await query.answer()
    text, keyboard = new_ticket_prompt()
    await edit_text_if_changed(
        query.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "ticket_type_destination")
async def choose_destination_ticket(
    query: CallbackQuery,
    state: FSMContext,
    advertiser_catalog: AdvertiserCatalog,
):
    await state.set_state(UserStates.waiting_for_destination_advertiser)
    await query.answer()
    text, keyboard = destination_advertiser_prompt(advertiser_catalog.popular())
    await edit_text_if_changed(
        query.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "dest_adv_search")
async def search_destination_advertiser(query: CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.waiting_for_destination_advertiser)
    await query.answer()
    text, keyboard = destination_advertiser_search_prompt()
    await edit_text_if_changed(
        query.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "dest_adv_custom")
async def custom_destination_advertiser(query: CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.waiting_for_destination_custom_advertiser)
    await query.answer()
    text, keyboard = destination_custom_advertiser_prompt()
    await edit_text_if_changed(
        query.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("dest_adv_all:"))
async def all_destination_advertisers(
    query: CallbackQuery,
    advertiser_catalog: AdvertiserCatalog,
):
    page = int(query.data.rsplit(":", 1)[1])
    entries, total_pages = advertiser_catalog.page(page)
    page = max(0, min(page, total_pages - 1))
    await query.answer()
    text, keyboard = destination_advertiser_results(
        "Все варианты",
        entries,
        page=page,
        total_pages=total_pages,
    )
    await edit_text_if_changed(
        query.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("dest_adv_select:"))
async def select_destination_advertiser(
    query: CallbackQuery,
    state: FSMContext,
    advertiser_catalog: AdvertiserCatalog,
):
    entry = advertiser_catalog.get(query.data.rsplit(":", 1)[1])
    if entry is None:
        await query.answer("Advertiser не найден. Обновите список.", show_alert=True)
        return
    await state.update_data(
        destination_advertiser=entry.name,
        destination_url_parameters=entry.url_parameters,
        destination_technician_note=entry.technician_note,
    )
    await state.set_state(UserStates.waiting_for_destination_offer)
    await query.answer()
    text, keyboard = destination_offer_prompt(entry.name)
    await edit_text_if_changed(
        query.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )


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


@router.message(UserStates.waiting_for_destination_advertiser)
async def receive_destination_advertiser(
    message: Message,
    state: FSMContext,
    advertiser_catalog: AdvertiserCatalog,
):
    query = (message.text or "").strip()
    if not query:
        await message.reply("Поисковая строка не может быть пустой.")
        return
    if "\n" in query or "\r" in query:
        await message.reply("Введите часть названия одной строкой.")
        return
    entries = advertiser_catalog.search(query)
    if not entries:
        text = "Ничего не найдено. Попробуйте другой запрос или выберите новый advertiser."
        _, keyboard = destination_advertiser_results("Результаты поиска", ())
        await message.reply(
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        return
    text, keyboard = destination_advertiser_results("Результаты поиска", entries)
    await message.reply(text, reply_markup=keyboard, parse_mode="HTML")


@router.message(UserStates.waiting_for_destination_custom_advertiser)
async def receive_custom_destination_advertiser(
    message: Message,
    state: FSMContext,
):
    advertiser = (message.text or "").strip()
    if not advertiser or "\n" in advertiser or "\r" in advertiser:
        await message.reply("Укажите точное название одной строкой.")
        return
    if len(advertiser) > 200:
        await message.reply("Название слишком длинное.")
        return
    await state.update_data(
        destination_advertiser=advertiser,
        destination_url_parameters="",
        destination_technician_note=(
            "Новый advertiser: серверные URL-параметры ещё не настроены"
        ),
    )
    await state.set_state(UserStates.waiting_for_destination_offer)
    text, keyboard = destination_offer_prompt(advertiser)
    await message.reply(text, reply_markup=keyboard, parse_mode="HTML")


@router.message(UserStates.waiting_for_destination_offer)
async def create_destination_ticket(
    message: Message,
    state: FSMContext,
    ticket_service: TicketService,
    notification_service: NotificationService,
    destination_service: DestinationService,
    advertiser_catalog: AdvertiserCatalog,
):
    source_body = (message.text or "").strip()
    if not source_body:
        await message.reply("Тело оффера не может быть пустым.")
        return

    state_data = await state.get_data()
    advertiser = str(state_data.get("destination_advertiser") or "").strip()
    if not advertiser:
        await state.set_state(UserStates.waiting_for_destination_advertiser)
        text, keyboard = destination_advertiser_prompt(advertiser_catalog.popular())
        await message.reply(text, reply_markup=keyboard, parse_mode="HTML")
        return

    display_name = telegram_display_name(message.from_user)
    ticket_service.register_user(
        message.from_user.id,
        display_name,
        message.date,
    )
    progress = await message.reply("Оформляю Destination и создаю задачу…")
    formatting_failed = False
    try:
        result = await destination_service.format_offer(
            advertiser=advertiser,
            source_body=source_body,
            url_parameters=str(
                state_data.get("destination_url_parameters") or ""
            ),
        )
        formatted_body = result.text
    except DestinationFormatterError:
        formatting_failed = True
        logger.exception("Destination formatting failed")
        formatted_body = (
            f"Destination | {advertiser}\n\n"
            "Автоматическое оформление не выполнено. "
            "Исходные данные сохранены в переписке."
        )

    ticket = ticket_service.create_destination_ticket(
        message.from_user.id,
        advertiser,
        source_body,
        formatted_body,
        message.date,
        request_key=f"telegram-message:{message.chat.id}:{message.message_id}",
        technician_note=(
            str(state_data.get("destination_technician_note") or "").strip()
            or None
        ),
    )
    text, keyboard = ticket_created(
        ticket.ticket_id,
        formatting_failed=formatting_failed,
    )
    await progress.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await notification_service.new_ticket(ticket, display_name)
    await state.clear()
