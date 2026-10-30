from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


class AioPayloadError(ValueError):
    """AIO returned a row that does not satisfy the monitored contract."""


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AioPayloadError(f"{field} must be an object")
    return value


def _text(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise AioPayloadError(f"{field} is required")
    return result


def _money(value: Any, field: str) -> Decimal:
    if value is None or (isinstance(value, str) and not value.strip()):
        return Decimal("0")
    if isinstance(value, bool):
        raise AioPayloadError(f"{field} must be numeric")
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise AioPayloadError(f"{field} must be numeric") from exc


@dataclass(frozen=True, slots=True)
class AioConversion:
    conversion_uuid: str
    visit_uuid: str
    conversion_at: str
    type_uuid: str
    type_name: str
    revenue: Decimal
    destination_uuid: str
    destination_name: str
    advertiser_uuid: str | None
    advertiser_name: str | None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> AioConversion:
        conversion_type = _mapping(row.get("type"), "type")
        destination = _mapping(row.get("destination"), "destination")
        advertiser_value = destination.get("advertiser")
        advertiser = advertiser_value if isinstance(advertiser_value, Mapping) else {}
        advertiser_uuid = str(advertiser.get("uuid") or "").strip() or None
        advertiser_name = str(advertiser.get("name") or "").strip() or None
        return cls(
            conversion_uuid=_text(row.get("uuid"), "uuid"),
            visit_uuid=_text(row.get("visit_uuid"), "visit_uuid"),
            conversion_at=_text(row.get("conversion_date"), "conversion_date"),
            type_uuid=_text(conversion_type.get("uuid"), "type.uuid"),
            type_name=_text(conversion_type.get("name"), "type.name"),
            revenue=_money(row.get("revenue"), "revenue"),
            destination_uuid=_text(destination.get("uuid"), "destination.uuid"),
            destination_name=_text(destination.get("name"), "destination.name"),
            advertiser_uuid=advertiser_uuid,
            advertiser_name=advertiser_name,
        )

    def is_zero_revenue_ftd(self, ftd_type_uuid: str) -> bool:
        return self.type_uuid == ftd_type_uuid and self.revenue == 0
