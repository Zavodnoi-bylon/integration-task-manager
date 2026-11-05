from __future__ import annotations

import re
import unittest
from importlib.resources import files
from pathlib import Path


class UiAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        source_asset = (
            Path(__file__).resolve().parents[1]
            / "src/dest_miniapp/web/assets/styles.css"
        )
        if source_asset.is_file():
            cls.css = source_asset.read_text(encoding="utf-8")
        else:
            cls.css = (
                files("dest_miniapp")
                .joinpath("web/assets/styles.css")
                .read_text(encoding="utf-8")
            )

    def test_long_draft_preview_cannot_expand_the_viewport(self) -> None:
        self.assertRegex(
            self.css,
            re.compile(r"\.draft-card\s*\{[^}]*min-width:\s*0", re.DOTALL),
        )
        self.assertRegex(
            self.css,
            re.compile(
                r"\.draft-card strong,\s*\.draft-card span\s*"
                r"\{[^}]*max-width:\s*100%[^}]*overflow:\s*hidden",
                re.DOTALL,
            ),
        )
        self.assertRegex(
            self.css,
            re.compile(r"html\s*\{[^}]*overflow-x:\s*hidden", re.DOTALL),
        )


if __name__ == "__main__":
    unittest.main()
