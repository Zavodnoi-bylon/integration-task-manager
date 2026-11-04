from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Role = Literal["client", "technician", "admin"]


@dataclass(frozen=True)
class TelegramIdentity:
    user_id: int
    display_name: str
    username: str | None


@dataclass(frozen=True)
class Actor:
    user_id: int
    display_name: str
    role: Role
    active: bool = True

    @property
    def is_staff(self) -> bool:
        return self.role in {"technician", "admin"}
