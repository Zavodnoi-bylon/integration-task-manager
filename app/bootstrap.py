import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from app import storage as sql
from app.handlers import (
    client_tickets,
    common,
    postbacks,
    ticket_messages,
    technician_tickets,
    ticket_actions,
)
from app.services.notification_service import NotificationService
from app.services.postback_service import PostbackService
from app.services.ticket_service import TicketService
from app.settings import Settings, load_settings


def build_dispatcher(
    bot: Bot,
    settings: Settings,
) -> Dispatcher:
    ticket_service = TicketService(settings)
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher["ticket_service"] = ticket_service
    dispatcher["notification_service"] = NotificationService(bot, settings)
    dispatcher["postback_service"] = PostbackService(settings)
    dispatcher.include_routers(
        common.router,
        postbacks.router,
        client_tickets.router,
        ticket_messages.router,
        technician_tickets.router,
        ticket_actions.router,
    )
    return dispatcher


async def run() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = load_settings()
    sql.create_tables()
    sql.sync_user_roles(
        settings.bot_key,
        settings.admin_users,
        settings.technician_users,
    )
    bot = Bot(token=settings.bot_token)
    dispatcher = build_dispatcher(bot, settings)
    await dispatcher.start_polling(bot)
