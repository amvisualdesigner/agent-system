"""Shared test utilities for Constraint Graph integration tests.

Provides:
- FakeWorkspace: simple temp directory
- RepoFixture: git-initialized repo for full mutation tests
- make_sample_graph: canonical GraphIR builder via GraphIRDraft
- semantic builders: make_nested_layout, make_dashboard, make_tabbed_layout,
  make_deeply_nested
"""

import json
import os
import subprocess
import tempfile
import shutil
from dataclasses import dataclass, field
from typing import Any

from app.graphir.models import (
    GraphIR, GraphIRLayout, GraphIRDraft,
    GraphIRNode, GraphIREdge, EdgeRole,
)
from app.graphir.layout import LayoutDerivationEngine


@dataclass
class FakeWorkspace:
    """A temporary workspace with a known file structure.

    Creates a temp directory on enter, destroys on exit.
    Provides methods to add files, read content, and verify state.

    Usage:
        with FakeWorkspace() as ws:
            ws.add_file("src/KpiRow.tsx", "export const KpiRow = ...")
            assert ws.exists("src/KpiRow.tsx")
    """

    root: str = ""
    _tmpdir: Any = None

    def __enter__(self):
        self._tmpdir = tempfile.mkdtemp(prefix="cg_test_")
        self.root = self._tmpdir
        return self

    def __exit__(self, *args):
        if self._tmpdir:
            shutil.rmtree(self._tmpdir)

    def add_file(self, rel_path: str, content: str = "") -> str:
        """Create a file at rel_path within the workspace. Creates dirs."""
        full_path = os.path.join(self.root, rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w") as f:
            f.write(content)
        return rel_path

    def read_file(self, rel_path: str) -> str:
        with open(os.path.join(self.root, rel_path)) as f:
            return f.read()

    def exists(self, rel_path: str) -> bool:
        return os.path.isfile(os.path.join(self.root, rel_path))

    def list_files(self) -> list[str]:
        result = []
        for dirpath, _, filenames in os.walk(self.root):
            for fn in filenames:
                rel = os.path.relpath(os.path.join(dirpath, fn), self.root)
                result.append(rel)
        return sorted(result)


def make_sample_graph(kind: str = "kpi") -> tuple[GraphIR, GraphIRLayout]:
    """Build a sample GraphIR directly via GraphIRDraft.

    This is the canonical way to build GraphIR for tests.
    Does NOT use GraphIRPipeline (which only accepts StructuralIR).

    Args:
        kind: "kpi" → KpiRow, "dashboard" → Page + KpiRow + Timeseries,
              "table" → Page + AnalyticsTable

    Returns:
        (GraphIR, GraphIRLayout) — frozen graph + auto-derived layout.
    """
    if kind == "kpi":
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(
            id="n1", type="KpiRow",
            data={"metric": "revenue", "period": "30d"},
        ))
    elif kind == "dashboard":
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(id="page", type="Page",
                                   data={"title": "Dashboard"}))
        draft.add_node(GraphIRNode(id="kpi", type="KpiRow",
                                   data={"metric": "revenue"}))
        draft.add_node(GraphIRNode(id="ts", type="Timeseries",
                                   data={"metric": "revenue",
                                          "interval": "daily"}))
        draft.add_edge(GraphIREdge(source="page", target="kpi",
                                    role=EdgeRole.CONTAINS))
        draft.add_edge(GraphIREdge(source="page", target="ts",
                                    role=EdgeRole.CONTAINS))
    elif kind == "table":
        draft = GraphIRDraft()
        draft.add_node(GraphIRNode(id="page", type="Page",
                                   data={"title": "Data View"}))
        draft.add_node(GraphIRNode(id="table", type="AnalyticsTable",
                                   data={"columns": ["name", "value"]}))
        draft.add_edge(GraphIREdge(source="page", target="table",
                                    role=EdgeRole.CONTAINS))
    else:
        raise ValueError(f"Unknown sample kind: {kind}")

    graph = draft.freeze()
    layout = LayoutDerivationEngine.derive(graph)
    return graph, layout


# ═══════════════════════════════════════════════════════════════
# RepoFixture — git-aware workspace for full mutation tests
# ═══════════════════════════════════════════════════════════════


