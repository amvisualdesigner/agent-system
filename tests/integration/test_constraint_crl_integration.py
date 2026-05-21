"""ConflictResolutionLayer — integration tests.

Validates the end-to-end CRL flow:
  RepositorySemanticMemory.save() → CRL.resolve() → IdentityResolver

Requires: REPO_ROOT env var.
"""

from app.graphir.constraint import (
    ExecutionContext,
    Decision,
)
from app.graphir.constraint.indexer import RepositoryIndexer
from app.graphir.constraint.matcher import IntentFileMatcher
from app.graphir.constraint.resolver import IdentityResolver
from app.graphir.constraint.memory import RepositorySemanticMemory
from app.graphir.constraint.crl import ConflictResolutionLayer

from tests.helpers import FakeWorkspace, build_sample_graph


# Fingerprint for presentation.kpi_row with domain=(presentation,) and
# empty params: sha256("{}")[:12] = 44136fa355b3
KPI_ROW_FP = "presentation.kpi_row:presentation:44136fa355b3"


class TestCRLStaleMappingIntegration:
    """Memory points to a file that no longer exists."""

    def test_stale_mapping_causes_fallback_to_scoring(self):
        """File deleted → CRL invalidates mapping → resolver uses Level 3."""
        graph, layout = build_sample_graph("kpi")

        # Write memory in a workspace
        with FakeWorkspace() as ws:
            ws.add_file("src/components/KpiRow.tsx",
                        "export const KpiRow = () => null;")
            ctx = ExecutionContext(run_id="crl-test", workspace_root=ws.root)

            memory = RepositorySemanticMemory(ctx.memory_path)
            memory.save({KPI_ROW_FP: "src/components/KpiRow.tsx"})

            # Read back while workspace still active
            raw = memory.load()
            assert KPI_ROW_FP in raw

        # Workspace is now gone — file and memory path no longer exist
        # CRL should still work with raw data captured before teardown
        with FakeWorkspace() as ws2:
            ctx2 = ExecutionContext(run_id="crl-test-2", workspace_root=ws2.root)
            indexer = RepositoryIndexer()
            fn2, cn2 = indexer.index(ws2.root)  # empty

            # CRL reconciles captured raw mapping against current file_nodes
            crl = ConflictResolutionLayer()
            cleaned, conflicts = crl.resolve(raw, fn2)

            assert KPI_ROW_FP not in cleaned
            assert len(conflicts) == 1
            assert "stale" in conflicts[0].conflict_type.value

            # Resolver uses cleaned mapping (no memory for this identity)
            resolver = IdentityResolver(resolved_mapping=cleaned)
            matcher = IntentFileMatcher()
            identities, candidates = matcher.match(graph, fn2)
            decisions = resolver.resolve(identities, candidates, fn2)

            for d in decisions.values():
                assert d.decision == Decision.CREATE

    def test_valid_mapping_survives_crl(self):
        """File exists → CRL keeps mapping → resolver uses Level 1."""
        graph, layout = build_sample_graph("kpi")

        with FakeWorkspace() as ws:
            ws.add_file("src/components/KpiRow.tsx",
                        "export const KpiRow = () => null;")
            ctx = ExecutionContext(run_id="crl-valid", workspace_root=ws.root)

            indexer = RepositoryIndexer()
            fn, cn = indexer.index(ws.root)

            # Write valid memory with CORRECT fingerprint
            memory = RepositorySemanticMemory(ctx.memory_path)
            memory.save({KPI_ROW_FP: "src/components/KpiRow.tsx"})

            raw = memory.load()

            # CRL — file exists, mapping should survive
            crl = ConflictResolutionLayer()
            cleaned, conflicts = crl.resolve(raw, fn)

            assert KPI_ROW_FP in cleaned
            assert len(conflicts) == 0

            # Resolver uses Level 1 (resolved mapping)
            resolver = IdentityResolver(resolved_mapping=cleaned)
            matcher = IntentFileMatcher()
            identities, candidates = matcher.match(graph, fn)
            decisions = resolver.resolve(identities, candidates, fn)

            d = list(decisions.values())[0]
            assert d.confidence == 1.0
            assert d.decision == Decision.UPDATE
            assert "Identity match via resolved mapping" in d.rationale
