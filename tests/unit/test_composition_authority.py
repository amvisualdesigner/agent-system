"""F7 lock tests: composition contract authority.

The composition parent/child rules are CONTRACTUAL — derived exclusively from
SkillContract.ast_template (capabilities + slots). They MUST NOT react to the
repository, GraphIR topology, or data_access.json.

Pinned rules:
  A.  _build_contract_composition_map returns {child_cap: parent_cap} only from
      contract slots; parent is the Page/layout capability.
  C1. Child CREATE/DELETE/instance_only  -> parent promoted to MODIFY.
  C2. Child MODIFY                       -> parent unchanged.
  C3. Parent CREATE/MODIFY -> KEEP slot child -> INSTANCE (instance_only=True)
      in StructuralIR.operations.
"""
from __future__ import annotations

import pytest

from app.contracts.contract_resolution import ContractResolution
from app.contracts.semantic_resolution import SemanticResolution
from app.contracts.skill_registry import SkillContract
from app.engine.structural_completion import (
    CREATE,
    DELETE,
    KEEP,
    MODIFY,
    CompletionMode,
    ResolvedCapability,
    _build_contract_composition_map,
    _expand_composition_children,
    _sync_composition_parents,
    complete_structure,
)
from app.intent.models import RefactorChange


# ─── Fixtures ───


@pytest.fixture
def composition_contract() -> SkillContract:
    """Contract with Page parent + two slot children (dashboard-like)."""
    return SkillContract(
        contract_id="dashboard.sales_overview",
        version=1,
        input_schema={
            "type": "object",
            "required": ["metrics"],
            "properties": {
                "metrics": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["revenue", "growth", "retention", "churn"]},
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
                "Domain": "domain.sales",
            },
        },
        renderer={},
    )


@pytest.fixture
def map_purity_contract(composition_contract: SkillContract) -> SkillContract:
    """Same contract plus a capability WITHOUT a slot: must NOT become a child."""
    caps = dict(composition_contract.ast_template["capabilities"])
    caps["Loose"] = "presentation.loose"
    return SkillContract(
        contract_id=composition_contract.contract_id,
        version=1,
        input_schema=composition_contract.input_schema,
        ast_template={**composition_contract.ast_template, "capabilities": caps},
        renderer={},
    )


@pytest.fixture
def no_composition_contract(composition_contract: SkillContract) -> SkillContract:
    """Contract without any slot: no composition map, all rules no-op."""
    ast = dict(composition_contract.ast_template)
    ast = {**ast, "slots": []}
    return SkillContract(
        contract_id=composition_contract.contract_id,
        version=1,
        input_schema=composition_contract.input_schema,
        ast_template=ast,
        renderer={},
    )


# ─── Helpers ───


def rc(
    name: str,
    action: str = KEEP,
    instance_only: bool = False,
    params: dict | None = None,
) -> ResolvedCapability:
    return ResolvedCapability(
        name=name,
        params=params if params is not None else {},
        mode=CompletionMode.SAFE_COMPLETE,
        action=action,
        instance_only=instance_only,
    )


def make_contract_resolution(params: dict | None = None, confidence: float = 0.9) -> ContractResolution:
    return ContractResolution(
        contract_params=params or {},
        contract_provenance={k: "test" for k in (params or {})},
        confidence=confidence,
        contract_id="dashboard.sales_overview",
        contract_version=1,
    )


def make_semantic(params: dict | None = None, actions: list[dict] | None = None, confidence: float = 0.9) -> SemanticResolution:
    return SemanticResolution(
        semantic_params=params or {},
        semantic_provenance={k: "test" for k in (params or {})},
        confidence=confidence,
        actions=actions or [],
    )


# ─── A. Composition map: contract-only authority ───