@dataclass
class RepoFixture:
    """Mini-repositorio Git reproducible para tests de mutación.

    Crea:
      - git init con user config
      - directorio .opencode/ opcional
      - archivos fuente con componentes reales

    Uso:
        with RepoFixture() as repo:
            repo.add_component("src/KpiRow.tsx", "KpiRow", kind="component")
            repo.add_memory({"fp1": {"file_path": "src/KpiRow.tsx"}})
            repo.git_commit("initial state")
            assert repo.exists("src/KpiRow.tsx")
    """

    root: str = ""
    _tmpdir: Any = None

    def __enter__(self):
        self._tmpdir = tempfile.mkdtemp(prefix="repo_fixture_")
        self.root = self._tmpdir
        self._init_git()
        return self

    def __exit__(self, *args):
        if self._tmpdir:
            shutil.rmtree(self._tmpdir)

    def _init_git(self):
        subprocess.run(["git", "init"], cwd=self.root,
                       capture_output=True, check=False)
        subprocess.run(["git", "config", "user.email", "test@test.com"],
                       cwd=self.root, capture_output=True, check=False)
        subprocess.run(["git", "config", "user.name", "Test"],
                       cwd=self.root, capture_output=True, check=False)

    def add_component(
        self, rel_path: str, name: str,
        kind: str = "component",
    ) -> str:
        """Create a TSX file with a named exported component."""
        full_path = os.path.join(self.root, rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        content = self._render_component(name, kind)
        with open(full_path, "w") as f:
            f.write(content)
        return rel_path

    def add_memory(self, records: dict) -> str:
        """Create .opencode/semantic_memory.json."""
        memory_dir = os.path.join(self.root, ".opencode")
        os.makedirs(memory_dir, exist_ok=True)
        path = os.path.join(memory_dir, "semantic_memory.json")
        with open(path, "w") as f:
            json.dump(records, f)
        return path

    def git_commit(self, message: str = "test state"):
        subprocess.run(["git", "add", "-A"], cwd=self.root,
                       capture_output=True, check=False)
        subprocess.run(["git", "commit", "-m", message],
                       cwd=self.root, capture_output=True, check=False)

    def read_file(self, rel_path: str) -> str:
        with open(os.path.join(self.root, rel_path)) as f:
            return f.read()

    def exists(self, rel_path: str) -> bool:
        return os.path.isfile(os.path.join(self.root, rel_path))

    def list_files(self) -> list[str]:
        result = []
        for dirpath, _, filenames in os.walk(self.root):
            for fn in filenames:
                rel = os.path.relpath(os.path.join(dirpath, fn), self.root)
                result.append(rel)
        return sorted(result)

    @staticmethod
    def _render_component(name: str, kind: str) -> str:
        if kind == "component":
            return (
                f"import React from 'react';\n\n"
                f"export const {name}: React.FC = () => {{\n"
                f"  return <div>{{{name}}}</div>;\n"
                f"}};\n"
            )
        if kind == "page":
            return (
                f"import React from 'react';\n\n"
                f"export const {name}: React.FC = () => {{\n"
                f"  return <div className=\"page\">{{{name}}}</div>;\n"
                f"}};\n"
            )
        if kind == "interface":
            return f"export interface {name} {{\n  // TODO\n}}\n"
        return f"export const {name} = () => {{}};\n"


# ═══════════════════════════════════════════════════════════════
# Semantic builders — 100% pure GraphIR via GraphIRDraft
# These builders NEVER reference filesystem, paths, or IO.
# ═══════════════════════════════════════════════════════════════


def make_nested_layout() -> tuple[GraphIR, GraphIRLayout]:
    """Page → Section → KpiRow ×2 + Timeseries"""
    draft = GraphIRDraft()
    draft.add_node(GraphIRNode(id="page", type="Page",
                               data={"title": "Dashboard"}))
    draft.add_node(GraphIRNode(id="section", type="Section",
                               data={"label": "Metrics"}))
    draft.add_node(GraphIRNode(id="kpi1", type="KpiRow",
                               data={"metric": "revenue"}))
    draft.add_node(GraphIRNode(id="kpi2", type="KpiRow",
                               data={"metric": "users"}))
    draft.add_node(GraphIRNode(id="ts", type="Timeseries",
                               data={"metric": "revenue"}))
    draft.add_edge(GraphIREdge(source="page", target="section",
                                role=EdgeRole.CONTAINS))
    draft.add_edge(GraphIREdge(source="section", target="kpi1",
                                role=EdgeRole.PRIMARY))
    draft.add_edge(GraphIREdge(source="section", target="kpi2",
                                role=EdgeRole.SUPPORTING))
    draft.add_edge(GraphIREdge(source="page", target="ts",
                                role=EdgeRole.SUPPORTING))
    graph = draft.freeze()
    layout = LayoutDerivationEngine.derive(graph)
    return graph, layout


def make_dashboard() -> tuple[GraphIR, GraphIRLayout]:
    """Page → KpiRow + Timeseries + AnalyticsTable"""
    draft = GraphIRDraft()
    draft.add_node(GraphIRNode(id="page", type="Page",
                               data={"title": "Dashboard"}))
    draft.add_node(GraphIRNode(id="kpi", type="KpiRow",
                               data={"metric": "revenue",
                                      "period": "30d"}))
    draft.add_node(GraphIRNode(id="ts", type="Timeseries",
                               data={"metric": "revenue",
                                      "interval": "daily"}))
    draft.add_node(GraphIRNode(id="table", type="AnalyticsTable",
                               data={"columns": ["name", "value",
                                                  "trend"]}))
    draft.add_edge(GraphIREdge(source="page", target="kpi",
                                role=EdgeRole.PRIMARY))
    draft.add_edge(GraphIREdge(source="page", target="ts",
                                role=EdgeRole.SUPPORTING))
    draft.add_edge(GraphIREdge(source="page", target="table",
                                role=EdgeRole.SUPPORTING))
    graph = draft.freeze()
    layout = LayoutDerivationEngine.derive(graph)
    return graph, layout


def make_tabbed_layout() -> tuple[GraphIR, GraphIRLayout]:
    """Page → TabContainer → Tab[Overview, Details]"""
    draft = GraphIRDraft()
    draft.add_node(GraphIRNode(id="page", type="Page",
                               data={"title": "Analytics"}))
    draft.add_node(GraphIRNode(id="tabs", type="TabContainer",
                               data={"tabs": ["Overview", "Details"]}))
    draft.add_node(GraphIRNode(id="overview", type="Section",
                               data={"label": "Overview"}))
    draft.add_node(GraphIRNode(id="details", type="Section",
                               data={"label": "Details"}))
    draft.add_node(GraphIRNode(id="kpi", type="KpiRow",
                               data={"metric": "revenue"}))
    draft.add_node(GraphIRNode(id="ts", type="Timeseries",
                               data={"metric": "revenue"}))
    draft.add_edge(GraphIREdge(source="page", target="tabs",
                                role=EdgeRole.CONTAINS))
    draft.add_edge(GraphIREdge(source="tabs", target="overview",
                                role=EdgeRole.PRIMARY))
    draft.add_edge(GraphIREdge(source="tabs", target="details",
                                role=EdgeRole.SUPPORTING))
    draft.add_edge(GraphIREdge(source="overview", target="kpi",
                                role=EdgeRole.CONTAINS))
    draft.add_edge(GraphIREdge(source="overview", target="ts",
                                role=EdgeRole.CONTAINS))
    graph = draft.freeze()
    layout = LayoutDerivationEngine.derive(graph)
    return graph, layout


def make_deeply_nested() -> tuple[GraphIR, GraphIRLayout]:
    """Page → Section → Group → KpiRow"""
    draft = GraphIRDraft()
    draft.add_node(GraphIRNode(id="page", type="Page",
                               data={"title": "Deep"}))
    draft.add_node(GraphIRNode(id="section", type="Section",
                               data={"label": "Analytics"}))
    draft.add_node(GraphIRNode(id="group", type="Group",
                               data={"label": "Primary Metrics"}))
    draft.add_node(GraphIRNode(id="kpi", type="KpiRow",
                               data={"metric": "revenue"}))
    draft.add_edge(GraphIREdge(source="page", target="section",
                                role=EdgeRole.CONTAINS))
    draft.add_edge(GraphIREdge(source="section", target="group",
                                role=EdgeRole.CONTAINS))
    draft.add_edge(GraphIREdge(source="group", target="kpi",
                                role=EdgeRole.PRIMARY))
    graph = draft.freeze()
    layout = LayoutDerivationEngine.derive(graph)
    return graph, layout
