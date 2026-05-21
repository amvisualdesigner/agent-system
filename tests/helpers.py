"""Shared test utilities for Constraint Graph integration tests.

Provides:
- FakeWorkspace: temp directory with controlled file structure
- Sample GraphIR builders: produce known GraphIR instances
"""

import os
import tempfile
import shutil
from dataclasses import dataclass, field
from typing import Any

from app.graphir.intent import Intent, IntentPlan
from app.graphir.pipeline import GraphIRPipeline
from app.graphir.models import GraphIR, GraphIRLayout


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


def build_sample_graph(kind: str = "kpi") -> tuple[GraphIR, GraphIRLayout]:
    """Build a sample GraphIR from an IntentPlan.

    Args:
        kind: "kpi" → KpiRow, "dashboard" → Page + KpiRow + Timeseries

    Returns:
        (GraphIR, GraphIRLayout) — same output as GraphIRPipeline.run()
    """
    if kind == "kpi":
        plan = IntentPlan(
            intents=[
                Intent(id="i1", capability="presentation.kpi_row"),
            ],
        )
    elif kind == "dashboard":
        plan = IntentPlan(
            intents=[
                Intent(id="i1", capability="layout.page"),
                Intent(id="i2", capability="presentation.kpi_row"),
                Intent(id="i3", capability="presentation.timeseries"),
            ],
        )
    elif kind == "table":
        plan = IntentPlan(
            intents=[
                Intent(id="i1", capability="layout.page"),
                Intent(id="i2", capability="presentation.table"),
            ],
        )
    else:
        raise ValueError(f"Unknown sample kind: {kind}")

    return GraphIRPipeline.run(plan)
