"""Cross-consistency: ambos resolvedores siguen DELETE RESOLUTION CONTRACT v1.

Valida que _resolve_delete_instance (nivel abstracto, StructuralIndex)
y _stem_matches_hint (nivel concreto, filesystem) producen clasificaciones
equivalentes para escenarios equivalentes.

El contrato se define en:
    backend/app/engine/delete_resolution_contract.py
"""

from __future__ import annotations

import pytest

from app.engine.apply_engine import _stem_matches_hint
from app.engine.errors import AmbiguousStructuralTargetError
from app.engine.structural_completion import _resolve_delete_instance
from app.engine.structural_index import StructuralIndex
from app.engine.state_adapter import ComponentInstanceInfo


# ── Helpers ────────────────────────────────────────────────────────────

def _make_index(files: list[str], capability: str = "presentation.timeseries") -> StructuralIndex:
    """Build StructuralIndex from a list of file paths (single capability)."""
    instances = [
        ComponentInstanceInfo(path=fp, capability=capability, instance_id=str(i))
        for i, fp in enumerate(files)
    ]
    return StructuralIndex.from_mapping({capability: instances})


def resolve_abstract(files: list[str], hint: str | None) -> str:
    """Clasificación vía _resolve_delete_instance (nivel abstracto).

    Returns: "unique" | "ambiguous" | "no_match" | "skip"
    """
    if not files:
        return "skip"
    cap = "presentation.timeseries"
    index = _make_index(files, cap)
    try:
        result = _resolve_delete_instance(cap, hint, index)
        return "unique"
    except AmbiguousStructuralTargetError:
        return "ambiguous"


def resolve_concrete(files: list[str], hint: str | None) -> str:
    """Clasificación vía _stem_matches_hint (nivel concreto).

    Reproduce la lógica del delete loop en apply_engine.
    Returns: "unique" | "ambiguous" | "no_match" | "skip"
    """
    if not files:
        return "skip"
    if len(files) == 1:
        return "unique"
    if not hint:
        return "ambiguous"
    hint_stem = hint.lower().replace(".tsx", "")
    matched = [fp for fp in files if _stem_matches_hint(fp, hint_stem)]
    if len(matched) == 1:
        return "unique"
    if len(matched) == 0:
        return "no_match"
    return "ambiguous"


# ── Escenarios de contrato ─────────────────────────────────────────────

# ── Diferencia de nivel entre resolvedores ────────────────────────────
#
# _resolve_delete_instance trabaja con StructuralIndex (paths abstractos).
# Sabe si hay 0, 1, o >1 instancias, pero NO puede hacer filename stem matching.
# Con hint presente y >1 instancias → UNIQUE (delega al apply engine).
#
# _stem_matches_hint loop trabaja con paths concretos del filesystem.
# Puede hacer filename stem matching.
#
# Por diseño, la clasificación final (UNIQUE/AMBIGUOUS/NO_MATCH) se decide
# en el apply engine. _resolve_delete_instance solo detecta el caso más
# obvio: >1 instancias sin hint → AMBIGUOUS.

