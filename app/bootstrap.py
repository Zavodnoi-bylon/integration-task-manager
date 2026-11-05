import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from app.handlers import (
    client_tickets,
    common,
    drafts,
    postbacks,
    ticket_messages,
    technician_tickets,
    ticket_actions,
)
from app.repositories import TicketRepository, build_repository
from app.services.advertiser_catalog import AdvertiserCatalog
from app.services.destination_service import DestinationService
from app.services.notification_service import NotificationService
from app.services.postback_service import PostbackService
from app.services.ticket_service import TicketService
from app.settings import Settings, load_settings


def build_dispatcher(
    bot: Bot,
    settings: Settings,
    repository: TicketRepository | None = None,
) -> Dispatcher:
    ticket_service = TicketService(settings, repository)
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher["ticket_service"] = ticket_service
    dispatcher["notification_service"] = NotificationService(bot, settings)
    dispatcher["postback_service"] = PostbackService(settings)
    dispatcher["destination_service"] = DestinationService(
        api_key=settings.openrouter_api_key,
        model=settings.destination_model,
        prompt_path=settings.destination_prompt_path,
    )
    dispatcher["advertiser_catalog"] = AdvertiserCatalog(
        bot_key=settings.bot_key,
        catalog_path=settings.destination_catalog_path,
    )
    dispatcher.include_routers(
        common.router,
        postbacks.router,
        client_tickets.router,
        ticket_messages.router,
        technician_tickets.router,
        ticket_actions.router,
        drafts.router,
    )
    return dispatcher


async def run() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = load_settings()
    repository = build_repository()
    repository.initialize()
    repository.sync_configured_roles(
        settings.bot_key,
        settings.admin_users,
        settings.technician_users,
    )
    bot = Bot(token=settings.bot_token)
    dispatcher = build_dispatcher(bot, settings, repository)
    await dispatcher.start_polling(bot)
