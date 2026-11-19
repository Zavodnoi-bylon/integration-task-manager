import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime

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
        database_url=settings.destination_catalog_database_url,
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
    ticket_service = dispatcher["ticket_service"]
    stop_batches = asyncio.Event()
    batch_worker = asyncio.create_task(
        _run_forward_batch_worker(bot, ticket_service, stop_batches),
        name=f"forward-batches-{settings.bot_key}",
    )
    try:
        await dispatcher.start_polling(bot)
    finally:
        stop_batches.set()
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(batch_worker, timeout=5)
        if not batch_worker.done():
            batch_worker.cancel()
            with suppress(asyncio.CancelledError):
                await batch_worker


async def _run_forward_batch_worker(
    bot: Bot,
    ticket_service: TicketService,
    stop: asyncio.Event,
) -> None:
    logger = logging.getLogger(__name__)
    while not stop.is_set():
        try:
            drafts = await asyncio.to_thread(
                ticket_service.finalize_due_forward_batches,
                datetime.now(UTC),
            )
            for draft in drafts:
                try:
                    await bot.send_message(
                        draft.author_id,
                        f"Черновик #{draft.draft_id} готов. Откройте Mini App → Создать, "
                        "выберите тип задачи и проверьте данные.",
                    )
                except Exception:
                    logger.exception(
                        "Unable to notify the author about draft %s",
                        draft.draft_id,
                    )
        except Exception:
            logger.exception("Forward batch worker iteration failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.5)
        except TimeoutError:
            pass
