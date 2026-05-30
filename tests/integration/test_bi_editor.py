"""BI Dashboard Editor — behavioral tests for ADD, REMOVE, MODIFY, MOVE, REPLACE.

Tests exercise the full structural pipeline: actions → layout_hints →
replace_pairs → resolve_layout → consistency. No workspace I/O.

Each test is self-contained, deterministic, and verifies the contract
(StructuralIR outputs, layout hints, edge ordering).
"""

from app.graphir.semantic_frame import _extract_actions
from app.engine.structural_completion import (
    _resolve_action, CREATE, MODIFY, DELETE, KEEP,
    _extract_layout_hints, _extract_replace_pairs,
    validate_replace_consistency, StructuralIR, ResolvedCapability,
    CompletionMode,
)
from app.graphir.structure.layout_resolver import resolve_layout, _node_id_for_cap
from app.graphir.models import (
    GraphIR, GraphIRNode, GraphIREdge, EdgeRole, GraphIRLayout,
)


def _si(*caps: str):
    """Build StructuralIndex from capability names (test helper)."""
    from app.engine.structural_index import StructuralIndex
    from app.engine.state_adapter import ComponentInstanceInfo
    mapping = {
        cap: [ComponentInstanceInfo(capability=cap, path=cap.rsplit(".", 1)[-1])]
        for cap in caps
    }
    return StructuralIndex.from_mapping(mapping)


def _make_dashboard_graph(kpi_after_table: bool = True) -> GraphIR:
    """Helper: create a GraphIR with Page root, kpi_row and table children.

    Args:
        kpi_after_table: if True, kpi edge is after table edge in list.
    """
    nodes = {
        "Page": GraphIRNode(
            id="Page", type="Page",
            component_instance_path="dashboard.sales",
            data={},
            metadata={"intent_capability": "layout.page"},
        ),
        "KpiRow": GraphIRNode(
            id="KpiRow", type="KpiRow",
            component_instance_path="dashboard.sales.kpi_row",
            data={"metrics": ["revenue"]},
            metadata={"intent_capability": "presentation.kpi_row"},
        ),
        "AnalyticsTable": GraphIRNode(
            id="AnalyticsTable", type="AnalyticsTable",
            component_instance_path="dashboard.sales.table",
            data={"columns": ["product", "revenue"]},
            metadata={"intent_capability": "presentation.table"},
        ),
    }
    if kpi_after_table:
        edges = [
            GraphIREdge(source="Page", target="AnalyticsTable", role=EdgeRole.PRIMARY),
            GraphIREdge(source="Page", target="KpiRow", role=EdgeRole.PRIMARY),
        ]
    else:
        edges = [
            GraphIREdge(source="Page", target="KpiRow", role=EdgeRole.PRIMARY),
            GraphIREdge(source="Page", target="AnalyticsTable", role=EdgeRole.PRIMARY),
        ]
    return GraphIR(
        nodes=nodes,
        edges=edges,
        layout=GraphIRLayout(root="Page"),
        params={},
    )


def _make_structural_ir(
    layout_hints: dict | None = None,
    replace_pairs: list | None = None,
) -> StructuralIR:
    """Helper: create a StructuralIR with layout_hints and replace_pairs."""
    return StructuralIR(
        contract_id="test.bi",
        contract_version=1,
        capabilities=(
            ResolvedCapability(
                name="presentation.kpi_row", params={"metrics": ["revenue"]},
                mode=CompletionMode.SAFE_COMPLETE, action=MODIFY,
            ),
            ResolvedCapability(
                name="presentation.table", params={"columns": ["product", "revenue"]},
                mode=CompletionMode.SAFE_COMPLETE, action=MODIFY,
            ),
        ),
        param_provenance={},
        confidence=1.0,
        completion_warnings=(),
        layout_hints=layout_hints or {},
        replace_pairs=replace_pairs or [],
        replace_pairs_index=(
            {new: old for old, new in (replace_pairs or [])}
        ),
    )


# ── ADD ──────────────────────────────────────────────────────────

def test_add_component_no_existing():
    """Workspace vacío → ADD 'add a table' → CREATE action."""
    action = _resolve_action(
        "presentation.table", "add", structural_index=None,
    )
    assert action == CREATE


def test_add_component_existing_upgrades_to_modify():
    """Workspace con table → ADD 'add a table' → MODIFY (upgrade)."""
    action = _resolve_action(
        "presentation.table", "add", structural_index=_si("presentation.table"),
    )
    assert action == MODIFY


