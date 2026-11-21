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

    def test_new_advertiser_is_available_to_both_profiles(self) -> None:
        self.assertEqual(
            self.js.count(
                "const newOption = '<option value=\"__new__\">+ Новый рекламодатель</option>';"
            ),
            2,
        )
        self.assertNotIn('const newOption = isKalisto() ? ""', self.js)
        self.assertNotIn("Выберите рекламодателя из списка", self.js)

    def test_new_advertiser_is_first_selectable_option(self) -> None:
        self.assertIn(
            "'<option value=\"\" disabled selected hidden>Выбрать…</option>' + newOption + "
            "state.advertisers.map",
            self.js,
        )
        self.assertIn(
            "'<option value=\"\" disabled selected hidden>Выбрать…</option>' + newOption + "
            "state.draftAdvertisers.map",
            self.js,
        )

    def test_advertiser_placeholder_is_not_a_selectable_option(self) -> None:
        placeholder = '<option value="" disabled selected hidden>Выбрать…</option>'
        self.assertEqual(self.js.count(placeholder), 2)
        self.assertIn(placeholder, self.html)
        self.assertNotIn('<option value="">Выбрать…</option>', self.js)
        self.assertNotIn('<option value="">Выбрать…</option>', self.html)

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

    def test_kalisto_naming_is_generated_but_can_be_overridden(self) -> None:
        for field_id in (
            "destination-offer",
            "destination-platform",
            "destination-approach",
            "draft-offer",
            "draft-platform",
            "draft-approach",
        ):
            self.assertIn(f'id="{field_id}"', self.html)
        self.assertEqual(self.html.count("Итоговый нейминг"), 2)
        self.assertEqual(self.html.count("Собрать заново"), 2)
        self.assertIn('function buildKalistoNaming(context)', self.js)
        self.assertIn(
            '[advertiser, offerPlatform, geo, approach, cap, rateWithCurrency]',
            self.js,
        )
        self.assertIn('state.namingManual[context]', self.js)
        self.assertIn('resetAutoNaming("create")', self.js)
        self.assertIn('resetAutoNaming("draft")', self.js)
        self.assertIn('offer_name: value("offer")', self.js)
        self.assertIn('approach: value("approach")', self.js)

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

    def test_explorer_handoff_is_only_in_summary_and_mobile_safe(self) -> None:
        self.assertNotIn('id="analytics-button"', self.html)
        self.assertIn('id="summary-open-explorer"', self.html)
        self.assertIn('state.me.links?.analytics', self.js)
        self.assertIn('api("/api/analytics/handoff"', self.js)
        self.assertIn('tg.openLink(url)', self.js)
        self.assertNotIn('tg.openTelegramLink(url)', self.js)
        self.assertNotIn(".bottom-nav.analytics-enabled", self.css)
        self.assertIn(".bottom-nav.summary-enabled", self.css)
        self.assertIn("repeat(4, minmax(0, 1fr))", self.css)
        self.assertRegex(
            self.css,
            re.compile(
                r"\.bottom-nav button\s*\{[^}]*min-width:\s*0"
                r"[^}]*text-overflow:\s*ellipsis",
                re.DOTALL,
            ),
        )

    def test_theraw_summary_uses_internal_navigation_and_back(self) -> None:
        self.assertIn('id="summary-page"', self.html)
        self.assertIn('id="summary-button"', self.html)
        self.assertIn('id="summary-open-explorer"', self.html)
        self.assertIn("state.me?.capabilities?.analytics_compact", self.js)
        self.assertIn('navigate("summary")', self.js)
        self.assertIn('state.page === "summary"', self.js)
        self.assertIn('else if (state.page === "summary") navigate("tickets")', self.js)
        self.assertIn('$("#summary-open-explorer").addEventListener("click", openAnalytics)', self.js)
        self.assertIn('api("/api/analytics/handoff"', self.js)
        self.assertNotIn('window.open(', self.js)

    def test_theraw_summary_has_only_agreed_kpis_and_destination_table(self) -> None:
        for period in ("today", "week", "month", "custom"):
            self.assertIn(f'data-summary-period="{period}"', self.html)
        for element_id in (
            "summary-created",
            "summary-launched",
            "summary-not-launched",
            "summary-reg-sum",
            "summary-ftd-sum",
            "summary-advertiser",
            "summary-status",
            "summary-destination-list",
            "summary-updated",
        ):
            self.assertIn(f'id="{element_id}"', self.html)
        for label in ("ID", "Dest", "Создан", "REG", "FTD"):
            self.assertIn(f"<span>{label}</span>", self.html)
        for status in ("all", "launched", "not_launched", "with_ftd"):
            self.assertIn(f'value="{status}"', self.html)
        self.assertIn('/api/analytics/compact/summary?${summaryParams}', self.js)
        self.assertIn('/api/analytics/compact/destinations?${listParams}', self.js)
        self.assertNotIn('data-summary-list="geo"', self.html)
        self.assertNotIn("top_geos", self.js)
        self.assertNotIn("is_demo", self.js)

    def test_theraw_summary_has_loading_empty_error_and_mobile_guards(self) -> None:
        for element_id in ("summary-loading", "summary-empty", "summary-error"):
            self.assertIn(f'id="{element_id}"', self.html)
        for state_name in ("loading", "empty", "error", "content"):
            self.assertIn(f'name !== "{state_name}"', self.js)
        self.assertIn("timeoutMs: 12000", self.js)
        self.assertIn('error.name === "AbortError"', self.js)
        self.assertIn("coverage.history_complete_for_cohort", self.js)
        self.assertIn("coverage.mart_refreshed_at", self.js)
        self.assertRegex(
            self.css,
            re.compile(
                r"\.analytics-summary\s*\{[^}]*min-width:\s*0"
                r"[^}]*overflow-x:\s*hidden",
                re.DOTALL,
            ),
        )
        self.assertIn(
            ".summary-periods { grid-template-columns: repeat(2, minmax(0, 1fr)); }",
            self.css,
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

    def test_profile_important_comment_is_optional_reusable_and_wrapped(self) -> None:
        for field_id in (
            "important-comment",
            "important-comment-history",
            "draft-important-comment",
            "draft-important-comment-history",
        ):
            self.assertIn(f'id="{field_id}"', self.html)
        self.assertIn('api("/api/destination-comments")', self.js)
        self.assertIn('payload.important_comment = $("#important-comment").value.trim()', self.js)
        self.assertIn(
            "importantMarkup(ticket.important_comment, ticket.new_advertiser)",
            self.js,
        )
        self.assertIn('if (newAdvertiser) lines.push("<p>Новый рекл</p>");', self.js)
        self.assertIn('if (!lines.length) return "";', self.js)
        self.assertIn('class="important-comment"', self.js)
        self.assertIn(
            '$("#important-comment-field").classList.toggle("hidden", !destination);',
            self.js,
        )
        self.assertIn(
            '$("#draft-important-comment-field").classList.toggle("hidden", !destination);',
            self.js,
        )
        self.assertEqual(self.html.count('placeholder="Сделать тест реги"'), 2)
        self.assertNotIn(">Параметры clickid/subid<", self.html)
        self.assertNotIn(">Дополнительно<", self.html)
        self.assertIn('<input id="destination-clickid" type="hidden">', self.html)
        self.assertIn('<input id="destination-additional" type="hidden">', self.html)
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
