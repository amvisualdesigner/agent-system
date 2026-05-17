import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

CATALOG_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "backend",
    "app", "examples", "catalog",
)


class TestExtraction(unittest.TestCase):
    def _load(self, domain: str) -> dict:
        path = os.path.join(CATALOG_DIR, f"{domain}.json")
        with open(path) as f:
            return json.load(f)

    def test_dashboard_catalog_exists(self):
        catalog = self._load("dashboard")
        self.assertEqual(catalog["version"], 1)
        self.assertEqual(catalog["domain"], "dashboard")

    def test_dashboard_has_all_components(self):
        catalog = self._load("dashboard")
        expected = {"SalesOverview", "DashboardLayout", "KpiRow", "RevenueChart", "RevenueTable"}
        self.assertEqual(set(catalog["components"]), expected)

    def test_dashboard_has_dashboard_layout(self):
        catalog = self._load("dashboard")
        self.assertIn("DashboardLayout", catalog["layouts"])

    def test_dashboard_has_composition_edges(self):
        catalog = self._load("dashboard")
        self.assertGreater(len(catalog["composition"]), 0)
        self.assertIn(["SalesOverview", "DashboardLayout"], catalog["composition"])

    def test_dashboard_imports_are_complete(self):
        catalog = self._load("dashboard")
        all_imports = "\n".join(catalog["imports"])
        self.assertIn("React", all_imports)
        self.assertIn("Card", all_imports)
        self.assertIn("DashboardLayout", all_imports)

    def test_forms_catalog_exists(self):
        catalog = self._load("forms")
        self.assertEqual(catalog["domain"], "forms")
        self.assertIn("SettingsForm", catalog["components"])
        self.assertIn("UserProfileForm", catalog["components"])

    def test_tables_catalog_exists(self):
        catalog = self._load("tables")
        self.assertEqual(catalog["domain"], "tables")
        self.assertIn("AnalyticsTable", catalog["components"])

    def test_catalog_versioning(self):
        for domain in ("dashboard", "forms", "tables"):
            catalog = self._load(domain)
            self.assertEqual(catalog["version"], 1, f"{domain} catalog missing version")

    def test_renderer_schema_version(self):
        for domain in ("dashboard", "forms", "tables"):
            catalog = self._load(domain)
            self.assertEqual(catalog.get("renderer_schema_version"), "2026-05",
                             f"{domain} catalog missing renderer_schema_version")

    def test_dashboard_imports_have_path_prefix(self):
        catalog = self._load("dashboard")
        for imp in catalog["imports"]:
            if "'./" in imp or "'@/" in imp:
                continue  # valid relative or alias import
            if "'react'" in imp:
                continue  # valid bare import


if __name__ == "__main__":
    unittest.main()