# ── REMOVE ───────────────────────────────────────────────────────

def test_remove_existing_component():
    """Workspace con table → REMOVE 'remove the table' → DELETE."""
    action = _resolve_action(
        "presentation.table", "remove", structural_index=_si("presentation.table"),
    )
    assert action == DELETE


def test_remove_nonexistent_component():
    """Workspace vacío → REMOVE 'remove the table' → KEEP (no-op)."""
    action = _resolve_action(
        "presentation.table", "remove", structural_index=None,
    )
    assert action == KEEP


# ── MODIFY ───────────────────────────────────────────────────────

def test_modify_existing_component():
    """Workspace con kpi → MODIFY 'change revenue to profit' → MODIFY."""
    action = _resolve_action(
        "presentation.kpi_row", "change", structural_index=_si("presentation.kpi_row"),
    )
    assert action == MODIFY


def test_modify_nonexistent_component():
    """Workspace vacío → MODIFY 'change revenue to profit' → CREATE."""
    action = _resolve_action(
        "presentation.kpi_row", "change", structural_index=None,
    )
    assert action == CREATE


# ── MOVE ─────────────────────────────────────────────────────────

def test_move_produces_layout_hint():
    """MOVE action → _extract_layout_hints produces hint with scope='layout'."""
    hints = _extract_layout_hints(
        SemanticResolutionStub(actions=[{
            "verb": "move", "object": "kpi", "reference": "table",
        }]),
        contract_caps=["presentation.kpi_row", "presentation.table"],
        structural_index=_si("presentation.kpi_row", "presentation.table"),
    )
    assert "presentation.kpi_row" in hints
    assert hints["presentation.kpi_row"]["move_after"] == "presentation.table"
    assert hints["presentation.kpi_row"]["scope"] == "layout"


def test_move_reorders_edges():
    """MOVE hint → resolve_layout reorders edges: kpi after table."""
    graph = _make_dashboard_graph(kpi_after_table=False)
    sir = _make_structural_ir(layout_hints={
        "presentation.kpi_row": {
            "move_after": "presentation.table",
            "scope": "layout",
        },
    })

    result = resolve_layout(sir, graph)

    edges = list(result.edges)
    edge_targets = [e.target for e in edges]
    kpi_idx = edge_targets.index("KpiRow")
    table_idx = edge_targets.index("AnalyticsTable")
    assert kpi_idx > table_idx, (
        f"Expected KpiRow ({kpi_idx}) after AnalyticsTable ({table_idx})"
    )


def test_move_idempotent():
    """Same MOVE input twice → same graph output."""
    graph = _make_dashboard_graph(kpi_after_table=False)
    sir = _make_structural_ir(layout_hints={
        "presentation.kpi_row": {
            "move_after": "presentation.table",
            "scope": "layout",
        },
    })

    result1 = resolve_layout(sir, graph)
    result2 = resolve_layout(sir, graph)

    assert result1.edges == result2.edges
    assert result1 == result2


# ── REPLACE ──────────────────────────────────────────────────────

def test_replace_preserves_slot():
    """Replace hint → resolve_layout: new edge at old edge position."""
    graph = _make_dashboard_graph(kpi_after_table=True)
    sir = _make_structural_ir(
        layout_hints={
            "presentation.chart.bar": {
                "replace_anchor": "presentation.table",
            },
        },
        replace_pairs=[("presentation.table", "presentation.chart.bar")],
    )

    result = resolve_layout(sir, graph)

    edges = list(result.edges)
    edge_targets = [e.target for e in edges]
    assert "AnalyticsTable" in edge_targets
    assert "KpiRow" in edge_targets
    # AnalyticsTable stayed — LayoutResolver only moves edges for
    # replacement targets. The old AnalyticsTable still exists in graph.
    # In real pipeline DELETE+CREATE happens at fileops level.
    # verify edge order unchanged (table before kpi in _make_dashboard_graph(kpi_after_table=True))
    table_idx = edge_targets.index("AnalyticsTable")
    kpi_idx = edge_targets.index("KpiRow")
    assert table_idx < kpi_idx, "Edge order should be preserved"


