from __future__ import annotations

from typing import Literal, TypeAlias


UNCAP: Literal["uncap"] = "uncap"
CapValue: TypeAlias = int | Literal["uncap"]


def is_valid_cap(value: object) -> bool:
    return value == UNCAP or (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 1 <= value <= 1_000_000
    )


def cap_label(value: CapValue) -> str:
    if not is_valid_cap(value):
        raise ValueError("Cap must be a positive integer or uncap")
    return str(value)
