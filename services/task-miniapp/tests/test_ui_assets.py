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

    def test_cap_mode_buttons_cover_create_and_draft_forms(self) -> None:
        for name in ("cap-mode", "draft-cap-mode"):
            self.assertIn(f'name="{name}" value="unlim"', self.html)
            self.assertIn(f'name="{name}" value="daily"', self.html)
            self.assertIn(f'name="{name}" value="monthly"', self.html)
            self.assertIn(f'name="{name}" value="lifetime"', self.html)
        self.assertIn('readCap("#cap-input", "cap-mode")', self.js)
        self.assertIn('readCap("#draft-cap", "draft-cap-mode")', self.js)

    def test_kalisto_moves_cap_directly_below_rate(self) -> None:
        self.assertIn('id="destination-cap-anchor"', self.html)
        self.assertIn('id="draft-cap-anchor"', self.html)
        self.assertIn('$("#destination-cap-anchor").after($("#cap-fields"))', self.js)
        self.assertIn('$("#draft-cap-anchor").after($("#draft-cap-fields"))', self.js)

    def test_advertiser_selector_loads_the_full_catalog_without_more_button(self) -> None:
        self.assertNotIn('id="advertiser-more"', self.html)
        self.assertNotIn('id="draft-advertiser-more"', self.html)
        self.assertNotIn("advertiser-more", self.js)
        self.assertIn('mode: "page", page: "1", page_size: "100"', self.js)

    def test_advertiser_catalog_has_profile_scoped_manual_refresh(self) -> None:
        self.assertIn('id="advertiser-refresh"', self.html)
        self.assertIn('id="draft-advertiser-refresh"', self.html)
        self.assertEqual(
            self.html.count("Обновить список рекламодателей"),
            2,
        )
        self.assertIn('api("/api/advertisers/refresh"', self.js)
        self.assertIn('["pending", "running"].includes(result.status)', self.js)
        self.assertIn('toast("Список рекламодателей обновлён")', self.js)
        self.assertIn(".catalog-refresh-button", self.css)

    def test_long_urls_and_unbroken_text_wrap_inside_mobile_containers(self) -> None:
        for selector in (".destination-url", ".message p", ".faq-body"):
            self.assertRegex(
                self.css,
                re.compile(
                    re.escape(selector) + r"\s*\{[^}]*overflow-wrap:\s*anywhere",
                    re.DOTALL,
                ),
            )
        self.assertRegex(
            self.css,
            re.compile(
                r"dialog\s*\{[^}]*max-width:\s*calc\(100%\s*-\s*32px\)"
                r"[^}]*overflow-x:\s*hidden",
                re.DOTALL,
            ),
        )
        self.assertIn("grid-template-columns: minmax(0, 1fr)", self.css)

    def test_required_destination_fields_name_only_actual_missing_values(self) -> None:
        self.assertIn('"Не заполнено поле" : "Не заполнены поля"', self.js)
        for label in (
            "рекламодатель",
            "CAP",
            "нейминг",
            "GEO",
            "ставка",
            "валюта",
            "основной ленд",
        ):
            self.assertIn(f'"{label}"', self.js)
        self.assertIn('setAttribute("aria-invalid", "true")', self.js)
        self.assertIn('missingKalistoFields("destination-", payload.details)', self.js)
        self.assertIn('missingDraftSubmitFields()', self.js)
        self.assertNotIn("Выберите рекламодателя, CAP и его категорию", self.js)
        self.assertNotIn(
            "Заполните нейминг, GEO, ставку, валюту и основной ленд",
            self.js,
        )
        self.assertIn('[aria-invalid="true"]', self.css)

    def test_public_number_is_displayed_but_internal_id_stays_in_routes(self) -> None:
        self.assertIn(
            "return ticket.ticket_number || ticket.ticket_id;",
            self.js,
        )
        self.assertIn('data-id="${ticket.ticket_id}"', self.js)
        self.assertIn("openTicket(result.ticket_id)", self.js)
        self.assertNotIn('<span class="ticket-id">#${ticket.ticket_id}</span>', self.js)

    def test_tools_page_contains_role_scoped_help(self) -> None:
        self.assertIn('id="help-content"', self.html)
        self.assertIn('api("/api/help")', self.js)
        self.assertIn('class="faq-item"', self.js)

    def test_postbacks_are_shown_before_help(self) -> None:
        self.assertLess(
            self.html.index('id="postback-list"'),
            self.html.index('id="help-content"'),
        )

    def test_analytics_link_is_optional_and_mobile_safe(self) -> None:
        self.assertIn('id="analytics-button"', self.html)
        self.assertIn('state.me.links?.analytics', self.js)
        self.assertIn('api("/api/analytics/handoff"', self.js)
        self.assertIn('tg.openLink(url)', self.js)
        self.assertNotIn('tg.openTelegramLink(url)', self.js)
        self.assertIn(".bottom-nav.analytics-enabled", self.css)
        self.assertIn("repeat(4, minmax(0, 1fr))", self.css)
        self.assertRegex(
            self.css,
            re.compile(
                r"\.bottom-nav button\s*\{[^}]*min-width:\s*0"
                r"[^}]*text-overflow:\s*ellipsis",
                re.DOTALL,
            ),
        )

    def test_buying_flag_is_role_scoped_and_has_no_history_ui(self) -> None:
        self.assertIn('id="buying-filter"', self.html)
        self.assertIn('data-buying="true"', self.html)
        self.assertIn("state.me.capabilities.buying_flag", self.js)
        self.assertIn('params.set("issued_to_buying", "true")', self.js)
        self.assertIn('data-buying-flag="true"', self.js)
        self.assertIn('data-buying-flag="false"', self.js)
        self.assertIn("ticket.issued_to_buying", self.js)
        self.assertIn(".ticket-card.issued-to-buying", self.css)
        self.assertNotIn("buying-history", self.html + self.js)

    def test_ticket_actions_are_rendered_above_ticket_content(self) -> None:
        action_row = (
            '<div class="action-row detail-action-row">'
            '${buyingFlagButton(ticket)}${actionButtons(ticket)}</div>'
        )
        self.assertIn(action_row, self.js)
        self.assertLess(
            self.js.index(action_row),
            self.js.index('${destination}\n      </header>'),
        )
        self.assertIn(".detail-action-row", self.css)

    def test_mephisto_important_comment_is_optional_reusable_and_wrapped(self) -> None:
        for field_id in (
            "important-comment",
            "important-comment-history",
            "draft-important-comment",
            "draft-important-comment-history",
        ):
            self.assertIn(f'id="{field_id}"', self.html)
        self.assertIn('api("/api/destination-comments")', self.js)
        self.assertIn('payload.important_comment = $("#important-comment").value.trim()', self.js)
        self.assertIn('if (!comment) return "";', self.js)
        self.assertIn('class="important-comment"', self.js)
        self.assertIn("!destination || isKalisto()", self.js)
        self.assertRegex(
            self.css,
            re.compile(
                r"\.important-comment p\s*\{[^}]*overflow-wrap:\s*anywhere",
                re.DOTALL,
            ),
        )
        self.assertIn(".destination-result h2 { font-size: 20px", self.css)


if __name__ == "__main__":
    unittest.main()