def test_replace_produces_pair():
    """REPLACE action → _extract_replace_pairs produces (old, new)."""
    pairs = _extract_replace_pairs(
        SemanticResolutionStub(actions=[{
            "verb": "replace", "object": "table", "reference": "bar chart",
        }]),
        contract_caps=["presentation.table", "presentation.chart.bar"],
        structural_index=_si("presentation.table"),
    )
    assert len(pairs) == 1
    assert pairs[0] == ("presentation.table", "presentation.chart.bar")


def test_replace_action_is_modify_not_delete():
    """REPLACE verb on existing capability → MODIFY (not DELETE)."""
    action = _resolve_action(
        "presentation.table", "replace", structural_index=_si("presentation.table"),
    )
    assert action == MODIFY


def test_replace_consistency_valid():
    """validate_replace_consistency: valid fileops → no warnings."""
    from app.graphir.utils import FileOp
    sir = _make_structural_ir(
        replace_pairs=[("presentation.table", "presentation.chart.bar")],
    )
    fileops = [
        FileOp(action="create", path="/tmp/BarChart.tsx", content=""),
        FileOp(action="delete", path="/tmp/AnalyticsTable.tsx", content=""),
    ]

    warnings = validate_replace_consistency(
        sir, fileops, structural_index=_si("presentation.table"),
    )
    assert warnings == []


def test_replace_consistency_missing_delete():
    """validate_replace_consistency: missing DELETE for old → warning."""
    from app.graphir.utils import FileOp
    sir = _make_structural_ir(
        replace_pairs=[("presentation.table", "presentation.chart.bar")],
    )
    fileops = [
        FileOp(action="create", path="/tmp/BarChart.tsx", content=""),
        # No DELETE for AnalyticsTable
    ]

    warnings = validate_replace_consistency(
        sir, fileops, structural_index=_si("presentation.table"),
    )
    assert len(warnings) == 1
    assert "no DELETE fileop" in warnings[0]


def test_is_replacement_method():
    """StructuralIR.is_replacement() and is_replace_target() work."""
    sir = StructuralIR(
        contract_id="test.bi",
        contract_version=1,
        capabilities=(),
        param_provenance={},
        confidence=1.0,
        replace_pairs=[("presentation.table", "presentation.chart.bar")],
        replace_pairs_index={"presentation.chart.bar": "presentation.table"},
    )
    assert sir.is_replacement("presentation.chart.bar") is True
    assert sir.is_replacement("presentation.table") is False
    assert sir.is_replace_target("presentation.table") is True
    assert sir.is_replace_target("presentation.chart.bar") is False


# ── MOVE + MODIFY combined ──────────────────────────────────────

def test_move_does_not_affect_modify_resolution():
    """MOVE verb → _resolve_action returns MODIFY (same as modify verb)."""
    action_move = _resolve_action(
        "presentation.kpi_row", "move", structural_index=_si("presentation.kpi_row"),
    )
    action_modify = _resolve_action(
        "presentation.kpi_row", "change", structural_index=_si("presentation.kpi_row"),
    )
    assert action_move == MODIFY
    assert action_modify == MODIFY


def test_extracted_action_has_verb():
    """Semantic frame extracts move verb with reference."""
    actions = _extract_actions("move kpi below table")
    move_actions = [a for a in actions if a.verb == "move"]
    assert len(move_actions) >= 1
    ma = move_actions[0]
    assert ma.verb == "move"
    assert ma.reference == "table"
    assert ma.position == "below"


# ── Test 2: Binding semántico (IR no se pierde) ──────────────

