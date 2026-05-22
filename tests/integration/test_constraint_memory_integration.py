"""RepositorySemanticMemory — integration tests.

Validates the end-to-end flow:
  RepositoryIndexer → IntentFileMatcher → IdentityResolver →
  RepositorySemanticMemory.load → IdentityResolver(resolved_mapping=...) →
  Level 1 resolved_mapping override

Requires: REPO_ROOT env var.
"""

import json
import os

from app.graphir.backends import BackendConfig
from app.graphir.constraint import (
    ExecutionContext,
    Decision,
)
from app.graphir.constraint.models import MemoryRecord
from app.graphir.constraint.indexer import RepositoryIndexer
from app.graphir.constraint.matcher import IntentFileMatcher
from app.graphir.constraint.resolver import IdentityResolver
from app.graphir.constraint.memory import RepositorySemanticMemory

from tests.helpers import FakeWorkspace, build_sample_graph


class TestMemoryPersistence:
    """Memory saves and loads correctly across runs."""

    KPI_TSX = """export const KpiRow = () => null;"""

    def test_save_and_reload_mapping(self):
        """After first run, memory is written → second run uses mapping."""
        graph, layout = build_sample_graph("kpi")

        with FakeWorkspace() as ws:
            ws.add_file("src/components/KpiRow.tsx", self.KPI_TSX)

            ctx = ExecutionContext(run_id="mem-test", workspace_root=ws.root)
            config = BackendConfig()

            memory_path = ctx.memory_path

            # ── First run: no memory → scoring fallback → UPDATE ──
            indexer = RepositoryIndexer()
            fn, cn = indexer.index(ws.root)

            matcher = IntentFileMatcher()
            memory = RepositorySemanticMemory(memory_path)
            resolved_mapping = memory.load()

            resolver = IdentityResolver(resolved_mapping=resolved_mapping)
            identities, candidates = matcher.match(graph, fn)
            decisions = resolver.resolve(identities, candidates, fn)

            # Persist
            updated = memory.merge(decisions, identities, resolved_mapping)
            memory.save(updated)

            # Verify file exists
            assert os.path.exists(memory_path)
            with open(memory_path) as f:
                saved = json.load(f)
            assert len(saved) == 1

            fingerprint = list(saved.keys())[0]
            # Phase 6a format: dict with file_path and component_name
            assert "file_path" in saved[fingerprint]
            assert "KpiRow" in saved[fingerprint]["file_path"]

            # ── Second run: load memory → Level 1 match ──
            matcher2 = IntentFileMatcher()
            memory2 = RepositorySemanticMemory(memory_path)
            resolved_mapping2 = memory2.load()

            resolver2 = IdentityResolver(resolved_mapping=resolved_mapping2)
            identities2, candidates2 = matcher2.match(graph, fn)
            decisions2 = resolver2.resolve(identities2, candidates2, fn)

            # Level 1 match has confidence 1.0
            d = list(decisions2.values())[0]
            assert d.confidence == 1.0
            assert d.decision == Decision.UPDATE
            assert "Identity match via resolved mapping" in d.rationale

    def test_memory_does_not_affect_new_intents(self):
        """Memory only maps known fingerprints; new intents still CREATE."""
        with FakeWorkspace() as ws:
            ctx = ExecutionContext(run_id="mem-test-2", workspace_root=ws.root)

            # Pre-write memory with a mapping for a DIFFERENT capability
            memory = RepositorySemanticMemory(ctx.memory_path)
            memory.save({
                "other.fingerprint:generic:abc": MemoryRecord(
                    fingerprint="other.fingerprint:generic:abc",
                    file_path="src/Existing.tsx",
                    component_name="Other",
                ),
            })

            # Now run a KpiRow intent (different fingerprint)
            graph, layout = build_sample_graph("kpi")
            indexer = RepositoryIndexer()
            fn, cn = indexer.index(ws.root)

            matcher = IntentFileMatcher()
            resolved = memory.load()
            resolver = IdentityResolver(resolved_mapping=resolved)
            identities, candidates = matcher.match(graph, fn)
            decisions = resolver.resolve(identities, candidates, fn)

            # KpiRow has no memory entry → CREATE (greenfield)
            for d in decisions.values():
                assert d.decision == Decision.CREATE, \
                    f"Expected CREATE for new intent, got {d.decision}"
