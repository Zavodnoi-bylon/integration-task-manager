from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.domain import AdvertiserEntry


class AdvertiserCatalog:
    """Read-only, bot-scoped advertiser configuration."""

    def __init__(self, *, bot_key: str, catalog_path: str) -> None:
        path = Path(catalog_path)
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[2] / path
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("advertisers")
        if not isinstance(rows, list):
            raise ValueError("Advertiser catalog must contain an advertisers list")

        entries: list[AdvertiserEntry] = []
        normalized_names: set[str] = set()
        keys: set[str] = set()
        for row in rows:
            if not isinstance(row, dict) or row.get("bot_key") != bot_key:
                continue
            name = str(row.get("name") or "").strip()
            if not name:
                raise ValueError(f"Empty advertiser name for bot {bot_key}")
            normalized = name.casefold()
            if normalized in normalized_names:
                raise ValueError(f"Duplicate advertiser name for {bot_key}: {name}")
            key = hashlib.sha256(
                f"{bot_key}\0{normalized}".encode("utf-8")
            ).hexdigest()[:12]
            if key in keys:
                raise ValueError(f"Advertiser key collision for {bot_key}: {name}")
            normalized_names.add(normalized)
            keys.add(key)
            note = str(row.get("technician_note") or "").strip() or None
            entries.append(AdvertiserEntry(
                key=key,
                name=name,
                url_parameters=str(row.get("url_parameters") or "").strip(),
                technician_note=note,
                priority=max(0, int(row.get("priority") or 0)),
            ))

        if not entries:
            raise ValueError(f"Advertiser catalog is empty for bot {bot_key}")
        self._entries = tuple(sorted(entries, key=lambda item: item.name.casefold()))
        self._by_key = {entry.key: entry for entry in self._entries}

    def get(self, key: str) -> AdvertiserEntry | None:
        return self._by_key.get(key)

    def popular(self, limit: int = 6) -> tuple[AdvertiserEntry, ...]:
        return tuple(sorted(
            self._entries,
            key=lambda item: (-item.priority, item.name.casefold()),
        )[:limit])

    def search(self, query: str, limit: int = 8) -> tuple[AdvertiserEntry, ...]:
        needle = query.strip().casefold()
        if not needle:
            return ()
        starts = [item for item in self._entries if item.name.casefold().startswith(needle)]
        contains = [
            item for item in self._entries
            if needle in item.name.casefold() and item not in starts
        ]
        return tuple((starts + contains)[:limit])

    def page(
        self,
        page: int,
        page_size: int = 8,
    ) -> tuple[tuple[AdvertiserEntry, ...], int]:
        page = max(0, page)
        total_pages = max(1, (len(self._entries) + page_size - 1) // page_size)
        page = min(page, total_pages - 1)
        start = page * page_size
        return self._entries[start:start + page_size], total_pages

    @property
    def count(self) -> int:
        return len(self._entries)
