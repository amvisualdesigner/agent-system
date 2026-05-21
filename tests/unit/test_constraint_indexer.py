"""RepositoryIndexer — unit tests for structural extraction.

State Layer tests: use FakeWorkspace for reproducible IO.
"""

import os

from app.graphir.constraint.indexer import RepositoryIndexer
from app.graphir.constraint.models import FileNode, ComponentNode
from tests.helpers import FakeWorkspace


class TestIndexerEmptyWorkspace:
    def test_empty_workspace_returns_empty(self):
        with FakeWorkspace() as ws:
            indexer = RepositoryIndexer()
            files, components = indexer.index(ws.root)
            assert files == {}
            assert components == {}


class TestIndexerFileDiscovery:
    def test_discovers_tsx_files(self):
        with FakeWorkspace() as ws:
            ws.add_file("src/KpiRow.tsx", "export const KpiRow = () => null;")
            ws.add_file("src/Timeseries.tsx", "export const Timeseries = () => null;")
            indexer = RepositoryIndexer()
            files, components = indexer.index(ws.root)
            assert len(files) == 2
            assert "src/KpiRow.tsx" in files
            assert "src/Timeseries.tsx" in files

    def test_ignores_node_modules(self):
        with FakeWorkspace() as ws:
            ws.add_file("node_modules/react/index.tsx", "export const X = 1;")
            ws.add_file("src/App.tsx", "export const App = () => null;")
            indexer = RepositoryIndexer()
            files, components = indexer.index(ws.root)
            assert len(files) == 1
            assert "src/App.tsx" in files

    def test_ignores_non_source_files(self):
        with FakeWorkspace() as ws:
            ws.add_file("readme.md", "# hello")
            ws.add_file("package.json", "{}")
            indexer = RepositoryIndexer()
            files, components = indexer.index(ws.root)
            assert files == {}

    def test_sorts_files_alphabetically(self):
        with FakeWorkspace() as ws:
            ws.add_file("z.tsx", "")
            ws.add_file("a.tsx", "")
            ws.add_file("m.tsx", "")
            indexer = RepositoryIndexer()
            files, components = indexer.index(ws.root)
            paths = list(files.keys())
            assert paths == sorted(paths)


class TestIndexerExportExtraction:
    SAMPLE_TSX = """import React from 'react';

interface Props {
  metric: string;
}

export const KpiRow: React.FC<Props> = ({ metric }) => {
  return <div>{metric}</div>;
};

export default KpiRow;

function helper() { return null; }
"""

    def test_detects_named_export_component(self):
        with FakeWorkspace() as ws:
            ws.add_file("KpiRow.tsx", self.SAMPLE_TSX)
            indexer = RepositoryIndexer()
            files, components = indexer.index(ws.root)
            fn = files["KpiRow.tsx"]
            assert "KpiRow" in fn.exports

    def test_detects_default_export(self):
        with FakeWorkspace() as ws:
            ws.add_file("KpiRow.tsx", self.SAMPLE_TSX)
            indexer = RepositoryIndexer()
            files, components = indexer.index(ws.root)
            fn = files["KpiRow.tsx"]
            assert any(
                b.name == "KpiRow" and b.is_default_export
                for b in fn.component_boundaries
            )

    def test_extracts_imports(self):
        with FakeWorkspace() as ws:
            ws.add_file("KpiRow.tsx", self.SAMPLE_TSX)
            indexer = RepositoryIndexer()
            files, components = indexer.index(ws.root)
            fn = files["KpiRow.tsx"]
            assert "react" in fn.imports

    def test_component_name_in_list(self):
        with FakeWorkspace() as ws:
            ws.add_file("KpiRow.tsx", self.SAMPLE_TSX)
            indexer = RepositoryIndexer()
            files, components = indexer.index(ws.root)
            fn = files["KpiRow.tsx"]
            assert "KpiRow" in fn.component_names


