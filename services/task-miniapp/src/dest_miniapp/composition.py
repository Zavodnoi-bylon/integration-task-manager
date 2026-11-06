from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from .api import create_app
from .canonical import (
    CanonicalAdvertiserCatalogAdapter,
    CanonicalDestinationFormatterAdapter,
    PostgresMiniAppGateway,
)
from .postgres import (
    PostgresDatabase,
    PostgresIdempotencyReservations,
    PostgresSessionStore,
)
from .postgres_uow import PostgresTicketMutationUow
from .settings import Settings
from .use_cases import TicketWorkflow


@dataclass
class MiniAppRuntime:
    app: FastAPI
    database: PostgresDatabase

    async def open(self) -> None:
        await self.database.open()

    async def close(self) -> None:
        await self.database.close()


def build_postgres_runtime(
    *,
    settings: Settings,
    database_url: str,
    advertiser_catalog: Any,
    destination_service: Any,
    postback_service: Any,
    admin_ids: frozenset[int],
    technician_ids: frozenset[int],
    technical_actor_telegram_id: int,
    help_directory: Path | None = None,
) -> MiniAppRuntime:
    database = PostgresDatabase(database_url)
    catalog_adapter = CanonicalAdvertiserCatalogAdapter(
        advertiser_catalog, profile_key=settings.profile_key
    )
    gateway = PostgresMiniAppGateway(
        database,
        profile_key=settings.profile_key,
        profile_name=settings.profile_name,
        advertiser_catalog=advertiser_catalog,
        postback_service=postback_service,
        admin_ids=admin_ids,
        technician_ids=technician_ids,
    )
    workflow = TicketWorkflow(
        PostgresTicketMutationUow(
            database,
            technical_actor_telegram_id=technical_actor_telegram_id,
        ),
        advertisers=catalog_adapter,
        destination_formatter=CanonicalDestinationFormatterAdapter(
            destination_service
        ),
        idempotency_reservations=PostgresIdempotencyReservations(database),
    )

    @asynccontextmanager
    async def lifespan(_app):
        await database.open()
        try:
            yield
        finally:
            await database.close()

    app = create_app(
        settings=settings,
        gateway=gateway,
        sessions=PostgresSessionStore(database),
        mutations=workflow,
        help_directory=help_directory,
        lifespan=lifespan,
    )
    return MiniAppRuntime(app=app, database=database)
