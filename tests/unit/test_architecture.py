"""Architecture lock tests — prevent silent regression of refactored invariants.

These tests enforce the semantic boundary established post-audit (2026-05-28):

  GraphIR plane (renderer, generator, diff, models) — PURE, no IO.
  Repository plane (apply_engine, indexer, memory) — IO permitted.
  Writer: FileOpApplier (executor.py) — SOLE mutation authority to workspace.
"""

from pathlib import Path
import inspect
import re

import pytest


class TestRendererPurity:
    """Renderer must NEVER access filesystem. Content arrives via parameters."""

    def test_no_filesystem_access_in_renderer(self):
        from app.graphir.constraint.renderer import RepositoryAwareRenderer

        source = inspect.getsource(RepositoryAwareRenderer)
        forbidden = [
            "open(", "os.remove(", "os.replace(", "os.unlink(",
            "tempfile.", "shutil.",
        ]
        for token in forbidden:
            assert token not in source, (
                f"Renderer must not access filesystem (found {token!r}). "
                "All file content arrives via existing_content_by_path."
            )

    def test_renderer_os_usage_is_path_only(self):
        """Renderer uses os only for path computation, never filesystem IO."""
        from app.graphir.constraint.renderer import RepositoryAwareRenderer

        source = inspect.getsource(RepositoryAwareRenderer)
        suspicious = ["os.getcwd", "os.listdir", "os.scandir", "os.walk",
                       "os.stat", "os.path.exists", "os.path.isfile",
                       "os.path.isdir", "os.path.getsize",
                       "os.remove", "os.replace", "os.unlink", "os.rename",
                       "os.makedirs", "os.mkdir", "os.rmdir",
                       "os.chdir", "os.chmod",
                       "os.fdopen", "os.open", "os.read", "os.write"]
        for s in suspicious:
            assert s not in source, (
                f"Renderer uses {s!r} — it must be removed. "
                "Allowed: os.path.dirname, os.path.splitext, os.path.relpath"
            )

    def test_no_fallback_to_filesystem(self):
        """Renderer.render() does not have a disk fallback path for content."""
        from app.graphir.constraint.renderer import RepositoryAwareRenderer

        source = inspect.getsource(RepositoryAwareRenderer)
        assert "existing_content_by_path" in source
        assert "existing_content_by_path or {}" in source
        assert ".get(decision.target_file, \"\")" in source


class TestSingleWriter:
    """FileOpApplier is the SOLE mutation authority to workspace files."""

    CONSTRAINTS_DIR = Path(__file__).resolve().parent.parent.parent / "backend" / "app" / "graphir" / "constraint"

    WRITE_MARKERS = [
        "os.remove(", "os.replace(", "os.unlink(", "os.rename(",
        "tempfile.mkstemp(", "tempfile.NamedTemporaryFile(",
        "shutil.rmtree(", "shutil.copy(", "shutil.move(",
    ]

    ALLOWED_WRITERS = {"executor.py", "memory.py"}

    @pytest.mark.parametrize("pyfile", sorted(
        p for p in CONSTRAINTS_DIR.glob("*.py") if p.name != "__init__.py"
    ), ids=lambda p: p.name)
    def test_only_executor_writes_to_disk(self, pyfile):
        if pyfile.name in self.ALLOWED_WRITERS:
            pytest.skip(f"{pyfile.name} is allowed — contains FileOpApplier or memory system")

        source = pyfile.read_text()
        for marker in self.WRITE_MARKERS:
            assert marker not in source, (
                f"{pyfile.name} contains {marker!r} but is not executor.py. "
                "Only FileOpApplier (in executor.py) may write to workspace."
            )

    def test_executor_has_both_write_methods(self):
        """FileOpApplier must implement _atomic_write (create/modify) and delete."""
        from app.graphir.constraint.executor import FileOpApplier

        source = inspect.getsource(FileOpApplier)
        assert "os.remove(" in source, "FileOpApplier must handle deletion"
        assert "_atomic_write" in source, "FileOpApplier must have atomic write"
        assert "os.replace(tmp_path, file_path)" in source, "Atomic rename required"


class TestGraphIREntrypoints:
    """GraphIR must be built via GraphIRDraft, not legacy pipelines."""

    def test_graphir_pipeline_run_raises(self):
        """GraphIRPipeline.run() must raise — it was removed."""
        from app.graphir.pipeline import GraphIRPipeline

        with pytest.raises(RuntimeError, match="does not exist"):
            GraphIRPipeline.run(None)

    def test_helpers_use_make_sample_graph(self):
        """Test helpers must not reference legacy build_sample_graph."""
        import tests.helpers as helpers

        source = inspect.getsource(helpers)
        assert "build_sample_graph" not in source, (
            "Legacy build_sample_graph must not be used. "
            "Use make_sample_graph with GraphIRDraft."
        )

    def test_semantic_builders_use_graphir_draft(self):
        """Semantic builders in test helpers must use GraphIRDraft."""
        import tests.helpers as helpers

        source = inspect.getsource(helpers)
        assert "GraphIRDraft()" in source, (
            "Semantic builders must construct GraphIR via GraphIRDraft."
        )


