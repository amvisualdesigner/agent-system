"""Fase 6.2 — lowering determinista del source confirmado (unit).

Cubre la frontera pura `app.binding.lower.lower_confirmed_page_source()`, la
re-hidratacion del hook del registry en `_infer_datasource_ir`, la precedencia
del backend (hook_name/hook_import > registry > mapa) y la delegacion de
`required_materialization` al backend.

Casos obligatorios (T1-T6):
  T1 identity   hook confirmado == hook registry  -> conservar registry, sin probe
  T2 override   hook confirmado != registry       -> DataSourceIR hook + import + slices
  T3 missing    hook no exportado bajo frontend/src -> SourceLoweringConflict missing_hook
  T4 ambiguous  hook exportado por >1 modulo      -> SourceLoweringConflict ambiguous_hook
  T5 multiple   >1 hooks confirmados distintos    -> SourceLoweringConflict multiple_hooks
  T6 kinds      slice/service/symbol/query        -> SourceLoweringConflict unsupported_kind
                  (nunca cae al registry, incluso con hooks validos presentes)
"""

from __future__ import annotations

import pytest

from app.binding.lower import (
    SOURCE_LOWERING_CONFLICT,
    SourceDriftItem,
    SourceLoweringConflict,
    lower_confirmed_page_source,
    probe_hook_modules,
)
from app.binding.materialize import (
    DriftReport,
    MaterializationRequirements,
    required_materialization,
)
from app.binding.models import ResolvedBindings
from app.graphir.backends.react_backend import ReactBackend
from app.intent.models import DataBinding, DataMappingEntry, DataSourceRef
from app.signature.prop_mapper import (
    DataSlice,
    DataSourceIR,
    _infer_datasource_ir,
)


def _binding(kind: str, ref: str, prop: str = "filters") -> DataBinding:
    return DataBinding(
        source=DataSourceRef(kind=kind, ref=ref),
        mapping=(
            DataMappingEntry(prop=prop, from_field=f"_pageData.{prop}", transform="identity"),
        ),
    )


def _registered_ds(hook: str = "useDashboardData") -> DataSourceIR:
    return DataSourceIR(
        type="dashboard_data",
        selector=None,
        slices=(
            DataSlice(component="FilterPanel", target_prop="filters", selector="filters"),
        ),
        hook_name=hook,
        hook_import=f"import {{ {hook} }} from '@/{hook.lower()}'",
    )


def _resolved(page_ds: DataSourceIR | None) -> ResolvedBindings:
    return ResolvedBindings(
        component_props={"presentation.filter_panel": {"filters": "JSVariable"}},
        page_data_source=page_ds,
    )


# ── T1: identidad ──────────────────────────────────────────────────────────


class TestIdentity:
    def test_identity_keeps_registry_representation(self):
        registered = _registered_ds("useDashboardData")
        res = _resolved(registered)
        confirmed = {"presentation.filter_panel": _binding("hook", "useDashboardData")}
        result = lower_confirmed_page_source(
            confirmed, res, {"frontend/src/hooks/useDashboardData.ts": "export function useDashboardData(){}"}
        )
        assert result.ir is None, "identity must keep the registry DataSourceIR"
        assert result.drift == ()

    def test_identity_skips_the_import_probe(self, monkeypatch):
        """Igual que el registry: no hay nada que resolver; el probe no corre."""
        def boom(*_a, **_k):
            raise AssertionError("import probe must NOT run on registry identity")
        monkeypatch.setattr(
            "app.binding.lower.probe_hook_modules", boom
        )
        confirmed = {"presentation.filter_panel": _binding("hook", "useDashboardData")}
        result = lower_confirmed_page_source(
            confirmed, _resolved(_registered_ds()), {},
        )
        assert result.ir is None


# ── T2: override confirmado ────────────────────────────────────────────────


