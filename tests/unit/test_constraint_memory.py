"""RepositorySemanticMemory — unit tests for persistence.

State Layer: isolated IO to a temporary file.
"""

import json
import os
import tempfile

from app.graphir.constraint.memory import RepositorySemanticMemory
from app.graphir.constraint.models import Decision, FileOpDecision, MemoryRecord
from app.graphir.constraint.identity import CanonicalIdentity


def _identity(fp_id="presentation.kpi:generic:abc") -> CanonicalIdentity:
    """Helper: identity whose fingerprint matches fp_id."""
    return CanonicalIdentity(
        component_name="KpiRow",
        capability_id="presentation.kpi",
        domain=("generic",),
        params_hash="abc",
    )


def _decision(decision=Decision.UPDATE, target="src/KpiRow.tsx",
              intent="presentation.kpi", node_id="n0") -> FileOpDecision:
    return FileOpDecision(
        intent_id=intent,
        graphir_node_id=node_id,
        decision=decision,
        target_file=target,
        confidence=0.9,
        rationale="test",
    )


class TestMemoryLoad:
    """Load from disk edge cases."""

    def test_load_missing_file_returns_empty(self):
        mem = RepositorySemanticMemory("/tmp/nonexistent/.opencode/memory.json")
        assert mem.load() == {}

    def test_load_empty_file_returns_empty(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            mem = RepositorySemanticMemory(path)
            assert mem.load() == {}
        finally:
            os.unlink(path)

    def test_load_valid_json(self):
        data = {"fp1": "src/A.tsx", "fp2": "src/B.tsx"}
        with tempfile.NamedTemporaryFile(
            suffix=".json", mode="w", delete=False,
        ) as f:
            json.dump(data, f)
            path = f.name
        try:
            mem = RepositorySemanticMemory(path)
            loaded = mem.load()
            assert len(loaded) == 2
            assert loaded["fp1"].file_path == "src/A.tsx"
            assert loaded["fp2"].file_path == "src/B.tsx"
            # Old format entries get empty component_name (migrated)
            assert loaded["fp1"].component_name == ""
        finally:
            os.unlink(path)

    def test_load_invalid_json_returns_empty(self):
        with tempfile.NamedTemporaryFile(
            suffix=".json", mode="w", delete=False,
        ) as f:
            f.write("not json")
            path = f.name
        try:
            mem = RepositorySemanticMemory(path)
            assert mem.load() == {}
        finally:
            os.unlink(path)

    def test_load_not_a_dict_returns_empty(self):
        with tempfile.NamedTemporaryFile(
            suffix=".json", mode="w", delete=False,
        ) as f:
            json.dump(["list", "not", "dict"], f)
            path = f.name
        try:
            mem = RepositorySemanticMemory(path)
            assert mem.load() == {}
        finally:
            os.unlink(path)


class TestMemorySave:
    """Save to disk edge cases."""

    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, ".opencode", "memory.json")
            mem = RepositorySemanticMemory(path)
            data = {
                "fp:a": MemoryRecord(fingerprint="fp:a", file_path="src/A.tsx", component_name="A"),
                "fp:b": MemoryRecord(fingerprint="fp:b", file_path="src/B.tsx", component_name="B"),
            }
            mem.save(data)

            assert os.path.exists(path)
            loaded = mem.load()
            assert loaded["fp:a"].file_path == "src/A.tsx"
            assert loaded["fp:a"].component_name == "A"
            assert loaded["fp:b"].file_path == "src/B.tsx"

    def test_save_empty_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, ".opencode", "memory.json")
            mem = RepositorySemanticMemory(path)
            mem.save({})

            assert os.path.exists(path)
            with open(path) as f:
                loaded = json.load(f)
            assert loaded == {}

    def test_save_creates_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a", "b", "memory.json")
            mem = RepositorySemanticMemory(path)
            mem.save({
                "k": MemoryRecord(fingerprint="k", file_path="src/K.tsx", component_name="K"),
            })
            assert os.path.exists(path)


class TestMemoryMerge:
    """Merge decisions + identities into existing mapping."""

    def test_merge_adds_update_mapping(self):
        ident = _identity()
        decisions = {"n0": _decision(Decision.UPDATE, "src/KpiRow.tsx")}
        identities = {"n0": ident}

        merged = RepositorySemanticMemory.merge(decisions, identities, {})
        fp = ident.fingerprint()
        assert merged[fp].file_path == "src/KpiRow.tsx"
        assert merged[fp].component_name == "KpiRow"

    def test_merge_adds_extend_mapping(self):
        ident = _identity()
        decisions = {"n0": _decision(Decision.EXTEND, "src/KpiRow.tsx")}
        identities = {"n0": ident}

        merged = RepositorySemanticMemory.merge(decisions, identities, {})
        fp = ident.fingerprint()
        assert merged[fp].file_path == "src/KpiRow.tsx"



    def test_merge_skips_split(self):
        ident = _identity()
        decisions = {"n0": _decision(Decision.SPLIT, "src/KpiRow.tsx")}
        identities = {"n0": ident}

        merged = RepositorySemanticMemory.merge(decisions, identities, {})
        assert merged == {}

    def test_merge_preserves_existing(self):
        ident = _identity()
        existing = {
            "other_fp": MemoryRecord(
                fingerprint="other_fp", file_path="src/Other.tsx", component_name="Other",
            ),
        }
        decisions = {"n0": _decision(Decision.UPDATE, "src/KpiRow.tsx")}
        identities = {"n0": ident}

        merged = RepositorySemanticMemory.merge(decisions, identities, existing)
        assert merged["other_fp"].file_path == "src/Other.tsx"
        fp = ident.fingerprint()
        assert merged[fp].file_path == "src/KpiRow.tsx"

    def test_merge_handles_missing_identity(self):
        decisions = {"n0": _decision()}
        merged = RepositorySemanticMemory.merge(decisions, {}, {})
        assert merged == {}

    def test_merge_handles_empty_inputs(self):
        existing = {
            "existing": MemoryRecord(
                fingerprint="existing", file_path="src/E.tsx", component_name="E",
            ),
        }
        merged = RepositorySemanticMemory.merge({}, {}, existing)
        assert merged["existing"].file_path == "src/E.tsx"

    def test_merge_overwrites_same_fingerprint(self):
        ident = _identity()
        fp = ident.fingerprint()
        existing = {
            fp: MemoryRecord(fingerprint=fp, file_path="src/Old.tsx", component_name="KpiRow"),
        }
        decisions = {"n0": _decision(Decision.UPDATE, "src/New.tsx")}
        identities = {"n0": ident}

        merged = RepositorySemanticMemory.merge(decisions, identities, existing)
        assert merged[fp].file_path == "src/New.tsx"
