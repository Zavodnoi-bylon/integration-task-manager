from __future__ import annotations

import argparse
import json
from pathlib import Path


TENANT_TO_BOT = {
    "TA": "kalisto",
    "TheRaw": "mephisto",
}
TECHNICIAN_NOTES = {
    ("TA", "V1VA"): "Выбрать advertiser для нужного GEO",
}


def build_catalog(source: Path) -> dict[str, object]:
    rows = json.loads(source.read_text(encoding="utf-8"))
    advertisers: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        tenant = str(row.get("tenant") or "").strip()
        name = str(row.get("advertiser") or "").strip()
        if tenant not in TENANT_TO_BOT or not name:
            continue
        identity = tenant, name.casefold()
        if identity in seen:
            raise ValueError(f"Duplicate advertiser: {tenant} / {name}")
        seen.add(identity)
        item: dict[str, object] = {
            "bot_key": TENANT_TO_BOT[tenant],
            "name": name,
            "url_parameters": str(row.get("url_parameters") or "").strip(),
            "priority": max(0, int(row.get("events_30d") or 0)),
        }
        note = TECHNICIAN_NOTES.get((tenant, name))
        if note:
            item["technician_note"] = note
        advertisers.append(item)
    advertisers.sort(key=lambda item: (str(item["bot_key"]), str(item["name"]).casefold()))
    return {"version": 1, "advertisers": advertisers}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    payload = build_catalog(args.source)
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    args.destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