class TestIOLayerBoundaries:
    """Non-workspace IO (memory, artifacts, git) is legitimate but tagged."""

    def test_memory_system_uses_open(self):
        """Memory system writes to .opencode/semantic_memory.json — legitimate."""
        from app.graphir.constraint.memory import RepositorySemanticMemory
        source = inspect.getsource(RepositorySemanticMemory)
        assert "open(" in source, "Memory system must persist to .opencode/"
        assert "semantic_memory.json" in source

    def test_indexer_is_read_only(self):
        """Indexer reads files for parsing — legitimate repository-plane IO."""
        from app.graphir.constraint.indexer import RepositoryIndexer
        source = inspect.getsource(RepositoryIndexer)
        assert "open(" in source
        assert 'open(path, "r")' in source or "open(path) as f" in source
        for marker in ["os.remove(", "os.replace(", "tempfile."]:
            assert marker not in source, "Indexer must not write files"


class TestMemoryIsNotLifecycleAuthority:
    """F2 lock: Memory is history/evidence, never a lifecycle input.

    These are static (source-level) invariants enforcing the F2 cut:
    Memory.load()/resolved_mapping must not feed IdentityResolver.
    """

    def test_resolver_has_no_memory_channel(self):
        from app.graphir.constraint.resolver import IdentityResolver
        source = inspect.getsource(IdentityResolver)
        assert "resolved_mapping" not in source, (
            "IdentityResolver must have no memory channel. "
            "Memory is history/evidence only (F2)."
        )
        assert "IdentityResolver(" not in source, (
            "Faith: __init__ keyword helpers must not re-add memory injection."
        )

    def test_apply_engine_does_not_feed_memory_to_resolver(self):
        from app.engine import apply_engine
        source = inspect.getsource(apply_engine)
        m = re.search(r"IdentityResolver\s*\([^)]*\)", source)
        assert m, "IdentityResolver(...) construction must exist in pipeline"
        assert "resolved_mapping" not in m.group(0), (
            "resolved_mapping must not be wired into IdentityResolver. "
            "Memory is never a lifecycle input (F2)."
        )

    def test_memory_persistence_preserved_as_history(self):
        from app.engine import apply_engine
        source = inspect.getsource(apply_engine)
        assert "memory.load(" in source, "load() survives as evidence read"
        assert "memory.merge(" in source, "merge() survives for historical record"
        assert "memory.save(" in source, "save() survives for historical record"


class TestIdentityResolverIsolation:
    """F3 lock: IdentityResolver is a proposal/evidence kernel, not runtime authority.

    F3 does NOT eliminate the semantic authority: the decision → render_mode
    projection in apply_engine remains a KNOWN F1 residue. These locks bound
    the resolver's influence to that single, documented point and prove the
    resolver cannot observe or mutate the Confirmed Plan (StructuralIR.operations).
    """

    RESOLVER_PATH = Path(__file__).resolve().parent.parent.parent / "backend" / "app" / "graphir" / "constraint" / "resolver.py"

    def test_resolver_has_no_channel_to_confirmed_plan(self):
        source = self.RESOLVER_PATH.read_text()
        for token in ("structural_ir", "structural_completion", "complete_structure"):
            assert token not in source, (
                f"IdentityResolver must have no channel to the Confirmed Plan (found {token!r}). "
                "F3: resolver is evidence/proposal; the plan is never observed."
            )

        from app.graphir.constraint.resolver import IdentityResolver
        sig = inspect.signature(IdentityResolver.resolve)
        assert list(sig.parameters) == ["self", "identities", "candidates", "file_nodes"], (
            "resolve() must not accept the Confirmed Plan or StructuralIR (F3)."
        )

    def test_resolver_does_not_write_runtime_lifecycle_fields(self):
        from app.graphir.constraint.resolver import IdentityResolver
        source = inspect.getsource(IdentityResolver)
        for token in (".render_mode", "render_mode=", ".operations"):
            assert token not in source, (
                f"IdentityResolver must not write runtime lifecycle fields (found {token!r}). "
                "Any runtime effect goes through the documented render_mode residue (F1)."
            )

    def test_renderer_actions_keyed_on_render_mode_only(self):
        from app.graphir.constraint.renderer import RepositoryAwareRenderer
        source = inspect.getsource(RepositoryAwareRenderer)
        assert "decision.decision in" not in source, (
            "Renderer must not branch on decision.decision (F3). "
            "The only lifecycle signal it reads is render_mode (residue of F1)."
        )
        assert source.count("decision.decision") <= 1, (
            "decision.decision may appear in the renderer only as the diff-engine "
            "strategy pass-through; all gates must read render_mode."
        )
        assert "decision.render_mode" in source, (
            "FileOp.action must be sourced from decision.render_mode."
        )

    def test_render_mode_projection_is_sole_annotated_residue(self):
        from app.engine import apply_engine
        source = inspect.getsource(apply_engine)
        assert "RESIDUO F1" in source, (
            "The render_mode projection must be annotated as the known F1 residue."
        )
        assert source.count("dec.decision in") == 2, (
            "decision → render_mode is read ONLY in the residue projection loop "
            "(create/modify). No other lifecycle gate may read the resolver decision."
        )
        assert len(re.findall(r'dec\.render_mode\s*=\s*"\w+"', source)) == 2, (
            "render_mode is written only inside the residue projection (create/modify)."
        )
        assert 'dec.render_mode == "modify"' in source, (
            "existing_content snapshot must key on render_mode (F3), not decision.decision."
        )
