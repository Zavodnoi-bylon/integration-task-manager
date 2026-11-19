from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Role = Literal["client", "supervisor", "technician", "admin"]


@dataclass(frozen=True)
class TelegramIdentity:
    telegram_user_id: int
    display_name: str
    username: str | None


@dataclass(frozen=True)
class Actor:
    telegram_user_id: int
    display_name: str
    role: Role
    active: bool = True

    @property
    def is_staff(self) -> bool:
        return self.role in {"technician", "admin"}

    @property
    def is_supervisor(self) -> bool:
        return self.role == "supervisor"