class TestCompositionMapContractAuthority:
    """_build_contract_composition_map derives {child→parent} ONLY from contract ast_template."""

    def test_map_composed_of_page_parent_and_slot_children(self, composition_contract):
        expected = {
            "presentation.kpi_row": "layout.page",
            "presentation.timeseries": "layout.page",
        }
        assert _build_contract_composition_map(composition_contract) == expected

    def test_capability_without_slot_is_not_a_child(self, map_purity_contract):
        cmap = _build_contract_composition_map(map_purity_contract)
        assert "presentation.loose" not in cmap
        assert cmap == {
            "presentation.kpi_row": "layout.page",
            "presentation.timeseries": "layout.page",
        }

    def test_map_is_deterministic_per_contract(self, composition_contract):
        assert _build_contract_composition_map(composition_contract) == _build_contract_composition_map(composition_contract)

    def test_no_slots_means_no_composition_map(self, no_composition_contract):
        assert _build_contract_composition_map(no_composition_contract) == {}


# ─── C1/C2. Parent promotion is contract-triggered only ───


class TestCompositionParentPromotion:
    """_sync_composition_parents: child CREATE/DELETE/instance_only -> parent MODIFY;
    child MODIFY never promotes."""

    def test_child_create_promotes_parent_to_modify(self, composition_contract):
        resolved = [rc("presentation.kpi_row", CREATE), rc("layout.page", KEEP)]
        warnings: list[str] = []

        trace = _sync_composition_parents(resolved, composition_contract, warnings)

        page = resolved[1]
        assert page.action == MODIFY
        assert len(trace) == 1
        rc_entry = trace[0]
        assert isinstance(rc_entry, RefactorChange)
        assert rc_entry.change_type == "composition_sync"
        assert rc_entry.source == "presentation.kpi_row"
        assert rc_entry.target == "layout.page"
        assert "composition sync" in (rc_entry.reason or "")

    def test_child_delete_promotes_parent_to_modify(self, composition_contract):
        resolved = [rc("presentation.timeseries", DELETE), rc("layout.page", KEEP)]
        warnings: list[str] = []

        trace = _sync_composition_parents(resolved, composition_contract, warnings)

        assert resolved[1].action == MODIFY
        assert len(trace) == 1
        assert trace[0].source == "presentation.timeseries"

    def test_child_instance_only_promotes_parent_to_modify(self, composition_contract):
        resolved = [rc("presentation.kpi_row", KEEP, instance_only=True), rc("layout.page", KEEP)]
        warnings: list[str] = []

        trace = _sync_composition_parents(resolved, composition_contract, warnings)

        assert resolved[1].action == MODIFY
        assert len(trace) == 1
        assert trace[0].reason and "instance_only" in trace[0].reason

    def test_child_modify_never_promotes_parent(self, composition_contract):
        resolved = [rc("presentation.kpi_row", MODIFY), rc("layout.page", KEEP)]
        warnings: list[str] = []

        trace = _sync_composition_parents(resolved, composition_contract, warnings)

        assert resolved[1].action == KEEP
        assert trace == []

    def test_non_slot_child_never_promotes_parent(self, composition_contract):
        resolved = [rc("domain.sales", CREATE), rc("layout.page", KEEP)]
        warnings: list[str] = []

        trace = _sync_composition_parents(resolved, composition_contract, warnings)

        assert resolved[1].action == KEEP
        assert trace == []

    def test_already_creating_parent_is_idempotent(self, composition_contract):
        resolved = [rc("presentation.kpi_row", CREATE), rc("layout.page", CREATE)]
        warnings: list[str] = []

        trace = _sync_composition_parents(resolved, composition_contract, warnings)

        assert resolved[1].action == CREATE
        assert trace == []

    def test_no_composition_map_is_noop(self, no_composition_contract):
        resolved = [rc("presentation.kpi_row", CREATE), rc("layout.page", KEEP)]
        warnings: list[str] = []

        trace = _sync_composition_parents(resolved, no_composition_contract, warnings)

        assert resolved[1].action == KEEP
        assert trace == []


