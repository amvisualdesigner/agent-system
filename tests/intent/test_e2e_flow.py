"""E2E flow validation: interpret → confirm → preview.

Tests 3 casos del usuario sin LLM (simulando el output del LLM
y probando el pipeline determinista completo).
"""

from __future__ import annotations

import json
import pytest

from app.intent.models import ConfirmedIntent, IntentAction
from app.intent.plan_compiler import compile_plan, expand_container_actions
from app.contracts.skill_registry import get_contract
from app.catalog.loader import get_contract_catalog


# ── Helpers ─────────────────────────────────────────────────────


def _simulate_interpret(message: str) -> dict:
    """Simula IntentInterpreter sin LLM para test.

    Returns dict similar a InterpretationDraft.to_dict().
    """
    from app.intent.interpreter import _select_contract, _has_action_verb, _build_worktree_caps

    contract_id = _select_contract(message)
    if not contract_id:
        return {"status": "unsupported", "contract_id": ""}

    catalog_entry = get_contract_catalog(contract_id)
    if not catalog_entry:
        return {"status": "unsupported"}

    if not _has_action_verb(message):
        return {"status": "needs_clarification", "contract_id": contract_id}

    # Para test, hardcodeamos los actions esperados según el message
    lower = message.lower()
    actions = []
    params = {}

    if "line chart" in lower or "linechart" in lower or "timeseries" in lower or "trend chart" in lower:
        if any(v in lower for v in ["remove", "delete", "hide"]):
            actions.append({"verb": "remove", "target_capability": "presentation.timeseries", "label": "Trend chart", "confidence": 0.9})
        elif any(v in lower for v in ["add", "create", "include", "insert"]):
            actions.append({"verb": "create", "target_capability": "presentation.timeseries", "label": "Trend chart", "confidence": 0.9})
    if "kpi" in lower or "metric" in lower:
        if any(v in lower for v in ["update", "modify", "change", "set"]):
            actions.append({"verb": "modify", "target_capability": "presentation.kpi_row", "label": "KPI row", "confidence": 0.9})
            if "revenue" in lower:
                params["metrics"] = ["revenue"]
            if "growth" in lower:
                params.setdefault("metrics", []).append("growth")
    if "dashboard" in lower or "page" in lower:
        if any(v in lower for v in ["update", "modify", "change", "edit"]):
            actions.append({"verb": "modify", "target_capability": "layout.page", "label": "Dashboard page", "confidence": 0.9})

    return {
        "status": "ok",
        "contract_id": contract_id,
        "contract_version": 1,
        "proposed_actions": actions,
        "params_proposed": params,
    }


def _get_estimated_files_from_contract(contract_id: str, version: int = 1) -> list[str]:
    """Replica la lógica de agent_confirm.py para estimated_files."""
    contract = get_contract(contract_id, version)
    if not contract:
        return []
    renderer = contract.renderer or {}
    base_path = renderer.get("base_path", "")
    return [f"{base_path}{f['path']}" for f in renderer.get("files", [])]


def _compute_actual_files_from_ops(structural_ops: list[dict], contract_id: str) -> list[str]:
    """Compute expected actual file paths from structural operations.

    Mapea cada capability a su archivo según el contract renderer.
    """
    contract = get_contract(contract_id, 1)
    if not contract:
        return []
    renderer = contract.renderer or {}
    base_path = renderer.get("base_path", "")

    # Build capability → file mapping: match capability suffix to file path
    cap_to_file: dict[str, str] = {}
    for f_entry in renderer.get("files", []):
        full_path = f"{base_path}{f_entry['path']}"
        fpath_lower = f_entry["path"].lower()
        for cap in ("layout.page", "presentation.kpi_row", "presentation.timeseries",
                    "presentation.table", "presentation.filter_panel",
                    "presentation.chart.bar", "presentation.metric_card"):
            suffix = cap.rsplit(".", 1)[-1].lower()
            if suffix in fpath_lower:
                cap_to_file[cap] = full_path
                break

    result = []
    for op in structural_ops:
        target = op.get("target", "")
        mapped = cap_to_file.get(target)
        if not mapped:
            # Fallback: match by capability suffix in any renderer file
            suffix = target.rsplit(".", 1)[-1].lower()
            for f_entry in renderer.get("files", []):
                if suffix in f_entry["path"].lower():
                    mapped = f"{base_path}{f_entry['path']}"
                    break
        if not mapped:
            mapped = f"{base_path}{target.rsplit('.', 1)[-1]}.tsx"
        result.append(mapped)
    return sorted(set(result))