class TestConfirmedOverride:
    def test_override_produces_hook_source_with_registry_slices(self):
        registered = _registered_ds("useDashboardData")
        res = _resolved(registered)
        confirmed = {"presentation.filter_panel": _binding("hook", "useSalesData")}
        result = lower_confirmed_page_source(
            confirmed,
            res,
            {
                "frontend/src/hooks/useSalesData.ts": (
                    "export function useSalesData() { return { filters: [] }; }"
                ),
            },
        )
        assert result.ir is not None
        assert result.ir.type == "hook"
        assert result.ir.hook_name == "useSalesData"
        assert result.ir.hook_import == "import { useSalesData } from '@/hooks/useSalesData'"
        assert result.ir.slices == registered.slices
        assert result.ir.selector == registered.selector

    def test_override_reports_source_drift(self):
        res = _resolved(_registered_ds("useDashboardData"))
        confirmed = {
            "presentation.filter_panel": _binding("hook", "useSalesData"),
            "presentation.kpi_row": _binding("hook", "useSalesData", prop="data"),
        }
        result = lower_confirmed_page_source(
            confirmed,
            res,
            {"frontend/src/hooks/useSalesData.ts": "export function useSalesData(){}"},
        )
        assert len(result.drift) == 2
        item = result.drift[0]
        assert isinstance(item, SourceDriftItem)
        assert item.confirmed == "useSalesData"
        assert item.registry == "useDashboardData"
        assert item.reason == "confirmed_source_differs"
        d = item.to_dict()
        assert d["component"] == "presentation.filter_panel"
        assert d["source"] == {"confirmed": "useSalesData", "registry": "useDashboardData"}

    def test_override_without_registry_page_source(self):
        confirmed = {"presentation.filter_panel": _binding("hook", "useSalesData")}
        result = lower_confirmed_page_source(
            confirmed,
            None,
            {"frontend/src/hooks/useSalesData.ts": "export function useSalesData(){}"},
        )
        assert result.ir is not None
        assert result.ir.slices == ()
        assert result.ir.selector is None
        assert result.drift[0].registry is None


# ── T3: hook ausente ───────────────────────────────────────────────────────


class TestMissingHook:
    def test_no_module_exports_hook_is_conflict(self):
        confirmed = {"presentation.filter_panel": _binding("hook", "useSalesData")}
        with pytest.raises(SourceLoweringConflict) as exc:
            lower_confirmed_page_source(
                confirmed, _resolved(_registered_ds()), {},
            )
        assert exc.value.reason == "missing_hook"
        assert exc.value.hook == "useSalesData"

    def test_module_exists_but_does_not_export_hook_is_conflict(self):
        confirmed = {"presentation.filter_panel": _binding("hook", "useSalesData")}
        with pytest.raises(SourceLoweringConflict) as exc:
            lower_confirmed_page_source(
                confirmed,
                _resolved(_registered_ds()),
                {
                    "frontend/src/hooks/useSalesData.ts": (
                        "export function useOtherData() {}"
                    ),
                },
            )
        assert exc.value.reason == "missing_hook"

    def test_hook_exported_in_a_non_probe_path_is_not_evidence(self):
        """Solo export exacto bajo frontend/src cuenta como evidencia."""
        confirmed = {"presentation.filter_panel": _binding("hook", "useSalesData")}
        with pytest.raises(SourceLoweringConflict):
            lower_confirmed_page_source(
                confirmed,
                _resolved(_registered_ds()),
                {"src/lib/useSalesData.ts": "export function useSalesData(){}"},
            )


# ── T4: hook ambiguo ───────────────────────────────────────────────────────


class TestAmbiguousHook:
    def test_two_modules_exporting_the_symbol_is_conflict(self):
        confirmed = {"presentation.filter_panel": _binding("hook", "useSalesData")}
        with pytest.raises(SourceLoweringConflict) as exc:
            lower_confirmed_page_source(
                confirmed,
                _resolved(_registered_ds()),
                {
                    "frontend/src/hooks/useSalesData.ts": "export function useSalesData(){}",
                    "frontend/src/lib/sales.ts": "export const useSalesData = () => ({});",
                },
            )
        assert exc.value.reason == "ambiguous_hook"
        assert len(exc.value.detail.split("modules")) >= 1
        # detail debe enumerar los modulos candidatos
        assert "hooks/useSalesData" in exc.value.detail
        assert "lib/sales" in exc.value.detail

    def test_probe_returns_sorted_unique_modules(self):
        files = {
            "frontend/src/hooks/useSalesData.ts": "export function useSalesData(){}",
            "frontend/src/hooks/useSalesData.tsx": "export function useSalesData(){}",
        }
        # Mismo stem: un solo modulo, no ambiguo.
        assert probe_hook_modules("useSalesData", files) == ["hooks/useSalesData"]