# ─── C3. Parent regeneration expands KEEP slot children to INSTANCE ───


class TestCompositionChildExpansion:
    """_expand_composition_children: parent CREATE/MODIFY -> KEEP slot children -> INSTANCE."""

    def test_keep_children_expanded_under_modify_parent(self, composition_contract):
        resolved = [
            rc("layout.page", MODIFY),
            rc("presentation.kpi_row", KEEP),
            rc("presentation.timeseries", CREATE),
        ]
        warnings: list[str] = []

        _expand_composition_children(resolved, composition_contract, warnings)

        by_name = {r.name: r for r in resolved}
        assert by_name["presentation.kpi_row"].instance_only is True
        assert by_name["presentation.kpi_row"].action == KEEP
        assert by_name["presentation.timeseries"].action == CREATE
        assert by_name["presentation.timeseries"].instance_only is False

    def test_keep_children_expanded_under_create_parent(self, composition_contract):
        resolved = [
            rc("layout.page", CREATE),
            rc("presentation.kpi_row", KEEP),
        ]
        warnings: list[str] = []

        _expand_composition_children(resolved, composition_contract, warnings)

        assert resolved[1].instance_only is True

    def test_no_expansion_when_parent_is_keep(self, composition_contract):
        resolved = [rc("layout.page", KEEP), rc("presentation.kpi_row", KEEP)]
        warnings: list[str] = []

        _expand_composition_children(resolved, composition_contract, warnings)

        assert resolved[1].instance_only is False

    def test_no_composition_map_is_noop(self, no_composition_contract):
        resolved = [rc("layout.page", CREATE), rc("presentation.kpi_row", KEEP)]
        warnings: list[str] = []

        _expand_composition_children(resolved, no_composition_contract, warnings)

        assert resolved[1].instance_only is False


# ─── Integration: complete_structure honors the pinned rules ───


class TestCompositionContractThroughCompleteStructure:
    """complete_structure end-to-end: promotions and INSTANCE ops survive into StructuralIR."""

    def test_create_child_promotes_page_to_modify_with_trace(self, composition_contract):
        sem = make_semantic(params={"metrics": ["revenue"]}, actions=[{"verb": "create", "object": "kpi"}])
        cr = make_contract_resolution(params={"metrics": ["revenue"]})

        ir = complete_structure(sem, cr, composition_contract)

        by_name = {cap.name: cap for cap in ir.capabilities}
        assert by_name["layout.page"].action == MODIFY
        assert by_name["presentation.kpi_row"].action == CREATE
        assert any(
            rc_entry.change_type == "composition_sync"
            and rc_entry.source == "presentation.kpi_row"
            and rc_entry.target == "layout.page"
            for rc_entry in ir.composition_sync_trace
        )

    def test_create_page_expands_keep_children_to_instance(self, composition_contract):
        sem = make_semantic(params={"metrics": ["revenue"]}, actions=[{"verb": "create", "object": "page"}])
        cr = make_contract_resolution(params={"metrics": ["revenue"]})

        ir = complete_structure(sem, cr, composition_contract)

        ops = {op["target"]: op for op in ir.operations}
        assert ops["layout.page"]["action"] == CREATE
        assert ops["presentation.timeseries"]["action"] == "INSTANCE"
        assert ops["presentation.timeseries"]["instance_only"] is True

        by_name = {cap.name: cap for cap in ir.capabilities}
        assert by_name["presentation.timeseries"].instance_only is True

    def test_operations_never_contain_instance_without_contract_child(self, composition_contract):
        sem = make_semantic(params={"metrics": ["revenue"]}, actions=[{"verb": "create", "object": "page"}])
        cr = make_contract_resolution(params={"metrics": ["revenue"]})

        ir = complete_structure(sem, cr, composition_contract)

        for op in ir.operations:
            if op.get("action") == "INSTANCE":
                assert op["target"] in _build_contract_composition_map(composition_contract)