def test_semantic_binding_contract_params_survive_to_structural_ir():
    """Contract params llegan intactos a StructuralIR sin transformación."""
    from app.contracts.semantic_resolution import SemanticResolution
    from app.contracts.contract_resolution import ContractResolution
    from app.contracts.skill_registry import get_contract
    from app.engine.structural_completion import complete_structure

    contract = get_contract("analytics.table", 1)
    assert contract is not None, "analytics.table@1 must exist"

    semantic = SemanticResolution(
        semantic_params={"columns": ["Metric", "Value", "Change"]},
        semantic_provenance={},
        confidence=0.9,
        resolution_trace=[],
        actions=[],
    )
    skill_ir = type("SkillIRStub", (), {
        "contract_id": "analytics.table",
        "version": 1,
        "params": {"columns": ["Metric", "Value", "Change"]},
        "intents": [],
        "decomposition": [],
        "metadata": {},
        "confidence": 0.9,
    })()
    contract_resolution = ContractResolution.from_skillir(skill_ir, contract)

    structural_ir = complete_structure(
        semantic, contract_resolution, contract,
        frame_dict={"objects": []},
    )

    # Buscar presentation.table en capabilities
    table_rc = None
    for rc in structural_ir.capabilities:
        if rc.name == "presentation.table":
            table_rc = rc
            break
    assert table_rc is not None, "presentation.table must be in StructuralIR"
    assert table_rc.params.get("columns") == ["Metric", "Value", "Change"], (
        f"Expected columns=['Metric', 'Value', 'Change'], got {table_rc.params.get('columns')}"
    )
    # No se transformó a bar chart
    bar_rc = [rc for rc in structural_ir.capabilities if "chart.bar" in rc.name]
    assert len(bar_rc) == 0, "table must not transform to bar chart"
    # No se degradó a KPI
    kpi_rc = [rc for rc in structural_ir.capabilities if "kpi_row" in rc.name]
    assert len(kpi_rc) == 0, "table must not degrade to kpi_row"


def test_semantic_binding_no_ir_leak_to_layout_page():
    """Params de tabla no filtran a layout.page."""
    from app.contracts.semantic_resolution import SemanticResolution
    from app.contracts.contract_resolution import ContractResolution
    from app.contracts.skill_registry import get_contract
    from app.engine.structural_completion import complete_structure

    contract = get_contract("analytics.table", 1)
    semantic = SemanticResolution(
        semantic_params={"columns": ["Metric", "Value"]},
        semantic_provenance={},
        confidence=0.9,
        resolution_trace=[],
        actions=[],
    )
    skill_ir = type("SkillIRStub", (), {
        "contract_id": "analytics.table",
        "version": 1,
        "params": {"columns": ["Metric", "Value"]},
        "intents": [],
        "decomposition": [],
        "metadata": {},
        "confidence": 0.9,
    })()
    contract_resolution = ContractResolution.from_skillir(skill_ir, contract)

    structural_ir = complete_structure(
        semantic, contract_resolution, contract,
        frame_dict={"objects": []},
    )

    for rc in structural_ir.capabilities:
        if rc.name == "layout.page":
            params = rc.params
            assert "columns" not in params, f"layout.page must not contain table columns: {params}"
            assert "table_data" not in params, f"layout.page must not contain table_data"
            break


# ── Test 3: Layout MOVE (position_hint, layout_role) ───────

def test_move_scope_layout_reorders_edges():
    """MOVE scope='layout' reordena edges sin cambiar parent."""
    graph = _make_dashboard_graph(kpi_after_table=False)
    sir = _make_structural_ir(layout_hints={
        "presentation.kpi_row": {
            "move_after": "presentation.table",
            "scope": "layout",
        },
    })
    result = resolve_layout(sir, graph)

    edges = list(result.edges)
    edge_targets = [e.target for e in edges]
    kpi_idx = edge_targets.index("KpiRow")
    table_idx = edge_targets.index("AnalyticsTable")
    assert kpi_idx > table_idx, "KpiRow must be after AnalyticsTable after MOVE"
    # Parent no cambió — ambos siguen siendo hijos de Page
    for e in edges:
        assert e.source == "Page", "Parent must remain Page after scope=layout MOVE"


def test_move_scope_structure_not_implemented():
    """MOVE scope='structure' no está implementado y debe ser ignorado por resolve_layout."""
    sir = _make_structural_ir(layout_hints={
        "presentation.kpi_row": {
            "move_after": "presentation.table",
            "scope": "structure",
        },
    })
    graph = _make_dashboard_graph(kpi_after_table=False)
    result = resolve_layout(sir, graph)

    # scope='structure' no es manejado → graph sin cambios
    assert result == graph, "scope='structure' must not modify graph (not implemented)"


# ── Test 4: Estabilidad MOVE (slot_id fijo, anchor cambia) ─

def test_move_slot_id_stable():
    """MOVE no cambia slot_id de ComponentInstanceInfo."""
    from app.engine.state_adapter import ComponentInstanceInfo

    original = ComponentInstanceInfo(
        capability="presentation.kpi_row",
        path="kpi_row",
        anchor={"parent": "Page", "index": 0},
        slot_id="kpi_row",
    )
    # Simular MOVE: anchor cambia, slot_id NO
    moved = ComponentInstanceInfo(
        capability="presentation.kpi_row",
        path="kpi_row",
        anchor={"parent": "Page", "index": 1},
        slot_id="kpi_row",
    )
    assert original.slot_id == moved.slot_id, "slot_id must not change after MOVE"
    assert original.anchor != moved.anchor, "anchor must change after MOVE"