# ── T5: multiples hooks ────────────────────────────────────────────────────


class TestMultipleHooks:
    def test_distinct_confirmed_hooks_are_conflict(self):
        confirmed = {
            "presentation.filter_panel": _binding("hook", "useSalesData"),
            "presentation.kpi_row": _binding("hook", "useOtherData", prop="data"),
        }
        with pytest.raises(SourceLoweringConflict) as exc:
            lower_confirmed_page_source(
                confirmed,
                _resolved(_registered_ds()),
                {
                    "frontend/src/hooks/useSalesData.ts": "export function useSalesData(){}",
                    "frontend/src/hooks/useOtherData.ts": "export function useOtherData(){}",
                },
            )
        assert exc.value.reason == "multiple_hooks"
        # Un Page tiene UN solo _pageData root (invariante I4).
        assert "multiple" in exc.value.reason


# ── T6: kinds no soportados (slice/service/symbol/query) ───────────────────


class TestUnsupportedKinds:
    @pytest.mark.parametrize("kind", ["slice", "service", "symbol", "query"])
    def test_non_hook_kinds_are_conflict_never_registry_fallback(self, kind):
        confirmed = {"presentation.filter_panel": _binding(kind, "SALES.byRegion.tableName")}
        with pytest.raises(SourceLoweringConflict) as exc:
            lower_confirmed_page_source(
                confirmed,
                _resolved(_registered_ds()),
                {"frontend/src/hooks/useSalesData.ts": "export function useSalesData(){}"},
            )
        assert exc.value.reason == "unsupported_kind"

    def test_unsupported_kind_wins_even_when_a_valid_hook_coexists(self):
        confirmed = {
            "presentation.filter_panel": _binding("slice", "SALES.byRegion.tableName"),
            "presentation.kpi_row": _binding("hook", "useSalesData", prop="data"),
        }
        with pytest.raises(SourceLoweringConflict) as exc:
            lower_confirmed_page_source(
                confirmed,
                _resolved(_registered_ds()),
                {"frontend/src/hooks/useSalesData.ts": "export function useSalesData(){}"},
            )
        assert exc.value.reason == "unsupported_kind"
        assert exc.value.hook == "SALES.byRegion.tableName"

    def test_malformed_empty_hook_ref_is_unsupported(self):
        confirmed = {"presentation.filter_panel": _binding("hook", "   ")}
        with pytest.raises(SourceLoweringConflict) as exc:
            lower_confirmed_page_source(confirmed, _resolved(_registered_ds()), {})
        assert exc.value.reason == "unsupported_kind"


# ── Registry `hook` re-hidratado en _infer_datasource_ir ───────────────────


class TestHookFromDataAccess:
    def test_infer_preserves_hook_name_and_import(self):
        raw = {
            "type": "dashboard_data",
            "hook": {"name": "useDashboardData", "import": "@/hooks/useDashboardData"},
            "slices": [{"component": "KpiRow", "targetProp": "data", "selector": "kpiData"}],
        }
        ir = _infer_datasource_ir(raw)
        assert ir.hook_name == "useDashboardData"
        assert ir.hook_import == "import { useDashboardData } from '@/hooks/useDashboardData'"

    def test_infer_keeps_full_import_statement_as_is(self):
        raw = {
            "type": "dashboard_data",
            "hook": {
                "name": "useSales",
                "import": "import { useSales } from '@/sales/hook'",
            },
        }
        ir = _infer_datasource_ir(raw)
        assert ir.hook_import == "import { useSales } from '@/sales/hook'"

    def test_infer_without_hook_keeps_fields_none(self):
        ir = _infer_datasource_ir({"type": "raw_selector", "selector": "store.x"})
        assert ir.hook_name is None
        assert ir.hook_import is None


