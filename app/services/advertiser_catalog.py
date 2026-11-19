from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.domain import AdvertiserEntry, ExternalIdentifierPolicy


LOG = logging.getLogger(__name__)


def _policies(
    rows: Any,
    *,
    bot_key: str,
    name: str,
) -> tuple[ExternalIdentifierPolicy, ...]:
    policy_rows = rows or []
    if not isinstance(policy_rows, list):
        raise ValueError(
            f"external_id_policy must be a list for {bot_key}: {name}"
        )
    policies: list[ExternalIdentifierPolicy] = []
    parameters: set[str] = set()
    for policy_row in policy_rows:
        if not isinstance(policy_row, Mapping):
            raise ValueError(f"Invalid external ID policy for {bot_key}: {name}")
        parameter = str(policy_row.get("parameter") or "").strip()
        action = str(policy_row.get("action") or "").strip()
        output_parameter = (
            str(policy_row.get("output_parameter") or "").strip() or None
        )
        if not parameter or any(char in parameter for char in "&=?#"):
            raise ValueError(
                f"Invalid external ID parameter for {bot_key}: {name}"
            )
        if action not in {"replace_visit_uuid", "remove"}:
            raise ValueError(f"Invalid external ID action for {bot_key}: {name}")
        if action == "remove" and output_parameter is not None:
            raise ValueError(
                f"Remove policy cannot rename a parameter for {bot_key}: {name}"
            )
        normalized = parameter.casefold()
        if normalized in parameters:
            raise ValueError(
                f"Duplicate external ID policy for {bot_key}: {name}"
            )
        parameters.add(normalized)
        policies.append(
            ExternalIdentifierPolicy(
                parameter=parameter,
                action=action,
                output_parameter=output_parameter,
            )
        )
    return tuple(policies)


def _entries(
    rows: Sequence[Mapping[str, Any]],
    *,
    bot_key: str,
    database_rows: bool,
) -> tuple[AdvertiserEntry, ...]:
    result: list[AdvertiserEntry] = []
    normalized_names: set[str] = set()
    keys: set[str] = set()
    for row in rows:
        if not database_rows and row.get("bot_key") != bot_key:
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            raise ValueError(f"Empty advertiser name for bot {bot_key}")
        normalized = name.casefold()
        if normalized in normalized_names:
            raise ValueError(f"Duplicate advertiser name for {bot_key}: {name}")
        key = (
            str(row.get("catalog_key") or "").strip()
            if database_rows
            else hashlib.sha256(
                f"{bot_key}\0{normalized}".encode("utf-8")
            ).hexdigest()[:12]
        )
        if not key or key in keys:
            raise ValueError(f"Advertiser key collision for {bot_key}: {name}")
        normalized_names.add(normalized)
        keys.add(key)
        result.append(
            AdvertiserEntry(
                key=key,
                name=name,
                url_parameters=str(row.get("url_parameters") or "").strip(),
                technician_note=(
                    str(row.get("technician_note") or "").strip() or None
                ),
                priority=max(0, int(row.get("priority") or 0)),
                external_id_policy=_policies(
                    row.get("external_id_policy"),
                    bot_key=bot_key,
                    name=name,
                ),
            )
        )
    return tuple(sorted(result, key=lambda item: item.name.casefold()))


class AdvertiserCatalog:
    """Bot-scoped catalog backed by PostgreSQL with a static safe fallback."""

    def __init__(
        self,
        *,
        bot_key: str,
        catalog_path: str,
        database_url: str = "",
        refresh_seconds: float = 60.0,
    ) -> None:
        path = Path(catalog_path)
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[2] / path
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("advertisers")
        if not isinstance(rows, list) or not all(
            isinstance(row, Mapping) for row in rows
        ):
            raise ValueError("Advertiser catalog must contain an advertisers list")
        fallback = _entries(rows, bot_key=bot_key, database_rows=False)
        if not fallback:
            raise ValueError(f"Advertiser catalog is empty for bot {bot_key}")
        if refresh_seconds <= 0:
            raise ValueError("refresh_seconds must be positive")
        self._bot_key = bot_key
        self._database_url = database_url.strip()
        self._refresh_seconds = refresh_seconds
        self._refresh_at = 0.0
        self._refresh_lock = threading.Lock()
        self._entries = fallback
        self._by_key = {entry.key: entry for entry in fallback}

    def _refresh_if_due(self) -> None:
        if not self._database_url or time.monotonic() < self._refresh_at:
            return
        with self._refresh_lock:
            if time.monotonic() < self._refresh_at:
                return
            self._refresh_at = time.monotonic() + self._refresh_seconds
            try:
                with psycopg.connect(
                    self._database_url,
                    connect_timeout=3,
                    row_factory=dict_row,
                    autocommit=True,
                ) as connection:
                    rows = connection.execute(
                        """
                        SELECT catalog_key,name,url_parameters,technician_note,
                               priority,external_id_policy
                        FROM ticketing.advertiser_catalog
                        WHERE profile_key=%s AND NOT aio_archived
                        ORDER BY normalized_name
                        """,
                        (self._bot_key,),
                    ).fetchall()
                if rows:
                    refreshed = _entries(
                        rows,
                        bot_key=self._bot_key,
                        database_rows=True,
                    )
                    self._entries = refreshed
                    self._by_key = {entry.key: entry for entry in refreshed}
            except (psycopg.Error, ValueError):
                LOG.exception(
                    "Unable to refresh advertiser catalog for profile %s; "
                    "using the last valid snapshot",
                    self._bot_key,
                )

    def get(self, key: str) -> AdvertiserEntry | None:
        self._refresh_if_due()
        return self._by_key.get(key)

    def popular(self, limit: int = 6) -> tuple[AdvertiserEntry, ...]:
        self._refresh_if_due()
        return tuple(
            sorted(
                self._entries,
                key=lambda item: (-item.priority, item.name.casefold()),
            )[:limit]
        )

    def search(self, query: str, limit: int = 8) -> tuple[AdvertiserEntry, ...]:
        self._refresh_if_due()
        needle = query.strip().casefold()
        if not needle:
            return ()
        starts = [
            item for item in self._entries if item.name.casefold().startswith(needle)
        ]
        contains = [
            item
            for item in self._entries
            if needle in item.name.casefold() and item not in starts
        ]
        return tuple((starts + contains)[:limit])

    def page(
        self,
        page: int,
        page_size: int = 8,
    ) -> tuple[tuple[AdvertiserEntry, ...], int]:
        self._refresh_if_due()
        page = max(0, page)
        total_pages = max(1, (len(self._entries) + page_size - 1) // page_size)
        page = min(page, total_pages - 1)
        start = page * page_size
        return self._entries[start:start + page_size], total_pages

    def invalidate(self) -> None:
        """Force the next catalog read to fetch the latest database snapshot."""
        with self._refresh_lock:
            self._refresh_at = 0.0

    @property
    def count(self) -> int:
        self._refresh_if_due()
        return len(self._entries)
