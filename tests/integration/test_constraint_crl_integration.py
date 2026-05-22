"""ConflictResolutionLayer — integration tests.

Validates the end-to-end CRL flow:
  RepositorySemanticMemory.save() → CRL.resolve() → IdentityResolver

Requires: REPO_ROOT env var.
"""

from app.graphir.constraint import (
    ExecutionContext,
    Decision,
)
from app.graphir.constraint.models import MemoryRecord
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
            memory.save({
                KPI_ROW_FP: MemoryRecord(
                    fingerprint=KPI_ROW_FP,
                    file_path="src/components/KpiRow.tsx",
                    component_name="KpiRow",
                ),
            })

            # Read back while workspace still active
            raw_memory = memory.load()
            assert KPI_ROW_FP in raw_memory

        # Workspace is now gone — file and memory path no longer exist
        # CRL should still work with raw data captured before teardown
        with FakeWorkspace() as ws2:
            ctx2 = ExecutionContext(run_id="crl-test-2", workspace_root=ws2.root)
            indexer = RepositoryIndexer()
            fn2, cn2 = indexer.index(ws2.root)  # empty

            # CRL operates on dict[str, str]; adapt MemoryRecord → file_path
            crl_input = {fp: rec.file_path for fp, rec in raw_memory.items()}
            crl = ConflictResolutionLayer()
            cleaned_paths, conflicts = crl.resolve(crl_input, fn2)

            # Rebuild MemoryRecord dict (need component_name from original)
            cleaned = {}
            for fp, file_path in cleaned_paths.items():
                rec = raw_memory.get(fp)
                if rec is not None:
                    cleaned[fp] = MemoryRecord(
                        fingerprint=fp,
                        file_path=file_path,
                        component_name=rec.component_name,
                    )

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
            memory.save({
                KPI_ROW_FP: MemoryRecord(
                    fingerprint=KPI_ROW_FP,
                    file_path="src/components/KpiRow.tsx",
                    component_name="KpiRow",
                ),
            })

            raw_memory = memory.load()

            # CRL operates on dict[str, str]; adapt
            crl_input = {fp: rec.file_path for fp, rec in raw_memory.items()}
            crl = ConflictResolutionLayer()
            cleaned_paths, conflicts = crl.resolve(crl_input, fn)

            # Rebuild MemoryRecord
            cleaned = {}
            for fp, file_path in cleaned_paths.items():
                rec = raw_memory.get(fp)
                if rec is not None:
                    cleaned[fp] = MemoryRecord(
                        fingerprint=fp,
                        file_path=file_path,
                        component_name=rec.component_name,
                    )

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