# ── Precedencia backend: hook_name/hook_import > registry > mapa ─────────────


class TestBackendPrecedence:
    def test_declaration_prefers_explicit_hook_name(self):
        ds = DataSourceIR(type="dashboard_data", hook_name="useSalesData")
        assert ReactBackend._page_hook_declaration(ds) == "const _pageData = useSalesData();"

    def test_import_prefers_explicit_hook_import(self):
        ds = DataSourceIR(
            type="dashboard_data",
            hook_name="useSalesData",
            hook_import="import { useSalesData } from '@/hooks/useSalesData'",
        )
        assert (
            ReactBackend._page_hook_import(ds)
            == "import { useSalesData } from '@/hooks/useSalesData'"
        )

    def test_registry_ir_falls_back_to_static_map(self):
        ds = DataSourceIR(type="dashboard_data", hook_name="useDashboardData")
        assert ReactBackend._page_hook_declaration(ds) == "const _pageData = useDashboardData();"
        assert (
            ReactBackend._page_hook_import(ds)
            == "import { useDashboardData } from '@/hooks/useDashboardData'"
        )

    def test_unmapped_source_has_no_hook(self):
        ds = DataSourceIR(type="parquet")
        assert ReactBackend._page_hook_declaration(ds) is None
        assert ReactBackend._page_hook_import(ds) is None


# ── required_materialization delega en el backend ──────────────────────────


class TestRequiredMaterializationDelegates:
    def test_uses_explicit_hook_from_lowered_ir(self):
        confirmed = {"presentation.filter_panel": _binding("hook", "useSalesData")}
        materialized = ResolvedBindings(
            component_props={},
            page_data_source=DataSourceIR(
                type="hook",
                hook_name="useSalesData",
                hook_import="import { useSalesData } from '@/hooks/useSalesData'",
                slices=(DataSlice(component="FilterPanel", target_prop="filters", selector="filters"),),
            ),
        )
        req: MaterializationRequirements = required_materialization(materialized, confirmed)
        assert req.missing == ()
        assert req.declaration == "const _pageData = useSalesData();"
        assert req.imports == ("import { useSalesData } from '@/hooks/useSalesData'",)
        assert "useDashboardData" not in " ".join(req.imports)


# ── Informe de drift combinado (props + source) ────────────────────────────


class TestDriftReportWithSources:
    def test_has_drift_and_serialization_cover_sources(self):
        report = DriftReport(
            sources=(
                SourceDriftItem(
                    component="presentation.filter_panel",
                    confirmed="useSalesData",
                    registry="useDashboardData",
                ),
            ),
        )
        assert report.has_drift
        assert report.to_dict() == [
            {
                "component": "presentation.filter_panel",
                "source": {"confirmed": "useSalesData", "registry": "useDashboardData"},
            },
        ]
        assert report.describe()[0] == (
            "presentation.filter_panel: confirmed source 'useSalesData' "
            "overrides registry 'useDashboardData'"
        )


def test_conflict_constant_and_reason_shape():
    try:
        lower_confirmed_page_source(
            {"presentation.filter_panel": _binding("hook", "useGhost")},
            _resolved(_registered_ds()),
            {},
        )
        raise AssertionError("expected SourceLoweringConflict")
    except SourceLoweringConflict as e:
        assert e.reason in ("unsupported_kind", "missing_hook", "ambiguous_hook", "multiple_hooks")
        assert e.components == ["presentation.filter_panel"]
        assert e.hook == "useGhost"
        assert SOURCE_LOWERING_CONFLICT == "source_lowering_conflict"


# ── Retarget capability → node.type (frontera del lowering fisico) ─────────