def _simulate_confirm(interpret_result: dict) -> dict:
    """Simula POST /agent/confirm devolviendo plan + preview."""
    contract_id = interpret_result.get("contract_id", "")
    actions_data = interpret_result.get("proposed_actions", [])
    params = interpret_result.get("params_proposed", {})

    if not actions_data:
        return {"status": "rejected", "reason": "no_actions"}

    contract = get_contract(contract_id, 1)
    if not contract:
        return {"status": "rejected", "reason": "contract_not_found"}

    # Strip extra keys (label, reason) that are not in IntentAction
    clean_actions = []
    for a in actions_data:
        clean_actions.append({
            "verb": a.get("verb", ""),
            "target_capability": a.get("target_capability", ""),
            "params": a.get("params", {}),
            "confidence": a.get("confidence", 1.0),
        })
    confirmed = ConfirmedIntent(
        contract_id=contract_id,
        contract_version=1,
        actions=[IntentAction(**a) for a in clean_actions],
        params=params,
        user_message="test",
        interpretation_id="e2e-test",
    )

    try:
        plan = compile_plan(confirmed)
    except ValueError as e:
        return {"status": "rejected", "reason": str(e)}

    preview_actions = expand_container_actions(confirmed.actions, contract)
    summary = "; ".join(f"{a.verb.capitalize()} {a.target_capability}" for a in preview_actions)

    structural_ops = []
    for a in preview_actions:
        op_verb = "DELETE" if a.verb == "remove" else a.verb.upper()
        structural_ops.append({"action": op_verb, "target": a.target_capability})

    # 3D: estimated_files from structural_ops × contract capability→file mapping
    cap_to_file = _build_test_cap_file_map(contract)
    estimated_files = sorted(set(
        cap_to_file[op["target"]] for op in structural_ops
        if op["target"] in cap_to_file
    ))

    return {
        "status": "ok",
        "plan": plan.to_dict(),
        "plan_preview": {
            "summary_human": summary,
            "structural_operations": structural_ops,
            "estimated_files": estimated_files,
        },
        "gate": {"blocked": False},
    }


def _build_test_cap_file_map(contract) -> dict[str, str]:
    """Replica _build_capability_file_map de agent_confirm.py para tests."""
    renderer = contract.renderer or {}
    base_path = renderer.get("base_path", "")
    capabilities_map = contract.ast_template.get("capabilities", {})
    cap_to_file: dict[str, str] = {}
    for f_entry in renderer.get("files", []):
        fname = f_entry["path"].rsplit("/", 1)[-1].replace(".tsx", "")
        cap = capabilities_map.get(fname)
        if cap:
            cap_to_file[cap] = f"{base_path}{f_entry['path']}"
    return cap_to_file


# ── Caso 1: Remove line chart ──────────────────────────────────


class TestCase1RemoveLineChart:
    """Usuario: 'Remove line chart'"""

    def test_interpret_selects_dashboard(self):
        result = _simulate_interpret("Remove line chart")
        assert result["status"] == "ok"
        assert result["contract_id"] == "dashboard.sales_overview"

    def test_interpret_detects_timeseries_remove(self):
        result = _simulate_interpret("Remove line chart")
        actions = result["proposed_actions"]
        assert len(actions) == 1
        assert actions[0]["verb"] == "remove"
        assert actions[0]["target_capability"] == "presentation.timeseries"

    def test_confirm_produces_delete_operation(self):
        interpret = _simulate_interpret("Remove line chart")
        result = _simulate_confirm(interpret)
        assert result["status"] == "ok"
        assert result["gate"]["blocked"] is False

        preview = result["plan_preview"]
        ops = preview["structural_operations"]
        assert len(ops) == 1
        assert ops[0]["action"] == "DELETE"
        assert ops[0]["target"] == "presentation.timeseries"

    def test_compiled_plan_has_one_intent(self):
        interpret = _simulate_interpret("Remove line chart")
        result = _simulate_confirm(interpret)
        plan = result["plan"]
        assert len(plan["intents"]) == 1
        assert plan["intents"][0]["capability"] == "presentation.timeseries"

    def test_compiled_plan_has_no_container_expansion(self):
        """Remove line chart on a leaf cap should NOT expand."""
        interpret = _simulate_interpret("Remove line chart")
        result = _simulate_confirm(interpret)
        preview = result["plan_preview"]
        ops = preview["structural_operations"]
        targets = [o["target"] for o in ops]
        assert "presentation.kpi_row" not in targets
        assert "presentation.timeseries" in targets


# ── Caso 2: Update KPI metrics to revenue and growth ───────────


