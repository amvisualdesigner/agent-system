"""Tests for the shared test helpers.

Validates FakeWorkspace, make_sample_graph, and RepoFixture.
"""

import os

from tests.helpers import FakeWorkspace, make_sample_graph


class TestFakeWorkspace:
    def test_empty_on_enter(self):
        with FakeWorkspace() as ws:
            assert ws.root != ""
            assert ws.list_files() == []

    def test_add_file_creates_file(self):
        with FakeWorkspace() as ws:
            ws.add_file("src/test.tsx", "content")
            assert ws.exists("src/test.tsx")
            assert ws.read_file("src/test.tsx") == "content"

    def test_add_file_creates_dirs(self):
        with FakeWorkspace() as ws:
            ws.add_file("a/b/c/test.tsx")
            assert ws.exists("a/b/c/test.tsx")

    def test_add_multiple_files(self):
        with FakeWorkspace() as ws:
            ws.add_file("a.tsx")
            ws.add_file("b.tsx")
            ws.add_file("dir/c.tsx")
            files = ws.list_files()
            assert len(files) == 3
            assert "a.tsx" in files
            assert "b.tsx" in files
            assert "dir/c.tsx" in files

    def test_cleanup_on_exit(self):
        root = None
        with FakeWorkspace() as ws:
            root = ws.root
            ws.add_file("test.tsx")
            assert os.path.isdir(root)
        assert not os.path.exists(root)


class TestMakeSampleGraph:
    def test_kpi_returns_graph_and_layout(self):
        graph, layout = make_sample_graph("kpi")
        assert len(graph.nodes) > 0
        assert layout.root in graph.nodes

    def test_dashboard_has_multiple_nodes(self):
        graph, layout = make_sample_graph("dashboard")
        assert len(graph.nodes) >= 3  # Page + KpiRow + Timeseries

    def test_table_has_correct_types(self):
        graph, layout = make_sample_graph("table")
        types = [n.type for n in graph.nodes.values()]
        assert "Page" in types
        assert "AnalyticsTable" in types

    def test_graph_is_valid(self):
        from app.graphir.validator import GraphIRValidator
        graph, layout = make_sample_graph("dashboard")
        GraphIRValidator.validate(graph)  # should not raise