class TestRetargetToComponentTypes:
    """Frontera: SOLO matricula la capability al node.type fisico del GraphIR.

    No selecciona capability, target, binding ni reinterpreta el WHAT; los
    DataBinding se trasladan intactos. Tampoco deja el binding INERTE: sin
    node.type fisico o con colision de nodos es CONFLICT, no caida silenciosa
    al registry.
    """

    def test_translates_capability_to_component_type(self):
        from app.binding.lower import retarget_confirmed_to_component_types

        confirmed = {"presentation.kpi_row": _binding("hook", "useSalesData")}
        out = retarget_confirmed_to_component_types(
            confirmed, {"presentation.kpi_row": "KpiRow"},
        )
        assert list(out) == ["KpiRow"]

    def test_binding_is_preserved_untouched_not_reinterpreted(self):
        """El WHAT no se reinterpreta: misma identidad, mismo mapping, mismo source."""
        from app.binding.lower import retarget_confirmed_to_component_types

        binding = _binding("hook", "useSalesData", prop="metrics")
        out = retarget_confirmed_to_component_types(
            {"presentation.kpi_row": binding}, {"presentation.kpi_row": "KpiRow"},
        )
        assert out["KpiRow"] is binding
        assert out["KpiRow"].source is binding.source
        assert out["KpiRow"].mapping == binding.mapping

    def test_only_physical_keys_change_nothing_is_selected_or_dropped(self):
        from app.binding.lower import retarget_confirmed_to_component_types

        confirmed = {
            "presentation.kpi_row": _binding("hook", "useSalesData", prop="metrics"),
            "layout.page": "NON_BINDING",  # sin binding: nunca llega al mapa
        }
        out = retarget_confirmed_to_component_types(
            {"presentation.kpi_row": confirmed["presentation.kpi_row"]},
            {"presentation.kpi_row": "KpiRow"},
        )
        assert sorted(out) == ["KpiRow"]
        assert len(out) == 1  # no se anade ninguna capability

    def test_unmapped_capability_is_conflict_not_inert_binding(self):
        """Sin node.type fisico -> CONFLICT missing_target (el registry no gana)."""
        from app.binding.lower import (
            SourceLoweringConflict,
            retarget_confirmed_to_component_types,
        )

        with pytest.raises(SourceLoweringConflict) as exc:
            retarget_confirmed_to_component_types(
                {"presentation.kpi_row": _binding("hook", "useSalesData")}, {},
            )
        assert exc.value.reason == "missing_target"
        assert exc.value.hook is None
        assert "presentation.kpi_row" in exc.value.detail
        assert exc.value.components == ["presentation.kpi_row"]

    def test_collision_on_same_node_type_is_conflict(self):
        """Dos capabilities confirmadas colisionando -> CONFLICT ambiguous_target."""
        from app.binding.lower import (
            SourceLoweringConflict,
            retarget_confirmed_to_component_types,
        )

        with pytest.raises(SourceLoweringConflict) as exc:
            retarget_confirmed_to_component_types(
                {
                    "presentation.kpi_row": _binding("hook", "useSalesData", prop="metrics"),
                    "presentation.kpi_row_alias": _binding("hook", "useOther", prop="data"),
                },
                {
                    "presentation.kpi_row": "KpiRow",
                    "presentation.kpi_row_alias": "KpiRow",
                },
            )
        assert exc.value.reason == "ambiguous_target"
        assert sorted(exc.value.components) == [
            "presentation.kpi_row", "presentation.kpi_row_alias",
        ]

    def test_empty_confirmed_is_noop(self):
        from app.binding.lower import retarget_confirmed_to_component_types

        assert retarget_confirmed_to_component_types({}, {"a": "A"}) == {}

    def test_graph_intent_capability_builds_the_map(self):
        from app.binding.lower import retarget_confirmed_to_component_types

        graph_nodes = {
            "KpiRow:0": type("N", (), {
                "metadata": {"intent_capability": "presentation.kpi_row"},
                "type": "KpiRow",
            })(),
            "Page:0": type("N", (), {
                "metadata": {"intent_capability": "layout.page"},
                "type": "Page",
            })(),
        }
        cap_to_type = {
            n.metadata["intent_capability"]: n.type
            for n in graph_nodes.values()
            if n.metadata.get("intent_capability")
        }
        out = retarget_confirmed_to_component_types(
            {"presentation.kpi_row": _binding("hook", "useSalesData")},
            cap_to_type,
        )
        assert list(out) == ["KpiRow"]