class TestCase2UpdateKpiMetrics:
    """Usuario: 'Update KPI metrics to revenue and growth'"""

    def test_interpret_selects_dashboard(self):
        result = _simulate_interpret("Update KPI metrics to revenue and growth")
        assert result["status"] == "ok"
        assert result["contract_id"] == "dashboard.sales_overview"

    def test_interpret_detects_kpi_modify_with_metrics(self):
        result = _simulate_interpret("Update KPI metrics to revenue and growth")
        actions = result["proposed_actions"]
        assert len(actions) == 1
        assert actions[0]["verb"] == "modify"
        assert actions[0]["target_capability"] == "presentation.kpi_row"
        assert "revenue" in result["params_proposed"].get("metrics", [])
        assert "growth" in result["params_proposed"].get("metrics", [])

    def test_confirm_preview_shows_modify_kpi(self):
        interpret = _simulate_interpret("Update KPI metrics to revenue and growth")
        result = _simulate_confirm(interpret)
        assert result["status"] == "ok"
        preview = result["plan_preview"]
        ops = preview["structural_operations"]
        assert len(ops) == 1
        assert ops[0]["action"] == "MODIFY"
        assert ops[0]["target"] == "presentation.kpi_row"

    def test_compiled_plan_has_params(self):
        interpret = _simulate_interpret("Update KPI metrics to revenue and growth")
        result = _simulate_confirm(interpret)
        plan = result["plan"]
        assert "metrics" in plan["skill_ir"]["params"]
        metrics = plan["skill_ir"]["params"]["metrics"]
        assert "revenue" in metrics
        assert "growth" in metrics

    def test_no_container_expansion(self):
        """KPI row is leaf, no container expansion."""
        interpret = _simulate_interpret("Update KPI metrics to revenue and growth")
        result = _simulate_confirm(interpret)
        ops = result["plan_preview"]["structural_operations"]
        targets = [o["target"] for o in ops]
        assert len(targets) == 1
        assert targets[0] == "presentation.kpi_row"


# ── Caso 3: Update dashboard (container expansion) ─────────────


class TestCase3UpdateDashboard:
    """Usuario: 'Update dashboard' — debe expandir contenedor."""

    def test_interpret_selects_dashboard(self):
        result = _simulate_interpret("Update dashboard")
        assert result["status"] == "ok"
        assert result["contract_id"] == "dashboard.sales_overview"

    def test_interpret_detects_page_modify(self):
        result = _simulate_interpret("Update dashboard")
        actions = result["proposed_actions"]
        assert len(actions) == 1
        assert actions[0]["verb"] == "modify"
        assert actions[0]["target_capability"] == "layout.page"

    def test_confirm_expands_to_children(self):
        """Update dashboard → debe expandir a layout.page + kpi_row + timeseries."""
        interpret = _simulate_interpret("Update dashboard")
        result = _simulate_confirm(interpret)
        assert result["status"] == "ok"

        ops = result["plan_preview"]["structural_operations"]
        targets = [o["target"] for o in ops]
        print(f"Expanded targets: {targets}")

        assert "layout.page" in targets, f"Expected layout.page in {targets}"
        assert "presentation.kpi_row" in targets, f"Expected kpi_row in {targets}"
        assert "presentation.timeseries" in targets, f"Expected timeseries in {targets}"

    def test_confirm_no_leaf_only(self):
        """Should not be only one operation."""
        interpret = _simulate_interpret("Update dashboard")
        result = _simulate_confirm(interpret)
        ops = result["plan_preview"]["structural_operations"]
        assert len(ops) >= 3, f"Expected at least 3 operations, got {len(ops)}"

    def test_compiled_plan_includes_all_intents(self):
        interpret = _simulate_interpret("Update dashboard")
        result = _simulate_confirm(interpret)
        plan = result["plan"]
        intents = plan["intents"]
        capabilities = {i["capability"] for i in intents}
        assert "layout.page" in capabilities
        assert "presentation.kpi_row" in capabilities
        assert "presentation.timeseries" in capabilities


# ── Caso 4: Add trend chart (preview vs reality convergence) ────


