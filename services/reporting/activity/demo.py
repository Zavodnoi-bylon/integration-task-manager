"""Explicit synthetic fixture. Never used as a database-error fallback."""
from __future__ import annotations

import json
from pathlib import Path

DEMO_LABEL = "Демонстрационные данные — не отчёт отдела"
DATASET_PATH = Path(__file__).resolve().parent.parent / "examples" / "demo_dataset.json"


class DemoRepository:
    mode = "demo"

    def health(self) -> None:
        self.snapshot()

    def snapshot(self) -> dict:
        with DATASET_PATH.open(encoding="utf-8") as handle:
            dataset = json.load(handle)
        if dataset.get("demo") is not True:
            raise ValueError("The demonstration dataset must carry demo=true.")
        dataset["coverage"]["note"] = DEMO_LABEL + ". " + dataset["coverage"].get("note", "")
        return dataset
