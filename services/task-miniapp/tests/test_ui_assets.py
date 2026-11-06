from __future__ import annotations

import re
import unittest
from importlib.resources import files
from pathlib import Path


class UiAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        source_root = (
            Path(__file__).resolve().parents[1]
            / "src/dest_miniapp/web"
        )
        if source_root.is_dir():
            cls.css = (source_root / "assets/styles.css").read_text(encoding="utf-8")
            cls.js = (source_root / "assets/app.js").read_text(encoding="utf-8")
            cls.html = (source_root / "index.html").read_text(encoding="utf-8")
        else:
            package = files("dest_miniapp")
            cls.css = package.joinpath("web/assets/styles.css").read_text(encoding="utf-8")
            cls.js = package.joinpath("web/assets/app.js").read_text(encoding="utf-8")
            cls.html = package.joinpath("web/index.html").read_text(encoding="utf-8")

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

    def test_uncap_controls_cover_create_and_draft_forms(self) -> None:
        self.assertIn('id="cap-uncap"', self.html)
        self.assertIn('id="draft-cap-uncap"', self.html)
        self.assertIn('? "uncap" : Number($("#cap-input").value)', self.js)
        self.assertIn('? "uncap" : Number($("#draft-cap").value)', self.js)

    def test_tools_page_contains_role_scoped_help(self) -> None:
        self.assertIn('id="help-content"', self.html)
        self.assertIn('api("/api/help")', self.js)
        self.assertIn('class="faq-item"', self.js)

    def test_postbacks_are_shown_before_help(self) -> None:
        self.assertLess(
            self.html.index('id="postback-list"'),
            self.html.index('id="help-content"'),
        )


if __name__ == "__main__":
    unittest.main()