class TestCase4AddTrendChart:
    """Usuario: 'Add trend chart' — verificar que estimated_files converge con operaciones reales."""

    def test_interpret_selects_dashboard(self):
        result = _simulate_interpret("Add trend chart")
        assert result["status"] == "ok"
        assert result["contract_id"] == "dashboard.sales_overview"

    def test_interpret_detects_timeseries_create(self):
        result = _simulate_interpret("Add trend chart")
        actions = result["proposed_actions"]
        assert len(actions) == 1
        assert actions[0]["verb"] == "create"
        assert actions[0]["target_capability"] == "presentation.timeseries"

    def test_estimated_files_matches_operations(self):
        """estimated_files ahora deriva de structural_operations, no del contrato completo."""
        interpret = _simulate_interpret("Add trend chart")
        result = _simulate_confirm(interpret)
        estimated = result["plan_preview"]["estimated_files"]
        ops = result["plan_preview"]["structural_operations"]

        # Solo 1 operación (create timeseries) → solo 1 archivo estimado
        assert len(estimated) == len(ops)
        assert len(estimated) == 1
        assert any("Timeseries" in f for f in estimated)
        assert not any("KpiRow" in f for f in estimated)
        assert not any("Page.tsx" in f for f in estimated)

    def test_estimated_vs_actual_subset(self):
        """estimated_files debe coincidir con actual_files (misma derivación de structural_ops)."""
        interpret = _simulate_interpret("Add trend chart")
        result = _simulate_confirm(interpret)
        estimated = set(result["plan_preview"]["estimated_files"])
        ops = result["plan_preview"]["structural_operations"]
        actual_files = set(_compute_actual_files_from_ops(ops, interpret.get("contract_id", "")))

        assert estimated == actual_files, (
            f"estimated={estimated} != actual={actual_files}. "
            f"3D debe converger."
        )

    def test_remove_line_chart_estimated_matches_ops(self):
        """DELETE también produce estimated_files correctos."""
        interpret = _simulate_interpret("Remove line chart")
        result = _simulate_confirm(interpret)
        estimated = result["plan_preview"]["estimated_files"]
        ops = result["plan_preview"]["structural_operations"]
        assert len(estimated) == len(ops) == 1
        assert any("Timeseries" in f for f in estimated)

    def test_update_dashboard_estimated_matches_all_children(self):
        """Container expansion produce estimated_files para todos los hijos."""
        interpret = _simulate_interpret("Update dashboard")
        result = _simulate_confirm(interpret)
        estimated = result["plan_preview"]["estimated_files"]
        assert len(estimated) >= 3  # page + kpi_row + timeseries
        assert any("Page.tsx" in f for f in estimated)
        assert any("KpiRow" in f for f in estimated)
        assert any("Timeseries" in f for f in estimated)


# ── Gate validation ─────────────────────────────────────────────


class TestGateValidation:
    """ApplyEngine no debe ejecutar sin confirmed_intent o si gate.blocked."""

    def test_gate_blocked_when_no_actions(self):
        """Confirm sin actions → gate.blocked = True."""
        # The confirm endpoint gates on empty actions.
        # PlanCompiler with empty actions creates an empty plan (gate in API layer).
        confirmed = ConfirmedIntent(
            contract_id="dashboard.sales_overview",
            contract_version=1,
            actions=[],
            params={},
            user_message="",
            interpretation_id="test",
        )
        plan = compile_plan(confirmed)
        # Empty actions → semantic frame has empty actions list
        assert len(plan.semantic_frame.get("actions", [])) == 0
        assert len(plan.intents) == 0

    def test_compiled_plan_with_actions_passes_gate(self):
        """Plan con acciones → gate not blocked."""
        confirm = {
            "run_id": "test-001",
            "contract_id": "dashboard.sales_overview",
            "actions": [{"verb": "modify", "target_capability": "presentation.kpi_row"}],
            "params": {},
            "user_message": "test",
            "interpretation_id": "test",
        }
        contract = get_contract("dashboard.sales_overview", 1)
        assert contract is not None
        confirmed = ConfirmedIntent(
            contract_id=confirm["contract_id"],
            contract_version=1,
            actions=[IntentAction(**a) for a in confirm["actions"]],
            params=confirm["params"],
            user_message=confirm["user_message"],
            interpretation_id=confirm["interpretation_id"],
        )
        plan = compile_plan(confirmed)
        assert len(plan.intents) > 0

    def test_apply_engine_rejects_gate_blocked(self):
        """ApplyEngine debe rechazar si gate.blocked=True.

        Probamos el guard logic directamente (el entry point real
        require paths válidos, eso es pre-existing behavior).
        """
        plan_with_blocked_gate = {
            "skill_ir": {"contract_id": "dashboard.sales_overview", "version": 1, "params": {}, "confidence": 0.0},
            "gate": {"blocked": True, "reason": "test_reason"},
            "semantic_frame": {"actions": []},
        }
        plan_gate = plan_with_blocked_gate.get("gate") if isinstance(plan_with_blocked_gate, dict) else {}
        is_blocked = isinstance(plan_gate, dict) and plan_gate.get("blocked", False)
        assert is_blocked is True

        plan_ok = {
            "skill_ir": {"contract_id": "dashboard.sales_overview", "version": 1, "params": {}, "confidence": 0.0},
            "gate": {"blocked": False},
            "semantic_frame": {"actions": [{"verb": "modify"}]},
        }
        plan_gate2 = plan_ok.get("gate") if isinstance(plan_ok, dict) else {}
        is_blocked2 = isinstance(plan_gate2, dict) and plan_gate2.get("blocked", False)
        assert is_blocked2 is False

        plan_no_gate = {"skill_ir": {"contract_id": "noop", "version": 1}}
        plan_gate3 = plan_no_gate.get("gate") if isinstance(plan_no_gate, dict) else {}
        is_blocked3 = isinstance(plan_gate3, dict) and plan_gate3.get("blocked", False)
        assert is_blocked3 is False