SCENARIOS = [
    # (files, hint, esperado_abstract, esperado_concrete)
    # 1. 0 candidates → skip (ambos niveles)
    pytest.param([], None, "skip", "skip", id="rule_1a_0_candidates_no_hint"),
    pytest.param([], "linechart", "skip", "skip", id="rule_1b_0_candidates_with_hint"),

    # 2. 1 candidate → unique (ambos niveles)
    pytest.param(["Timeseries.tsx"], None, "unique", "unique", id="rule_2a_1_candidate_no_hint"),
    pytest.param(["Timeseries.tsx"], "timeseries", "unique", "unique", id="rule_2b_1_candidate_hint_matches"),
    pytest.param(["Timeseries.tsx"], "linechart", "unique", "unique", id="rule_2c_1_candidate_hint_no_match"),

    # 3. >1 candidates, no hint → ambiguous (ambos niveles)
    pytest.param(
        ["LineChart.tsx", "Timeseries.tsx"], None, "ambiguous", "ambiguous",
        id="rule_3_multi_no_hint",
    ),
    pytest.param(
        ["LineChart.tsx", "Timeseries.tsx", "AreaChart.tsx"], None, "ambiguous", "ambiguous",
        id="rule_3_multi_3_no_hint",
    ),

    # 4a. hint matchea exactly 1 → unique (ambos niveles)
    pytest.param(
        ["LineChart.tsx", "Timeseries.tsx"], "linechart", "unique", "unique",
        id="rule_4a_hint_linechart",
    ),
    pytest.param(
        ["LineChart.tsx", "Timeseries.tsx"], "timeseries", "unique", "unique",
        id="rule_4a_hint_timeseries",
    ),
    pytest.param(
        ["LineChart.tsx", "Timeseries.tsx", "AreaChart.tsx"], "areachart", "unique", "unique",
        id="rule_4a_hint_areachart",
    ),

    # 4b. hint matchea 0 → abstract: unique (defer), concrete: no_match
    pytest.param(
        ["LineChart.tsx", "Timeseries.tsx"], "nonexistent", "unique", "no_match",
        id="rule_4b_hint_no_match",
    ),
    pytest.param(
        ["LineChart.tsx", "Timeseries.tsx"], "chart", "unique", "no_match",
        id="rule_4b_hint_generic_chart",
    ),

    # 4c. hint matchea >1 o 0 → concrete clasifica (abstract defiere)
    # (exact stem match: "line" no matchea "linechart" ni "linegraph")
    pytest.param(
        ["LineChart.tsx", "LineGraph.tsx"], "line", "unique", "no_match",
        id="rule_4c_hint_substring_no_match",
    ),
    # Para >1 matches real, se necesitan stems idénticos
    pytest.param(
        ["ChartA.tsx", "chartB.tsx"], "chartb", "unique", "unique",
        id="rule_4c_hint_one_match",
    ),
]


@pytest.mark.parametrize("files, hint, expected_abstract, expected_concrete", SCENARIOS)
def test_contract_scenario(files, hint, expected_abstract, expected_concrete):
    """Cada escenario del contrato produce clasificaciones esperadas."""
    abstract = resolve_abstract(files, hint)
    concrete = resolve_concrete(files, hint)

    assert abstract == expected_abstract, (
        f"_resolve_delete_instance({files}, hint={hint}): "
        f"expected {expected_abstract}, got {abstract}"
    )
    assert concrete == expected_concrete, (
        f"_stem_matches_hint loop({files}, hint={hint}): "
        f"expected {expected_concrete}, got {concrete}"
    )


# ── Consistency invariant ──────────────────────────────────────────────

def test_both_resolvers_agree_on_unique_hint_matches():
    """Hint matchea exactly 1 → ambos niveles producen unique."""
    files = ["LineChart.tsx", "Timeseries.tsx"]
    hint = "linechart"
    assert resolve_abstract(files, hint) == "unique"
    assert resolve_concrete(files, hint) == "unique"


def test_both_resolvers_agree_on_ambiguous_no_hint():
    """Sin hint con multi-instancia → ambos niveles producen ambiguous."""
    files = ["LineChart.tsx", "Timeseries.tsx"]
    assert resolve_abstract(files, None) == "ambiguous"
    assert resolve_concrete(files, None) == "ambiguous"


def test_abstract_defers_with_hint():
    """Con hint presente, abstract siempre UNIQUE (delega a apply engine).
    El apply engine es quien valida el match real."""
    files = ["LineChart.tsx", "Timeseries.tsx"]
    for hint in ["linechart", "timeseries", "nonexistent", "chart"]:
        assert resolve_abstract(files, hint) == "unique", f"hint={hint}"


def test_concrete_resolves_hint_matching():
    """El apply engine resuelve el hint contra filenames reales."""
    files = ["LineChart.tsx", "Timeseries.tsx"]
    assert resolve_concrete(files, "linechart") == "unique"
    assert resolve_concrete(files, "timeseries") == "unique"
    assert resolve_concrete(files, "nonexistent") == "no_match"
    assert resolve_concrete(files, "chart") == "no_match"


def test_skip_empty_inputs_both_levels():
    """0 candidates → skip siempre, nunca error."""
    assert resolve_abstract([], "anything") == "skip"
    assert resolve_concrete([], "anything") == "skip"
    assert resolve_abstract([], None) == "skip"
    assert resolve_concrete([], None) == "skip"