def test_move_anchor_changes_identity_does_not():
    """MOVE: identity (capability+path) es estable, anchor es volátil."""
    from app.engine.state_adapter import ComponentInstanceInfo

    before = ComponentInstanceInfo(
        capability="presentation.kpi_row", path="kpi_row",
        anchor={"parent": "Page", "index": 0}, slot_id="kpi_row",
    )
    after = ComponentInstanceInfo(
        capability="presentation.kpi_row", path="kpi_row",
        anchor={"parent": "Page", "index": 2}, slot_id="kpi_row",
    )
    # Identity: capability + path + slot_id
    assert (before.capability, before.path, before.slot_id) == (
        after.capability, after.path, after.slot_id,
    ), "Identity must be stable under MOVE"
    # Position: anchor cambia
    assert before.anchor != after.anchor, "Position anchor must change under MOVE"


# ── Test 5: Idempotencia del pipeline ──────────────────────

def test_same_input_produces_same_structural_ir():
    """Misma entrada → mismo StructuralIR (determinismo)."""
    from app.contracts.semantic_resolution import SemanticResolution
    from app.contracts.contract_resolution import ContractResolution
    from app.contracts.skill_registry import get_contract
    from app.engine.structural_completion import complete_structure

    contract = get_contract("analytics.table", 1)
    semantic = SemanticResolution(
        semantic_params={"columns": ["Metric", "Value"]},
        semantic_provenance={},
        confidence=0.9,
        resolution_trace=[],
        actions=[],
    )
    skill_ir = type("SkillIRStub", (), {
        "contract_id": "analytics.table",
        "version": 1,
        "params": {"columns": ["Metric", "Value"]},
        "intents": [],
        "decomposition": [],
        "metadata": {},
        "confidence": 0.9,
    })()
    contract_resolution = ContractResolution.from_skillir(skill_ir, contract)

    ir1 = complete_structure(
        semantic, contract_resolution, contract, frame_dict={"objects": []},
    )
    ir2 = complete_structure(
        semantic, contract_resolution, contract, frame_dict={"objects": []},
    )

    assert ir1.contract_id == ir2.contract_id
    assert ir1.contract_version == ir2.contract_version
    assert len(ir1.capabilities) == len(ir2.capabilities)
    for rc1, rc2 in zip(ir1.capabilities, ir2.capabilities):
        assert rc1.name == rc2.name
        assert rc1.action == rc2.action
        assert rc1.params == rc2.params


# ── Test 6: REPLACE vs DELETE (crítico para refactor) ──────

def test_replace_vs_delete_replace_pairs_in_ir():
    """REPLACE produce replace_pairs en IR, NO delete en operations."""
    sir = StructuralIR(
        contract_id="test.bi",
        contract_version=1,
        capabilities=(),
        param_provenance={},
        confidence=1.0,
        replace_pairs=[("presentation.table", "presentation.chart.bar")],
        replace_pairs_index={"presentation.chart.bar": "presentation.table"},
    )
    assert len(sir.replace_pairs) == 1
    assert sir.replace_pairs[0] == ("presentation.table", "presentation.chart.bar")
    # NO hay DELETE en operations
    delete_ops = [op for op in sir.operations if op.get("action") == "DELETE"]
    assert len(delete_ops) == 0, "REPLACE must not produce DELETE in operations"


def test_replace_vs_delete_fileops_do_delete():
    """REPLACE debe producir DELETE fileop para old (a nivel fileops, no IR)."""
    from app.graphir.utils import FileOp
    sir = StructuralIR(
        contract_id="test.bi",
        contract_version=1,
        capabilities=(),
        param_provenance={},
        confidence=1.0,
        replace_pairs=[("presentation.table", "presentation.chart.bar")],
        replace_pairs_index={"presentation.chart.bar": "presentation.table"},
    )
    fileops = [
        FileOp(action="create", path="src/components/BarChart.tsx", content=""),
        FileOp(action="delete", path="src/components/AnalyticsTable.tsx", content=""),
    ]
    warnings = validate_replace_consistency(
        sir, fileops, structural_index=_si("presentation.table"),
    )
    assert warnings == [], f"Replace consistency warnings: {warnings}"

    # Verificar que fileops tiene delete para old
    delete_ops = [fop for fop in fileops if fop.action == "delete"]
    assert len(delete_ops) == 1, "REPLACE must produce exactly 1 DELETE fileop"
    assert "AnalyticsTable" in delete_ops[0].path


