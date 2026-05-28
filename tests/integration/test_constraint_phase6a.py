"""Phase 6a — integration tests.

Validates:
  1. Existing component → UPDATE, not duplicate CREATE (Level 2.5)
  2. Component removed from plan → DELETE generated (state diff)
  3. DELETE in multi-component file → boundary-only removal
  4. DELETE in single-component file → delete_file
  5. DELETE cleans memory mapping
  6. SPLIT does NOT trigger DELETE on extracted components
  7. Shadow compare detects decision mismatches
"""

import json
import os

from app.graphir.backends import BackendConfig
from app.graphir.constraint import (
    ExecutionContext, Decision,
)
from app.graphir.constraint.models import MemoryRecord, DeletionRecord
from app.graphir.constraint.indexer import RepositoryIndexer
from app.graphir.constraint.matcher import IntentFileMatcher
from app.graphir.constraint.resolver import IdentityResolver
from app.graphir.constraint.renderer import RepositoryAwareRenderer
from app.graphir.constraint.memory import RepositorySemanticMemory
from app.graphir.constraint.deletion import detect_deletions
from app.graphir.constraint.split_analyzer import SPLITAnalyzer

from tests.helpers import FakeWorkspace, make_sample_graph


class TestExistenceAwareResolver:
    """Level 2.5: existing components get UPDATE, not duplicate CREATE."""

    # A pre-existing file with KpiRow component
    KPI_TSX = """import React from 'react';

export const KpiRow = () => null;
"""

    def test_existing_component_gets_update_not_create(self):
        """Component exists in file_node.component_names → UPDATE via Level 2.5."""
        graph, layout = make_sample_graph("kpi")

        with FakeWorkspace() as ws:
            ws.add_file("src/components/KpiRow.tsx", self.KPI_TSX)
            ctx = ExecutionContext(run_id="l25-test", workspace_root=ws.root)
            config = BackendConfig()

            indexer = RepositoryIndexer()
            fn, cn = indexer.index(ws.root)

            matcher = IntentFileMatcher()
            resolver = IdentityResolver()
            identities, candidates = matcher.match(graph, fn)

            # Even with NO memory (empty resolved_mapping), Level 2.5
            # should match because KpiRow is in component_names.
            decisions = resolver.resolve(identities, candidates, fn)

            for d in decisions.values():
                assert d.decision == Decision.UPDATE, \
                    f"Expected UPDATE, got {d.decision}"
                assert d.confidence == 0.85, \
                    f"Expected Level 2.5 confidence 0.85, got {d.confidence}"
                assert "Level 2.5" in d.rationale

    def test_new_component_still_creates(self):
        """Component NOT in any file_node → CREATE (greenfield)."""
        graph, layout = make_sample_graph("kpi")

        with FakeWorkspace() as ws:
            # Empty workspace — no files
            ctx = ExecutionContext(run_id="l25-create", workspace_root=ws.root)

            indexer = RepositoryIndexer()
            fn, cn = indexer.index(ws.root)

            matcher = IntentFileMatcher()
            resolver = IdentityResolver()
            identities, candidates = matcher.match(graph, fn)
            decisions = resolver.resolve(identities, candidates, fn)

            for d in decisions.values():
                assert d.decision == Decision.CREATE, \
                    f"Expected CREATE for greenfield, got {d.decision}"





