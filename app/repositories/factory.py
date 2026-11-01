from __future__ import annotations

import config

from app.repositories.base import TicketRepository
from app.repositories.module_adapter import ModuleTicketRepository


def build_repository(backend_name: str | None = None) -> TicketRepository:
    selected = (backend_name or config.DATABASE_BACKEND).strip().lower()
    if selected == "postgres":
        from app import postgres

        return ModuleTicketRepository(postgres)
    if selected == "sqlite":
        from app import sql

        return ModuleTicketRepository(sql)
    raise RuntimeError(f"Unsupported DATABASE_BACKEND: {selected}")