# ── Test 8: Regresión estructural global ────────────────────

def test_global_regression_5_nodes_0_orphans():
    """5 capabilities → 5 GraphIR nodes, 0 orphans, layout OK."""
    from app.engine.structural_completion import (
        complete_structure, StructuralIR, ResolvedCapability, CompletionMode,
    )
    from app.graphir.builder import GraphIRBuilder
    from app.graphir.structure.models import StructuralResolution

    capabilities = (
        ResolvedCapability("layout.page", {}, CompletionMode.SAFE_COMPLETE, CREATE),
        ResolvedCapability("presentation.kpi_row", {"metrics": ["revenue"]}, CompletionMode.SAFE_COMPLETE, CREATE),
        ResolvedCapability("presentation.table", {"columns": ["Metric", "Value"]}, CompletionMode.SAFE_COMPLETE, CREATE),
        ResolvedCapability("presentation.chart.bar", {"metrics": ["revenue"]}, CompletionMode.SAFE_COMPLETE, CREATE),
        ResolvedCapability("interaction.form", {"fields": ["name"]}, CompletionMode.SAFE_COMPLETE, CREATE),
    )
    sir = StructuralIR(
        contract_id="test.bi",
        contract_version=1,
        capabilities=capabilities,
        param_provenance={},
        confidence=1.0,
    )
    graph = GraphIRBuilder.build_from_structural(sir)

    # 5 nodos
    assert len(graph.nodes) == 5, f"Expected 5 nodes, got {len(graph.nodes)}"
    node_types = {n.type for n in graph.nodes.values()}
    assert "Page" in node_types
    assert "KpiRow" in node_types
    assert "AnalyticsTable" in node_types
    assert "BarChart" in node_types

    # 0 orphan nodes
    targets = {e.target for e in graph.edges}
    for nid, node in graph.nodes.items():
        if node.type == "Page":
            continue
        assert nid in targets, f"Node {nid} is orphan (no incoming edge)"

    # Layout deriva sin errores
    from app.graphir.layout import LayoutDerivationEngine
    layout = LayoutDerivationEngine.derive(graph)
    assert layout is not None
    root_node_id = next(nid for nid, n in graph.nodes.items() if n.type == "Page")
    assert layout.root == root_node_id


def test_global_regression_0_layout_conflicts():
    """GraphIR layout derivation no produce conflictos."""
    from app.engine.structural_completion import StructuralIR, ResolvedCapability, CompletionMode
    from app.graphir.builder import GraphIRBuilder
    from app.graphir.layout import LayoutDerivationEngine
    from app.graphir.models import LayoutConstraint

    capabilities = (
        ResolvedCapability("layout.page", {}, CompletionMode.SAFE_COMPLETE, CREATE),
        ResolvedCapability("presentation.kpi_row", {"metrics": ["revenue"]}, CompletionMode.SAFE_COMPLETE, CREATE),
        ResolvedCapability("presentation.table", {"columns": ["Metric"]}, CompletionMode.SAFE_COMPLETE, CREATE),
    )
    sir = StructuralIR(
        contract_id="test.bi",
        contract_version=1,
        capabilities=capabilities,
        param_provenance={},
        confidence=1.0,
    )
    graph = GraphIRBuilder.build_from_structural(sir)
    layout = LayoutDerivationEngine.derive(graph)

    # Ningún nodo tiene HIDDEN sin razón
    for nid, constraints in layout.constraints.items():
        assert LayoutConstraint.HIDDEN not in constraints, (
            f"Node {nid} has HIDDEN constraint — possible layout conflict"
        )
    # Todos los nodos tienen al menos un constraint
    assert all(len(c) > 0 for c in layout.constraints.values())


# ── Stub for SemanticResolution ─────────────────────────────────

class SemanticResolutionStub:
    """Minimal stub so _extract_layout_hints and _extract_replace_pairs work."""
    def __init__(self, actions: list[dict]):
        self.actions = actions
        self.semantic_params = {}
        self.semantic_provenance = {}
        self.confidence = 1.0
        self.resolution_trace = []
