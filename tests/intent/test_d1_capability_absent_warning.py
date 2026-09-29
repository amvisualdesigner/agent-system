"""D1 — warning determinista de capability ausente en interpret.

_validate_output debe exponer un warning observable cuando una acción
MODIFY/REMOVE referencia una capability que no está presente en el
RepositoryContext, sin mutar la acción, sin crear capabilities, sin
re-seleccionar target y sin bloquear: RepositoryValidation sigue siendo
la autoridad posterior.
"""

from __future__ import annotations

from copy import deepcopy

from app.intent.interpreter import _validate_output

_CATALOG = {
    "capabilities": [
        {"id": "presentation.kpi_row", "verbs": {"modify": {}, "remove": {}}},
        {"id": "presentation.filter_panel", "verbs": {"modify": {}, "remove": {}}},
    ]
}


def _caps(present: list[str] | None = None) -> list[dict]:
    present = set(present or [])
    return [
        {"id": "presentation.kpi_row", "present": "presentation.kpi_row" in present, "paths": []},
        {"id": "presentation.filter_panel", "present": "presentation.filter_panel" in present, "paths": []},
    ]


class TestCapabilityAbsentWarning:
    def test_present_capability_no_warning(self):
        action = {"verb": "modify", "target_capability": "presentation.filter_panel"}
        raw = {"actions": [deepcopy(action)]}
        warnings, clarification = _validate_output(raw, _CATALOG, _caps(["presentation.filter_panel"]))
        assert warnings == []
        assert clarification is None

    def test_absent_capability_modify_warns(self):
        raw = {"actions": [{"verb": "modify", "target_capability": "presentation.filter_panel"}]}
        warnings, _ = _validate_output(raw, _CATALOG, _caps())
        assert any(
            "presentation.filter_panel" in w and "not present" in w
            for w in warnings
        )

    def test_absent_capability_remove_and_delete_warn(self):
        for verb in ("remove", "delete"):
            raw = {"actions": [{"verb": verb, "target_capability": "presentation.filter_panel"}]}
            warnings, _ = _validate_output(raw, _CATALOG, _caps())
            assert any("presentation.filter_panel" in w for w in warnings), verb

    def test_warning_does_not_mutate_action(self):
        action = {"verb": "modify", "target_capability": "presentation.filter_panel"}
        raw = {"actions": [deepcopy(action)]}
        _validate_output(raw, _CATALOG, _caps())
        assert raw["actions"] == [action]

    def test_warning_does_not_create_capability(self):
        raw = {"actions": [{"verb": "modify", "target_capability": "presentation.filter_panel"}]}
        before = len(raw["actions"])
        _validate_output(raw, _CATALOG, _caps())
        assert len(raw["actions"]) == before
        assert all(a.get("verb") != "create" for a in raw["actions"])

    def test_warning_does_not_reselect_target(self):
        raw = {"actions": [{"verb": "modify", "target_capability": "presentation.filter_panel"}]}
        _validate_output(raw, _CATALOG, _caps())
        assert raw["actions"][0]["target_capability"] == "presentation.filter_panel"

    def test_repository_validation_stays_authority(self):
        # El warning es no-bloqueante (no fuerza clarificación) y no decide:
        # la acción se conserva intacta para que la validación del repo decida.
        raw = {"actions": [{"verb": "modify", "target_capability": "presentation.filter_panel"}]}
        warnings, clarification = _validate_output(raw, _CATALOG, _caps())
        assert warnings
        assert clarification is None
        assert raw["actions"] == [{"verb": "modify", "target_capability": "presentation.filter_panel"}]