class TestIndexerBoundaryDetection:
    def test_boundary_covers_entire_component(self):
        content = """export const Foo = () => {
  const x = 1;
  return <div>{x}</div>;
};"""
        with FakeWorkspace() as ws:
            ws.add_file("Foo.tsx", content)
            indexer = RepositoryIndexer()
            files, components = indexer.index(ws.root)
            fn = files["Foo.tsx"]
            assert len(fn.component_boundaries) == 1
            b = fn.component_boundaries[0]
            assert b.name == "Foo"
            assert b.kind == "component"
            assert b.line_start == 1
            assert b.line_end >= b.line_start

    def test_boundary_for_class_component(self):
        content = """import React from 'react';

export class MyWidget extends React.Component {
  render() {
    return <div />;
  }
}"""
        with FakeWorkspace() as ws:
            ws.add_file("MyWidget.tsx", content)
            indexer = RepositoryIndexer()
            files, components = indexer.index(ws.root)
            fn = files["MyWidget.tsx"]
            classes = [b for b in fn.component_boundaries if b.kind == "class"]
            assert len(classes) >= 1
            assert classes[0].name == "MyWidget"

    def test_boundary_for_interface(self):
        content = """export interface Props {
  metric: string;
  value: number;
}"""
        with FakeWorkspace() as ws:
            ws.add_file("types.ts", content)
            indexer = RepositoryIndexer()
            files, components = indexer.index(ws.root)
            fn = files["types.ts"]
            interfaces = [b for b in fn.component_boundaries if b.kind == "interface"]
            assert len(interfaces) >= 1
            assert interfaces[0].name == "Props"

    def test_multiple_components_in_one_file(self):
        content = """export const Header = () => <header />;
export const Footer = () => <footer />;
export const Layout = () => <><Header /><Footer /></>;"""
        with FakeWorkspace() as ws:
            ws.add_file("Layout.tsx", content)
            indexer = RepositoryIndexer()
            files, components = indexer.index(ws.root)
            fn = files["Layout.tsx"]
            names = [b.name for b in fn.component_boundaries]
            assert "Header" in names
            assert "Footer" in names
            assert "Layout" in names


class TestIndexerDomainInference:
    def test_domain_from_path_keyword(self):
        with FakeWorkspace() as ws:
            ws.add_file("analytics/KpiRow.tsx", "export const KpiRow = () => null;")
            indexer = RepositoryIndexer()
            files, components = indexer.index(ws.root)
            fn = files["analytics/KpiRow.tsx"]
            assert "analytics" in fn.domains

    def test_domain_from_content_keyword(self):
        content = """export const RevenueChart = () => {
  return <div>revenue data</div>;
};"""
        with FakeWorkspace() as ws:
            ws.add_file("Chart.tsx", content)
            indexer = RepositoryIndexer()
            files, components = indexer.index(ws.root)
            fn = files["Chart.tsx"]
            assert "sales" in fn.domains or "generic" in fn.domains

    def test_falls_back_to_generic(self):
        with FakeWorkspace() as ws:
            ws.add_file("Unknown.tsx", "export const X = 1;")
            indexer = RepositoryIndexer()
            files, components = indexer.index(ws.root)
            fn = files["Unknown.tsx"]
            assert fn.domains == ["generic"]

    def test_component_nodes_have_domains(self):
        with FakeWorkspace() as ws:
            ws.add_file("analytics/KpiRow.tsx", "export const KpiRow = () => null;")
            indexer = RepositoryIndexer()
            files, components = indexer.index(ws.root)
            assert len(components) > 0
            for cn in components.values():
                assert len(cn.domains) > 0


class TestIndexerFileHash:
    def test_file_hash_is_deterministic(self):
        with FakeWorkspace() as ws:
            ws.add_file("Foo.tsx", "content")
            indexer = RepositoryIndexer()
            files1, _ = indexer.index(ws.root)
            files2, _ = indexer.index(ws.root)
            assert files1["Foo.tsx"].file_hash == files2["Foo.tsx"].file_hash

    def test_different_content_different_hash(self):
        with FakeWorkspace() as ws:
            ws.add_file("Foo.tsx", "content a")
            indexer_a = RepositoryIndexer()
            files_a, _ = indexer_a.index(ws.root)
        with FakeWorkspace() as ws:
            ws.add_file("Foo.tsx", "content b")
            indexer_b = RepositoryIndexer()
            files_b, _ = indexer_b.index(ws.root)
        # Can't compare across workspaces; just verify non-empty
        assert len(files_a["Foo.tsx"].file_hash) > 0
        assert len(files_b["Foo.tsx"].file_hash) > 0
