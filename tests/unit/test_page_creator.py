"""Tests for PageCreator.

Covers:
  - CREATE page with correct name and content
  - MODIFY router when router.tsx exists
  - No MODIFY router when router.tsx missing
  - Idempotency checks
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from app.engine.page_creator import (
    create_page_ops,
    create_page_ops_dry,
    _suggest_page_name,
    _find_router_path,
    _has_route_for_context,
)


class TestSuggestPageName(unittest.TestCase):

    def test_basic(self):
        self.assertEqual(_suggest_page_name("analytics"), "AnalyticsDashboardPage")

    def test_multi_word(self):
        self.assertEqual(_suggest_page_name("marketing"), "MarketingDashboardPage")

    def test_single_letter(self):
        self.assertEqual(_suggest_page_name("a"), "ADashboardPage")


class TestFindRouterPath(unittest.TestCase):

    def test_no_router(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(_find_router_path(tmp))

    def test_finds_frontend_router(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "frontend", "src"))
            router = os.path.join(tmp, "frontend", "src", "router.tsx")
            with open(router, "w") as f:
                f.write("")
            result = _find_router_path(tmp)
            self.assertIsNotNone(result)
            self.assertTrue(result.endswith("router.tsx"))


class TestHasRouteForContext(unittest.TestCase):

    def test_route_exists(self):
        content = """
const router = createBrowserRouter([
  { path: '/analytics', element: <AnalyticsDashboardPage /> },
]);
"""
        self.assertTrue(_has_route_for_context(content, "analytics"))

    def test_route_missing(self):
        content = """
const router = createBrowserRouter([
  { path: '/sales', element: <SalesOverviewPage /> },
]);
"""
        self.assertFalse(_has_route_for_context(content, "analytics"))


class TestCreatePageOps(unittest.TestCase):

    def test_creates_page_with_neutral_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            ops = create_page_ops("analytics", tmp)
            page_ops = [o for o in ops if o.action == "CREATE"]
            self.assertEqual(len(page_ops), 1)
            op = page_ops[0]
            self.assertIn("AnalyticsDashboardPage", op.path)
            self.assertIn("export default function AnalyticsDashboardPage", op.content)
            self.assertNotIn("Outlet", op.content)
            self.assertEqual(op.pipeline_route, "page_creator")

    def test_no_router_modify_when_no_router(self):
        with tempfile.TemporaryDirectory() as tmp:
            ops = create_page_ops("analytics", tmp)
            router_ops = [o for o in ops if o.action == "MODIFY"]
            self.assertEqual(len(router_ops), 0)

    def test_modifies_router_when_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "frontend", "src"))
            router = os.path.join(tmp, "frontend", "src", "router.tsx")
            with open(router, "w") as f:
                f.write("""import { createBrowserRouter } from 'react-router-dom';\nconst router = createBrowserRouter([\n]);\nexport default router;\n""")
            ops = create_page_ops("analytics", tmp)
            router_ops = [o for o in ops if o.action == "MODIFY"]
            self.assertEqual(len(router_ops), 1)

    def test_idempotent_no_duplicate_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "frontend", "src"))
            router = os.path.join(tmp, "frontend", "src", "router.tsx")
            with open(router, "w") as f:
                f.write("""import { createBrowserRouter } from 'react-router-dom';\nconst router = createBrowserRouter([\n  { path: '/analytics', element: <AnalyticsDashboardPage /> },\n]);\nexport default router;\n""")
            ops = create_page_ops("analytics", tmp)
            router_ops = [o for o in ops if o.action == "MODIFY"]
            self.assertEqual(len(router_ops), 0)

    def test_create_page_ops_dry(self):
        with tempfile.TemporaryDirectory() as tmp:
            ops = create_page_ops_dry("analytics", tmp)
            self.assertEqual(len(ops), 1)
            self.assertEqual(ops[0]["action"], "CREATE")
            self.assertIn("/analytics/AnalyticsDashboardPage.tsx", ops[0]["path"])


if __name__ == "__main__":
    unittest.main()
