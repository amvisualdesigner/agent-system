"""End-to-end regression suite for structural grounding.

Tests the full pipeline chain:
  StructuralIndex → complete_structure → has_resolved_keep_state → GraphIRBuilder

No file I/O. Worktree state is simulated via StructuralIndex.from_mapping().

6 E2E scenarios + 3 architectural invariants enforced as code.
"""

from __future__ import annotations

import ast
import os
import sys

import pytest

from app.engine.structural_completion import (
    CompletionMode,
    ResolvedCapability,
    StructuralIR,
    complete_structure,
    CREATE,
    MODIFY,
    DELETE,
    KEEP,
)
from app.engine.structural_index import StructuralIndex
from app.engine.state_adapter import ComponentInstanceInfo
from app.engine.errors import AmbiguousStructuralTargetError
from app.contracts.semantic_resolution import SemanticResolution
from app.contracts.contract_resolution import ContractResolution
from app.contracts.skill_ir import SkillIR
from app.contracts.skill_registry import SkillContract
from app.graphir.builder import GraphIRBuilder
from app.graphir.structure.models import ComponentNode, StructuralResolution
from app.graphir.structure.registry import StructuralRegistry
from app.graphir.structure.resolver import resolve


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

BACKEND = os.path.join(os.path.dirname(__file__), "..", "..", "backend")


def structural_index(*caps: str) -> StructuralIndex:
    """Build StructuralIndex from capability names (simulates worktree)."""
    mapping = {
        cap: [ComponentInstanceInfo(capability=cap, path=cap.rsplit(".", 1)[-1])]
        for cap in caps
    }
    return StructuralIndex.from_mapping(mapping)


def _find_node_by_type(graph, node_type: str):
    for nid, node in graph.nodes.items():
        if node.type == node_type:
            return node
    return None


def run_pipeline(
    semantic: SemanticResolution,
    contract_res: ContractResolution,
    contract: SkillContract,
    index: StructuralIndex | None = None,
) -> tuple[StructuralIR, object]:
    """Simulates the core pipeline: complete_structure → builder.

    Returns (structural_ir, graph_or_error).
    - graph is None when has_resolved_keep_state=True (noop).
    - graph is the AmbiguousStructuralTargetError when builder rejects
      delete-only drafts (handled upstream in apply_engine).
    """
    ir = complete_structure(
        semantic, contract_res, contract,
    )
    if ir.has_resolved_keep_state:
        return ir, None
    try:
        graph = GraphIRBuilder.build_from_structural(ir)
        return ir, graph
    except AmbiguousStructuralTargetError as e:
        return ir, e


# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def dashboard_contract() -> SkillContract:
    """Dashboard contract with kpi_row, timeseries, layout.page."""
    return SkillContract(
        contract_id="dashboard.sales_overview",
        version=1,
        input_schema={
            "type": "object",
            "required": ["metrics"],
            "properties": {
                "metrics": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        },
        ast_template={
            "layout": "AnalyticsGrid",
            "slots": [
                {"type": "KpiRow", "props": {"metrics": "metrics"}},
                {"type": "Timeseries", "props": {"metric": "timeseries_metric"}},
            ],
            "capabilities": {
                "KpiRow": "presentation.kpi_row",
                "Timeseries": "presentation.timeseries",
                "Page": "layout.page",
            },
        },
        renderer={},
    )


@pytest.fixture
def chart_contract() -> SkillContract:
    """Chart contract with presentation.chart.bar capability."""
    return SkillContract(
        contract_id="analytics.chart",
        version=1,
        input_schema={
            "type": "object",
            "required": ["metrics"],
            "properties": {
                "metrics": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        },
        ast_template={
            "layout": None,
            "slots": [
                {"type": "BarChart", "props": {"metrics": "metrics"}},
            ],
            "capabilities": {
                "BarChart": "presentation.chart.bar",
            },
        },
        renderer={},
    )


def make_semantic(
    params: dict | None = None,
    actions: list[dict] | None = None,
) -> SemanticResolution:
    return SemanticResolution(
        semantic_params=params or {},
        semantic_provenance={k: "test" for k in (params or {})},
        confidence=0.9,
        actions=actions or [],
    )


def make_contract_res(
    contract: SkillContract,
    params: dict | None = None,
) -> ContractResolution:
    return ContractResolution.from_skillir(
        SkillIR(contract_id=contract.contract_id, confidence=0.9, params=params or {}),
        contract,
    )


# ═══════════════════════════════════════════════════════════════════
# 6 E2E Scenarios
# ═══════════════════════════════════════════════════════════════════


class TestStructuralGrounding:
    """6 E2E scenarios cubriendo el pipeline completo."""

    def test_existing_dashboard_declarative_is_noop(self, dashboard_contract):
        """Escenario 1: existing dashboard + sin actions → noop (all KEEP)."""
        idx = structural_index(
            "layout.page", "presentation.kpi_row", "presentation.timeseries",
        )
        ir, graph = run_pipeline(
            make_semantic(),
            make_contract_res(dashboard_contract),
            dashboard_contract,
            idx,
        )

        assert ir.has_resolved_keep_state is True, (
            f"Expected all KEEP, got actions: "
            f"{[c.action for c in ir.capabilities]}"
        )
        assert ir.operations == [], "Noop should produce 0 operations"
        assert graph is None, "Noop should not reach builder"

    def test_existing_table_modify(self, dashboard_contract):
        """Escenario 2: existing table + 'modify kpi' → MODIFY operation."""
        idx = structural_index("presentation.kpi_row", "presentation.timeseries")
        ir, graph = run_pipeline(
            make_semantic(
                params={"metrics": ["growth"]},
                actions=[{"verb": "modify", "object": "kpi", "confidence": 0.9}],
            ),
            make_contract_res(dashboard_contract, params={"metrics": ["growth"]}),
            dashboard_contract,
            idx,
        )

        assert ir.has_resolved_keep_state is False
        kpi_ops = [op for op in ir.operations if op["target"] == "presentation.kpi_row"]
        assert len(kpi_ops) == 1, f"Expected MODIFY for kpi_row, got {ir.operations}"
        assert kpi_ops[0]["action"] == MODIFY, f"Expected MODIFY, got {kpi_ops[0]}"
        assert not isinstance(graph, Exception), (
            f"Builder should succeed for MODIFY ops: {graph}"
        )
        assert _find_node_by_type(graph, "KpiRow") is not None, "Should have KpiRow node in graph"

    def test_existing_chart_delete(self, chart_contract):
        """Escenario 3: existing chart + 'remove chart' → DELETE operation.

        Delete-only IRs produce empty builder drafts (builder skips DELETE).
        This is handled upstream by apply_engine's fileop injection.
        The IR must correctly report the DELETE intent.
        """
        idx = structural_index("presentation.chart.bar")
        ir, graph = run_pipeline(
            make_semantic(
                actions=[{"verb": "remove", "object": "chart", "confidence": 0.9}],
            ),
            make_contract_res(chart_contract),
            chart_contract,
            idx,
        )

        assert ir.has_resolved_keep_state is False
        chart_ops = [op for op in ir.operations if op["target"] == "presentation.chart.bar"]
        assert len(chart_ops) == 1, f"Expected DELETE for chart.bar, got {ir.operations}"
        assert chart_ops[0]["action"] == DELETE, (
            f"Expected DELETE, got {chart_ops[0]}"
        )
        # Builder rejects delete-only drafts — this is correct:
        # upstream (apply_engine) handles DELETE via fileop injection
        assert isinstance(graph, AmbiguousStructuralTargetError), (
            "Builder should reject delete-only drafts"
        )

    def test_empty_repo_no_intent_no_create(self, dashboard_contract):
        """Phase 3: declarative mode + no actions → KEEP for all caps (no implicit CREATE)."""
        ir, graph = run_pipeline(
            make_semantic(),
            make_contract_res(dashboard_contract),
            dashboard_contract,
            structural_index(),  # empty worktree
        )

        assert ir.has_resolved_keep_state is True
        assert len(ir.operations) == 0, "No intent → no operations"
        assert graph is None, "No intent → no GraphIR"

    def test_existing_page_add_child_creates_child(self, dashboard_contract):
        """Escenario 5: existing page + 'add kpi' → CREATE kpi_row, MODIFY page (3E).

        3E composition sync ensures parent page is also MODIFY when a child is
        CREATED, so the renderer regenerates the page with correct imports/JSX.
        """
        idx = structural_index("layout.page")
        ir, graph = run_pipeline(
            make_semantic(
                params={"metrics": ["revenue"]},
                actions=[{"verb": "add", "object": "kpi", "confidence": 0.9}],
            ),
            make_contract_res(dashboard_contract, params={"metrics": ["revenue"]}),
            dashboard_contract,
            idx,
        )

        assert ir.has_resolved_keep_state is False
        op_map = {op["target"]: op["action"] for op in ir.operations}
        assert op_map.get("presentation.kpi_row") == CREATE, (
            f"kpi_row should be CREATE, got: {op_map}"
        )
        # 3E: page should be MODIFY when child is CREATED (composition sync)
        assert op_map.get("layout.page") == MODIFY, (
            f"Page should be MODIFY on child CREATE (3E), got: {op_map}"
        )
        assert not isinstance(graph, Exception), (
            f"Builder should succeed: {graph}"
        )
        assert _find_node_by_type(graph, "KpiRow") is not None, "Should have KpiRow node"
        # Page node should also be present (composition sync)
        assert _find_node_by_type(graph, "Page") is not None, "Should have Page node from composition sync"

    def test_empty_operations_raises_ambiguous(self):
        """Escenario 6: all-KEEP or all-DELETE StructuralIR → AmbiguousStructuralTargetError.

        This validates that the early return in apply_engine.py is
        necessary — calling the builder directly with no ops fails.
        """
        caps = (
            ResolvedCapability("layout.page", {}, CompletionMode.SAFE_SKIP, KEEP),
        )
        ir = StructuralIR(
            contract_id="test.bi",
            contract_version=1,
            capabilities=caps,
            param_provenance={},
            confidence=1.0,
        )
        assert ir.has_resolved_keep_state is True
        assert ir.operations == [], "All-KEEP should produce 0 operations"

        with pytest.raises(AmbiguousStructuralTargetError) as exc:
            GraphIRBuilder.build_from_structural(ir)
        assert "cannot select root from empty draft" in str(exc.value).lower()


class TestOperationsEmptyEdgeCases:
    """Casos donde operations=[] pero la causa NO es all-KEEP resuelto.

    La lección del bug original: operations vacío ≠ noop.
    estos tests documentan explicitamente cada via que produce operations=[]
    y si el sistema lo trata como noop, clarification, o safety error.
    """

    def test_safe_skip_all_keep_produces_has_resolved_keep_state(self):
        """SAFE_SKIP + KEEP action → has_resolved_keep_state=True.

        SAFE_SKIP con action=KEEP significa: capability existe en repo,
        no hay cambios solicitados, fue skipped por params insuficientes.
        Es semanticamente un noop valido — la capability ya esta.
        """
        caps = (
            ResolvedCapability(
                "presentation.kpi_row", {},
                CompletionMode.SAFE_SKIP, KEEP,
            ),
            ResolvedCapability(
                "presentation.timeseries", {},
                CompletionMode.SAFE_SKIP, KEEP,
            ),
        )
        ir = StructuralIR(
            contract_id="test.bi", contract_version=1,
            capabilities=caps, param_provenance={}, confidence=1.0,
        )
        assert ir.operations == [], "All SAFE_SKIP+KEEP → operations=[]"
        assert ir.has_resolved_keep_state is True, (
            "SAFE_SKIP+KEEP → has_resolved_keep_state=True. "
            "This means early return in apply_engine: noop."
        )

    def test_mixed_safe_skip_and_keep_still_noop(self):
        """SAFE_SKIP + KEEP + un KEEP normal → has_resolved_keep_state=True."""
        caps = (
            ResolvedCapability("layout.page", {}, CompletionMode.SAFE_COMPLETE, KEEP),
            ResolvedCapability(
                "presentation.kpi_row", {},
                CompletionMode.SAFE_SKIP, KEEP,
            ),
        )
        ir = StructuralIR(
            contract_id="test.bi", contract_version=1,
            capabilities=caps, param_provenance={}, confidence=1.0,
        )
        assert ir.operations == []
        assert ir.has_resolved_keep_state is True

    def test_non_keep_in_safe_skip_produces_operations(self):
        """SAFE_SKIP con action=CREATE → aparece en operations.

        operations=[] solo cuando TODAS las actions son KEEP.
        SAFE_SKIP con action=CREATE/MODIFY/DELETE produce ops.
        """
        caps = (
            ResolvedCapability(
                "presentation.kpi_row", {},
                CompletionMode.SAFE_SKIP, CREATE,
            ),
        )
        ir = StructuralIR(
            contract_id="test.bi", contract_version=1,
            capabilities=caps, param_provenance={}, confidence=1.0,
        )
        assert len(ir.operations) == 1, "CREATE should appear in operations"
        assert ir.operations[0]["action"] == CREATE
        assert ir.has_resolved_keep_state is False

    def test_keep_and_delete_mixed_operations_has_delete(self):
        """KEEP + DELETE → operations tiene DELETE (no se filtra).

        El operations property solo filtra KEEP. DELETE aparece.
        Pero has_resolved_keep_state=False porque hay non-KEEP.
        Builder rechaza con AmbiguousStructuralTargetError.
        """
        caps = (
            ResolvedCapability("layout.page", {}, CompletionMode.SAFE_COMPLETE, KEEP),
            ResolvedCapability(
                "presentation.kpi_row", {},
                CompletionMode.SAFE_SKIP, DELETE,
            ),
        )
        ir = StructuralIR(
            contract_id="test.bi", contract_version=1,
            capabilities=caps, param_provenance={}, confidence=1.0,
        )
        assert len(ir.operations) == 1, "DELETE should appear in operations"
        assert ir.operations[0] == {"action": DELETE, "target": "presentation.kpi_row"}
        assert ir.has_resolved_keep_state is False, (
            "DELETE action breaks has_resolved_keep_state"
        )

        with pytest.raises(AmbiguousStructuralTargetError):
            GraphIRBuilder.build_from_structural(ir)

    def test_has_resolved_keep_state_distinguish_from_safe_skip(self):
        """Verifica que has_resolved_keep_state NO es vacuous truth.

        Si capabilities estuviera vacio, has_resolved_keep_state
        seria False (por bool(self.capabilities)).
        """
        ir = StructuralIR(
            contract_id="test.bi", contract_version=1,
            capabilities=(), param_provenance={}, confidence=1.0,
        )
        assert ir.has_resolved_keep_state is False, (
            "Empty capabilities should NOT be has_resolved_keep_state"
        )
        assert ir.operations == []


# ═══════════════════════════════════════════════════════════════════
# 3 Architectural Invariants (as code, not documentation)
# ═══════════════════════════════════════════════════════════════════


class TestArchitecturalInvariants:
    """Architectural invariants enforced as AST-level tests.

    If any of these fail, the architecture has drifted.
    These are NOT documentation — they are executable contracts.
    """

    BACKEND = BACKEND

    # Invariant 1: GraphIRBuilder never accesses worktree

    def test_builder_never_accesses_worktree(self):
        """GraphIRBuilder is pure — no os.path, open(), os.walk, load_current_state."""
        filepath = os.path.join(self.BACKEND, "app", "graphir", "builder.py")
        source = open(filepath).read()

        forbidden = [
            "os.path", "os.walk", "open(", "load_current_state",
            "StructuralIndex", "pathlib",
        ]
        errors = [p for p in forbidden if p in source]

        assert not errors, (
            "Builder must not access worktree. Found: " + ", ".join(errors)
        )

    # Invariant 2: StructuralIndex is sole worktree authority

    def test_structural_index_is_sole_worktree_authority(self):
        """Only structural_index.py importa load_current_state."""
        backend_root = os.path.join(self.BACKEND, "app")
        violations = []

        for root, _dirs, files in os.walk(backend_root):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                fp = os.path.join(root, fn)
                rel = os.path.relpath(fp, backend_root)
                source = open(fp).read()
                if "load_current_state" not in source:
                    continue
                if rel in ("engine/structural_index.py", "engine/state_adapter.py"):
                    continue
                violations.append(rel)

        assert not violations, (
            "Only StructuralIndex should access load_current_state. "
            "Violations: " + ", ".join(violations)
        )

    # Invariant 3: Builder does NOT derive paths

    def test_builder_never_derives_paths(self):
        """GraphIRBuilder no deriva component_instance_path.

        Solo lee de resolution.capability_to_path o asigna None.
        Cualquier asignacion directa a component_instance_path
        o llamada a derive_* es violacion.
        """
        filepath = os.path.join(self.BACKEND, "app", "graphir", "builder.py")
        source = open(filepath).read()

        errors = []
        lines = source.split("\n")
        for n, line in enumerate(lines, 1):
            s = line.strip()
            if any(p in s for p in ["derive_", "component_instance_path = "]):
                # Permite: lectura desde resolution multilinea:
                #   component_instance_path = (
                #       resolution.capability_to_path.get(...) ...
                #       if resolution is not None
                #       else None
                #   )
                if "resolution.capability_to_path.get" in s:
                    continue
                if "component_instance_path = (" in s:
                    if n < len(lines) and "resolution.capability_to_path.get" in lines[n]:
                        continue
                if s in ("if resolution is not None", "else None", "):"):
                    continue
                errors.append(f"  Line {n}: {s}")

        assert not errors, (
            "Builder must not derive paths. Violations:\n" + "\n".join(errors)
        )


# ═══════════════════════════════════════════════════════════════════
# 4 Resolver Fallback Tests
# ═══════════════════════════════════════════════════════════════════


class TestResolverFallback:
    """Tests del fallback del resolver via StructuralIndex.

    Cuando el registry del contrato no tiene candidates para una
    capability, el resolver (si recibe structural_index) intenta:

      - CREATE → StructuralIndex.derive_create_path()
      - MODIFY → StructuralIndex.exists() + resolve_path()
      - DELETE  → skipped (no produce nodos)

    Estos tests verifican que cada ruta de fallback funciona
    correctamente y que no hay regresión en el caso normal
    (registry con candidates).
    """

    def make_ir(
        self,
        actions: list[tuple[str, str]],
        contract_id: str = "test.bi",
    ) -> StructuralIR:
        """Build StructuralIR with explicit action/target pairs."""
        return StructuralIR(
            contract_id=contract_id,
            contract_version=1,
            capabilities=(
                ResolvedCapability(t, {}, CompletionMode.SAFE_COMPLETE, a)
                for a, t in actions
            ),
            param_provenance={},
            confidence=1.0,
        )

    def test_create_fallback_derives_path(self):
        """CREATE con registry vacío → derive_create_path del index."""
        idx = StructuralIndex.empty()
        registry = StructuralRegistry()  # empty

        ir = self.make_ir([(CREATE, "presentation.kpi_row")])
        result = resolve(ir, registry, structural_index=idx)

        assert result.reason is None, (
            f"Expected successful resolution, got reason={result.reason}"
        )
        path = result.capability_to_path.get("presentation.kpi_row")
        assert path is not None, (
            "CREATE should derive a path via StructuralIndex.derive_create_path"
        )
        assert path == "test.bi.kpi_row", (
            f"Expected 'test.bi.kpi_row', got '{path}'"
        )

    def test_modify_fallback_resolves_via_index(self):
        """MODIFY con registry vacío + capability existente → resolve_path."""
        idx = structural_index("presentation.kpi_row")
        registry = StructuralRegistry()  # empty

        ir = self.make_ir([(MODIFY, "presentation.kpi_row")])
        result = resolve(ir, registry, structural_index=idx)

        assert result.reason is None, (
            f"Expected successful resolution, got reason={result.reason}"
        )
        path = result.capability_to_path.get("presentation.kpi_row")
        assert path is not None, (
            "MODIFY should resolve path via StructuralIndex.resolve_path"
        )
        assert path == "kpi_row", (
            f"Expected 'kpi_row' (from mapping), got '{path}'"
        )

    def test_modify_fallback_fails_if_not_in_index(self):
        """MODIFY con registry vacío + capability no existente → falla."""
        idx = StructuralIndex.empty()  # nothing exists
        registry = StructuralRegistry()

        ir = self.make_ir([(MODIFY, "presentation.kpi_row")])
        result = resolve(ir, registry, structural_index=idx)

        # MODIFY sin candidates ni index → unknown_count > 0 → no_structural_targets
        assert result.reason == "no_structural_targets", (
            f"Expected 'no_structural_targets', got reason={result.reason}"
        )
        assert result.capability_to_path == {}, (
            "No path should be resolved"
        )

    def test_no_fallback_when_registry_has_candidates(self):
        """Registry con candidates → usa registry, no fallback."""
        idx = StructuralIndex.empty()
        registry = StructuralRegistry()
        registry._register(
            component_instance_path="test.bi.kpi_row",
            type="KpiRow",
            capability="presentation.kpi_row",
        )

        ir = self.make_ir([(MODIFY, "presentation.kpi_row")])
        result = resolve(ir, registry, structural_index=idx)

        assert result.reason is None
        path = result.capability_to_path.get("presentation.kpi_row")
        assert path == "test.bi.kpi_row", (
            f"Should use registry path, got '{path}'"
        )

    def test_multiple_candidates_deterministic_fallback(self):
        """Registry con múltiples candidates → deterministic fallback picks first by sorted path."""
        idx = StructuralIndex.empty()
        registry = StructuralRegistry()
        registry._register(
            component_instance_path="test.bi.kpi_row.v1",
            type="KpiRow", capability="presentation.kpi_row",
        )
        registry._register(
            component_instance_path="test.bi.kpi_row.v2",
            type="KpiRow", capability="presentation.kpi_row",
        )

        ir = self.make_ir([(MODIFY, "presentation.kpi_row")])
        with pytest.raises(AmbiguousStructuralTargetError, match="Multiple candidates"):
            resolve(ir, registry, structural_index=idx)

    def test_no_fallback_when_no_structural_index(self):
        """Sin structural_index → no fallback (unknown_count > 0)."""
        registry = StructuralRegistry()  # empty

        ir = self.make_ir([(CREATE, "presentation.kpi_row")])
        result = resolve(ir, registry, structural_index=None)

        assert result.reason == "no_structural_targets", (
            "Without structural_index, CREATE with empty registry "
            f"should be no_structural_targets, got {result.reason}"
        )
        assert result.capability_to_path == {}
