"""Прикладные сценарии HelpDesk."""

from .destination_service import DestinationService
from .postback_service import PostbackService
from .ticket_service import TicketService

__all__ = ["DestinationService", "PostbackService", "TicketService"]
