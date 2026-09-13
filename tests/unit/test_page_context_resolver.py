"""Tests for PageContextResolver.

Covers:
  - Context extraction from user messages
  - No context when no page keyword present
  - Matching existing pages
  - No match triggers clarification
  - Multiple existing pages shown as choices
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from app.engine.page_context_resolver import (
    extract_requested_context,
    resolve,
    ContextDecision,
)


class TestExtractRequestedContext(unittest.TestCase):

    def test_dashboard_pattern(self):
        self.assertEqual(extract_requested_context("add filter in analytics dashboard"), "analytics")

    def test_page_pattern(self):
        self.assertEqual(extract_requested_context("create marketing page"), "marketing")

    def test_overview_pattern(self):
        self.assertEqual(extract_requested_context("update sales overview"), "sales")

    def test_screen_pattern(self):
        self.assertEqual(extract_requested_context("reports screen"), "reports")

    def test_in_analytics_area(self):
        self.assertEqual(extract_requested_context("add chart in analytics area"), "analytics")

    def test_on_marketing_section(self):
        self.assertEqual(extract_requested_context("add KPI on marketing section"), "marketing")

    def test_to_analytics(self):
        self.assertEqual(extract_requested_context("add export button to analytics dashboard"), "analytics")

    def test_no_context(self):
        self.assertIsNone(extract_requested_context("add chart"))
        self.assertIsNone(extract_requested_context("remove KPI"))
        self.assertIsNone(extract_requested_context("update metrics"))

    def test_context_in_middle(self):
        self.assertEqual(extract_requested_context("I want a filter in the analytics dashboard please"), "analytics")


class TestResolve(unittest.TestCase):

    def test_exact_match(self):
        decision = resolve("add KPI in sales dashboard", existing_pages=["SalesOverviewPage"])
        self.assertEqual(decision.requested_context, "sales")
        self.assertEqual(decision.matched_page, "SalesOverviewPage")
        self.assertEqual(decision.confidence, 1.0)
        self.assertFalse(decision.needs_clarification)

    def test_no_match_triggers_clarification(self):
        decision = resolve("add filter in marketing dashboard", existing_pages=["SalesOverviewPage"])
        self.assertEqual(decision.requested_context, "marketing")
        self.assertIsNone(decision.matched_page)
        self.assertEqual(decision.confidence, 0.0)
        self.assertTrue(decision.needs_clarification)

    def test_no_context_no_clarification(self):
        decision = resolve("add chart", existing_pages=["SalesOverviewPage"])
        self.assertIsNone(decision.requested_context)
        self.assertFalse(decision.needs_clarification)

    def test_choices_include_all_pages_plus_create(self):
        decision = resolve("analytics dashboard", existing_pages=["SalesOverviewPage", "RevenueDashboardPage"])
        self.assertTrue(decision.needs_clarification)
        self.assertEqual(len(decision.choices), 3)
        self.assertEqual(decision.choices[0].id, "page:SalesOverviewPage")
        self.assertEqual(decision.choices[1].id, "page:RevenueDashboardPage")
        self.assertEqual(decision.choices[2].id, "create_new")

    def test_empty_pages_list(self):
        decision = resolve("analytics dashboard", existing_pages=[])
        self.assertTrue(decision.needs_clarification)
        self.assertEqual(len(decision.choices), 1)
        self.assertEqual(decision.choices[0].id, "create_new")

    def test_case_insensitive_match(self):
        decision = resolve("SALES dashboard", existing_pages=["salesoverviewpage"])
        self.assertEqual(decision.confidence, 1.0)
        self.assertEqual(decision.matched_page, "salesoverviewpage")

    def test_partial_match_not_fooled(self):
        decision = resolve("analytics dashboard", existing_pages=["AnalyticsToolbar"])
        self.assertEqual(decision.confidence, 1.0)

    def test_no_pages_no_context(self):
        decision = resolve("add chart", existing_pages=[])
        self.assertIsNone(decision.requested_context)
        self.assertFalse(decision.needs_clarification)

    def test_matched_page_is_first_alpha(self):
        decision = resolve("sales dashboard", existing_pages=["RevenueDashboardPage", "SalesOverviewPage"])
        self.assertEqual(decision.matched_page, "SalesOverviewPage")

    def test_choices_to_dict(self):
        decision = resolve("reports dashboard", existing_pages=["SalesOverviewPage"])
        cd = decision.to_dict()
        self.assertIn("choices", cd)
        self.assertIn("requested_context", cd)
        self.assertEqual(cd["requested_context"], "reports")
        self.assertEqual(cd["confidence"], 0.0)


if __name__ == "__main__":
    unittest.main()
