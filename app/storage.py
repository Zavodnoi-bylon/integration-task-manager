"""Deprecated compatibility import.

Application code must use ``app.repositories.build_repository``. This module is
kept temporarily so third-party imports fail with a clear migration path instead
of silently re-exporting two different persistence APIs.
"""

from app.repositories import TicketRepository, build_repository

__all__ = ["TicketRepository", "build_repository"]
