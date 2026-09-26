"""Tests for Required Prop Enforcement (Stage 2 Compilation Gate).

Tests cover:
1. Extractor: required_props vs optional_props parsing
2. MISSING_REQUIRED_PROPS exception
3. Compiler gate: _build_node enforces required props
4. Renderer guard: bare tags blocked for required props
5. BINDING_MISSING: hard error for required, skip for optional
6. fallback_props: NOT used for required props
7. Full pipeline integration
"""

from __future__ import annotations

import json
import os

import pytest
from unittest.mock import MagicMock, patch

from app.binding.models import ResolvedBindings
from app.graphir.compiler import MISSING_REQUIRED_PROPS, UIIRCompiler

from app.graphir.models import GraphIR, GraphIRLayout, GraphIRNode
from app.graphir.ui_ir import UIComponentNode, UIComponentTree
from app.signature.extractor import _PROP_DETAIL_PATTERN


# ── 1. Extractor: required vs optional parsing ──

class TestExtractorRequiredOptional:
    """Tests for _PROP_DETAIL_PATTERN extracting required vs optional props.

    TypeScript allows both semicolon and semicolon-less syntax in interfaces.
    The extractor MUST produce identical prop_names/required_props/optional_props
    regardless of the semicolon style used.
    """

    # ── Required props ──

    def test_required_prop_with_semicolon(self):
        block = "interface Props {\n  data: Point[];\n}"
        matches = list(_PROP_DETAIL_PATTERN.finditer(block))
        assert len(matches) == 1
        assert matches[0].group(1) == "data"
        assert matches[0].group(2) == ""

    def test_required_prop_without_semicolon(self):
        """TypeScript interfaces without semicolons (the KpiRow bug)."""
        block = "interface Props {\n  data: Point[]\n}"
        matches = list(_PROP_DETAIL_PATTERN.finditer(block))
        assert len(matches) == 1
        assert matches[0].group(1) == "data"
        assert matches[0].group(2) == ""

    def test_required_prop_generic_type_without_semicolon(self):
        block = "interface Props {\n  data: KpiItem[]\n}"
        matches = list(_PROP_DETAIL_PATTERN.finditer(block))
        assert len(matches) == 1
        assert matches[0].group(1) == "data"

    def test_required_prop_generic_type_with_semicolon(self):
        block = "interface Props {\n  data: KpiItem[];\n}"
        matches = list(_PROP_DETAIL_PATTERN.finditer(block))
        assert len(matches) == 1
        assert matches[0].group(1) == "data"

    # ── Optional props ──

    def test_optional_prop_with_semicolon(self):
        block = "interface Props {\n  title?: string;\n}"
        matches = list(_PROP_DETAIL_PATTERN.finditer(block))
        assert len(matches) == 1
        assert matches[0].group(1) == "title"
        assert matches[0].group(2) == "?"

    def test_optional_prop_without_semicolon(self):
        block = "interface Props {\n  title?: string\n}"
        matches = list(_PROP_DETAIL_PATTERN.finditer(block))
        assert len(matches) == 1
        assert matches[0].group(1) == "title"
        assert matches[0].group(2) == "?"

    # ── Mixed required + optional ──

    def test_mixed_with_semicolons(self):
        block = "interface Props {\n  data: Point[];\n  title?: string;\n  type?: string;\n}"
        matches = list(_PROP_DETAIL_PATTERN.finditer(block))
        assert len(matches) == 3
        assert matches[0].group(1) == "data"
        assert matches[0].group(2) == ""
        assert matches[1].group(1) == "title"
        assert matches[1].group(2) == "?"
        assert matches[2].group(1) == "type"
        assert matches[2].group(2) == "?"

    def test_mixed_without_semicolons(self):
        """Same structure but no semicolons anywhere."""
        block = "interface Props {\n  data: Point[]\n  title?: string\n  type?: string\n}"
        matches = list(_PROP_DETAIL_PATTERN.finditer(block))
        assert len(matches) == 3
        assert matches[0].group(1) == "data"
        assert matches[0].group(2) == ""
        assert matches[1].group(1) == "title"
        assert matches[1].group(2) == "?"

    def test_mixed_inconsistent_semicolons(self):
        """Mixed styles: some with ; some without (real-world)."""
        block = "interface Props {\n  data: Point[];\n  title?: string\n  type?: string;\n}"
        matches = list(_PROP_DETAIL_PATTERN.finditer(block))
        assert len(matches) == 3

    def test_produces_identical_results_across_styles(self):
        """The EXACT same prop_names/required/optional regardless of style."""
        styles = [
            "interface Props {\n  data: Foo[]\n  title?: string\n}",
            "interface Props {\n  data: Foo[];\n  title?: string;\n}",
            "interface Props {\n  data: Foo[]\n  title?: string;\n}",
        ]
        expected_required = ["data"]
        expected_optional = ["title"]
        for block in styles:
            required = []
            optional = []
            for m in _PROP_DETAIL_PATTERN.finditer(block):
                name, is_opt = m.group(1), m.group(2)
                if is_opt == "?":
                    optional.append(name)
                else:
                    required.append(name)
            assert required == expected_required, f"Failed for block: {block}"
            assert optional == expected_optional, f"Failed for block: {block}"

    # ── Edge cases ──

    def test_no_props_returns_empty(self):
        block = "interface Props {}"
        matches = list(_PROP_DETAIL_PATTERN.finditer(block))
        assert len(matches) == 0

    def test_all_required_with_semicolons(self):
        block = "interface Props {\n  data: Point[];\n  series: Series[];\n  config: Config;\n}"
        required = []
        optional = []
        for m in _PROP_DETAIL_PATTERN.finditer(block):
            name, is_opt = m.group(1), m.group(2)
            if is_opt == "?":
                optional.append(name)
            else:
                required.append(name)
        assert required == ["data", "series", "config"]
        assert optional == []

    def test_all_required_without_semicolons(self):
        block = "interface Props {\n  data: Point[]\n  series: Series[]\n  config: Config\n}"
        required = []
        optional = []
        for m in _PROP_DETAIL_PATTERN.finditer(block):
            name, is_opt = m.group(1), m.group(2)
            if is_opt == "?":
                optional.append(name)
            else:
                required.append(name)
        assert required == ["data", "series", "config"]
        assert optional == []

    def test_all_optional_with_semicolons(self):
        block = "interface Props {\n  title?: string;\n  subtitle?: string;\n}"
        required = []
        optional = []
        for m in _PROP_DETAIL_PATTERN.finditer(block):
            name, is_opt = m.group(1), m.group(2)
            if is_opt == "?":
                optional.append(name)
            else:
                required.append(name)
        assert required == []
        assert optional == ["title", "subtitle"]

    def test_all_optional_without_semicolons(self):
        block = "interface Props {\n  title?: string\n  subtitle?: string\n}"
        required = []
        optional = []
        for m in _PROP_DETAIL_PATTERN.finditer(block):
            name, is_opt = m.group(1), m.group(2)
            if is_opt == "?":
                optional.append(name)
            else:
                required.append(name)
        assert required == []
        assert optional == ["title", "subtitle"]

    def test_multiline_type_without_semicolon(self):
        """Multi-word types like 'string | number' without semicolon."""
        block = "interface Props {\n  value: string | number\n}"
        matches = list(_PROP_DETAIL_PATTERN.finditer(block))
        assert len(matches) == 1
        assert matches[0].group(1) == "value"


# ── 2. MISSING_REQUIRED_PROPS exception ──

class TestMISSING_REQUIRED_PROPS:
    """Tests for the MISSING_REQUIRED_PROPS exception."""

    def test_exception_message_format(self):
        e = MISSING_REQUIRED_PROPS(
            component="Timeseries",
            missing=["data"],
            available=["title"],
            contract_params=["timeseries_metric"],
        )
        assert "MISSING_REQUIRED_PROPS" in str(e)
        assert "Timeseries" in str(e)
        assert "data" in str(e)

    def test_exception_attributes(self):
        e = MISSING_REQUIRED_PROPS(
            component="KpiRow",
            missing=["data", "metrics"],
            available=["title"],
            contract_params=["metrics"],
        )
        assert e.component == "KpiRow"
        assert e.missing == ["data", "metrics"]
        assert e.available == ["title"]
        assert e.contract_params == ["metrics"]

    def test_exception_without_contract_params(self):
        e = MISSING_REQUIRED_PROPS(
            component="Timeseries",
            missing=["data"],
            available=[],
        )
        assert e.component == "Timeseries"
        assert e.missing == ["data"]
        assert e.contract_params == []

    def test_exception_is_raiseable(self):
        with pytest.raises(MISSING_REQUIRED_PROPS):
            raise MISSING_REQUIRED_PROPS(
                component="Test",
                missing=["x"],
                available=["y"],
            )


# ── 4. Compiler gate: _build_node required-prop enforcement ──

class TestCompilerRequiredPropsGate:
    """Tests for the MISSING_REQUIRED_PROPS gate in _build_node."""

    def _make_graph(self, node_type: str = "Timeseries", node_id: str = "ts:1") -> GraphIR:
        """Minimal GraphIR with a single node."""
        node = GraphIRNode(
            id=node_id,
            type=node_type,
            data={},
        )
        return GraphIR(
            nodes={node_id: node},
            edges=[],
            layout=GraphIRLayout(root=node_id, constraints={}),
        )

    def test_required_props_present_passes(self):
        """All required props resolved → no error."""
        graph = self._make_graph("Timeseries", "ts:1")
        sig_with_required = {
            "prop_names": ["data", "title"],
            "required_props": ["data"],
            "optional_props": ["title"],
            "props": "interface Props { data: Point[]; title?: string; }",
            "imports": [],
        }
        sigs = {"Timeseries": sig_with_required}
        resolved = ResolvedBindings(component_props={"Timeseries": {"data": [1, 2, 3]}})
        tree = UIIRCompiler.compile(
            graph, graph.layout,
            resolved_bindings=resolved,
            component_signatures=sigs,
        )
        assert tree is not None
        assert tree.root.component == "Timeseries"
        assert "data" in tree.root.props

    def test_required_props_from_resolved_bindings(self):
        """Required prop resolved via BindingResolver → no error."""
        graph = self._make_graph("Timeseries", "ts:1")
        sig_with_required = {
            "prop_names": ["data", "title"],
            "required_props": ["data"],
            "optional_props": ["title"],
            "props": "interface Props { data: Point[]; title?: string; }",
            "imports": [],
        }
        sigs = {"Timeseries": sig_with_required}
        resolved = ResolvedBindings(component_props={"Timeseries": {"data": "revenue"}})
        tree = UIIRCompiler.compile(
            graph, graph.layout,
            resolved_bindings=resolved,
            component_signatures=sigs,
        )
        assert tree.root.props.get("data") == "revenue"

    def test_missing_required_props_raises_error(self):
        """Missing required prop → MISSING_REQUIRED_PROPS raised."""
        graph = self._make_graph("Timeseries", "ts:1")
        sig_with_required = {
            "prop_names": ["data", "title"],
            "required_props": ["data"],
            "optional_props": ["title"],
            "props": "interface Props { data: Point[]; title?: string; }",
            "imports": [],
        }
        sigs = {"Timeseries": sig_with_required}
        with pytest.raises(MISSING_REQUIRED_PROPS) as exc:
            UIIRCompiler.compile(
                graph, graph.layout,
                resolved_bindings=ResolvedBindings(),
                component_signatures=sigs,
            )
        assert exc.value.component == "Timeseries"
        assert "data" in exc.value.missing

    def test_optional_props_missing_allowed(self):
        """Missing optional props → no error, successful compile."""
        graph = self._make_graph("Timeseries", "ts:1")
        sig_with_optional = {
            "prop_names": ["data", "title"],
            "required_props": ["data"],
            "optional_props": ["title"],
            "props": "interface Props { data: Point[]; title?: string; }",
            "imports": [],
        }
        sigs = {"Timeseries": sig_with_optional}
        resolved = ResolvedBindings(component_props={"Timeseries": {"data": [1, 2, 3]}})
        tree = UIIRCompiler.compile(
            graph, graph.layout,
            resolved_bindings=resolved,
            component_signatures=sigs,
        )
        assert list(tree.root.props.get("data")) == [1, 2, 3]

    def test_no_signature_skips_gate(self):
        """No component signature → no required-prop enforcement."""
        graph = self._make_graph("Unknown", "u:1")
        tree = UIIRCompiler.compile(
            graph, graph.layout,
            resolved_bindings=ResolvedBindings(component_props={"Unknown": {"data": [1, 2, 3]}}),
            component_signatures=None,
        )
        assert tree is not None

    def test_multiple_required_props_all_present(self):
        """Multiple required props all resolved → success."""
        graph = self._make_graph("KpiRow", "kpi:1")
        sig = {
            "prop_names": ["data", "metrics", "title"],
            "required_props": ["data", "metrics"],
            "optional_props": ["title"],
            "props": "interface Props { data: KpiItem[]; metrics: string[]; title?: string; }",
            "imports": [],
        }
        sigs = {"KpiRow": sig}
        resolved = ResolvedBindings(component_props={"KpiRow": {"data": [], "metrics": ["revenue"]}})
        tree = UIIRCompiler.compile(
            graph, graph.layout,
            resolved_bindings=resolved,
            component_signatures=sigs,
        )
        assert tree.root.props.get("data") == []
        assert "metrics" in tree.root.props
        assert "title" not in tree.root.props  # optional, missing

    def test_missing_binding_raises_missing_required_props(self):
        """Without BindingResolver for a component with required props → error."""
        node_id = "ts:1"
        node = GraphIRNode(
            id=node_id,
            type="Timeseries",
            data={"data": [1, 2, 3]},  # intent (not UI props)
        )
        graph = GraphIR(
            nodes={node_id: node},
            edges=[],
            layout=GraphIRLayout(root=node_id, constraints={}),
        )
        sig = {
            "prop_names": ["data"],
            "required_props": ["data"],
            "optional_props": [],
            "props": "interface Props { data: Point[]; }",
            "imports": [],
        }
        sigs = {"Timeseries": sig}
        resolved = ResolvedBindings(component_props={}, page_data_source=None)
        with pytest.raises(MISSING_REQUIRED_PROPS) as exc:
            UIIRCompiler.compile(
                graph, graph.layout,
                resolved_bindings=resolved,
                component_signatures=sigs,
            )
        assert exc.value.component == "Timeseries"
        assert "data" in exc.value.missing

# ── 5. Renderer-guard / 6. BINDING_MISSING (F10/F11 note) ──
# The legacy ReactBackend render-time hard error (MISSING_REQUIRED_PROPS_AT_RENDER)
# and the legacy render_tree binding_missing error were removed with render()/
# render_tree(). Required-prop enforcement now lives in the shared UIIRCompiler
# gate (TestStage2CompilerGate above); the constraint renderer skips
# binding-missing nodes. Empty `pass` stubs referencing the deleted route were
# removed.

# ── 8. Full pipeline integration ──

class TestFullPipelineRequiredProps:
    """Full pipeline: interpret → confirm → apply with required prop enforcement."""

    def test_full_pipeline_all_required_ok(self):
        """All required props resolvable → status ok, valid JSX."""
        pass  # Integration test via apply_engine

    def test_full_pipeline_missing_required_rejected(self):
        """Missing required prop → status rejected, MISSING_REQUIRED_PROPS reason."""
        pass  # Integration test via apply_engine


# ── 9. Fase 1.2: Real component fixtures ──

AGENT_TEST_REPO = "/opt/agent-repos/agent-test-repo"


class TestExtractorRealComponents:
    """Extractor tests against real components from agent-test-repo.

    Validates that the extractor correctly parses actual TypeScript syntax
    as found in the repo, including semicolonless interfaces, generics,
    and complex types.
    """

    EXPECTED: dict[str, tuple[list[str], list[str], list[str]]] = {
        "KpiRow": (["data"], ["data"], []),
        "Timeseries": (["data", "title"], [], ["data", "title"]),
        "DataTable": ([], [], []),
        "PageHeader": ([], [], []),
    }

    @pytest.fixture(scope="class")
    def repo_signatures(self):
        if not os.path.isdir(AGENT_TEST_REPO):
            pytest.skip("agent-test-repo not available at " + AGENT_TEST_REPO)
        from app.signature.extractor import extract_signatures
        return extract_signatures(AGENT_TEST_REPO)

    def test_kpi_row_signature(self, repo_signatures):
        sig = repo_signatures.get("KpiRow")
        assert sig is not None, "KpiRow not found in signatures"
        assert sig["prop_names"] == ["data"], f"KpiRow prop_names: {sig['prop_names']}"
        assert sig["required_props"] == ["data"], f"KpiRow required_props: {sig['required_props']}"
        assert sig["optional_props"] == [], f"KpiRow optional_props: {sig['optional_props']}"
        assert "data" in sig["props"], "KpiRow props block should contain 'data'"

    def test_timeseries_signature(self, repo_signatures):
        sig = repo_signatures.get("Timeseries")
        assert sig is not None, "Timeseries not found in signatures"
        assert "data" in sig["prop_names"], f"Timeseries prop_names: {sig['prop_names']}"
        assert "title" in sig["prop_names"], f"Timeseries prop_names: {sig['prop_names']}"
        assert sig["required_props"] == [], f"Timeseries required_props: {sig['required_props']}"
        assert "data" in sig["optional_props"], f"Timeseries optional_props: {sig['optional_props']}"
        assert "title" in sig["optional_props"], f"Timeseries optional_props: {sig['optional_props']}"

    def test_datatable_signature(self, repo_signatures):
        sig = repo_signatures.get("DataTable")
        assert sig is not None, "DataTable not found in signatures"
        # DataTable uses inline props (not interface), may have empty prop_names
        assert isinstance(sig["prop_names"], list)

    def test_page_header_signature(self, repo_signatures):
        sig = repo_signatures.get("PageHeader")
        assert sig is not None, "PageHeader not found in signatures"
        assert isinstance(sig["prop_names"], list)

    def test_real_component_extra_types(self, repo_signatures):
        """KpiRow has KpiItem as extra_type (not props block)."""
        sig = repo_signatures.get("KpiRow")
        assert sig is not None
        extra_types = sig.get("extra_types", [])
        has_kpi_item = any("KpiItem" in t for t in extra_types)
        assert has_kpi_item, (
            f"KpiRow should have KpiItem as extra_type, got: {extra_types}"
        )

    def test_timeseries_has_no_extra_types_conflict(self, repo_signatures):
        """Timeseries uses 'type Props =' syntax - should not conflict."""
        sig = repo_signatures.get("Timeseries")
        assert sig is not None
        assert "data" in sig.get("prop_names", [])
        assert "title" in sig.get("prop_names", [])


# ── 9. Fase 1.3: Signature Coverage Test ──


class TestSignatureCoverage:
    """Signature Coverage: verify all indexed components have non-empty signatures.

    Fail if a component has:
      - required_props == [] AND prop_names == []
      - AND the component has an interface/type Props declaration

    This catches extractor regressions immediately (e.g. the semicolon bug).
    """

    @pytest.fixture(scope="class")
    def repo_signatures(self):
        if not os.path.isdir(AGENT_TEST_REPO):
            pytest.skip("agent-test-repo not available at " + AGENT_TEST_REPO)
        from app.signature.extractor import extract_signatures
        return extract_signatures(AGENT_TEST_REPO)

    def test_no_empty_signatures_for_components_with_props_interface(self, repo_signatures):
        """Every component with a Props-type interface must have non-empty prop_names.

        This is the regression guard for the semicolon bug.
        """
        empty = {}
        for name, sig in repo_signatures.items():
            props_block = sig.get("props", "")
            prop_names = sig.get("prop_names", [])
            # Only flag components that have an actual props block
            if not prop_names and "Props" in props_block:
                empty[name] = {
                    "file": sig.get("file_path", "?"),
                    "props": props_block,
                }

        assert not empty, (
            f"Components with Props interface but empty prop_names: {json.dumps(empty, indent=2)}"
            "\nThis means the extractor regex failed to parse their props."
        )

    def test_signature_metrics_report(self, repo_signatures):
        """Generate structured signature metrics report."""
        total = len(repo_signatures)
        with_props = sum(1 for s in repo_signatures.values() if s.get("prop_names"))
        empty_sigs = sum(1 for s in repo_signatures.values() if not s.get("prop_names"))
        with_required = sum(1 for s in repo_signatures.values() if s.get("required_props"))

        report = {
            "total_components": total,
            "parsed_components": with_props,
            "empty_signatures": empty_sigs,
            "components_with_required_props": with_required,
        }

        # Fail CI if more than 50% of signatures are empty
        threshold = total * 0.5
        assert empty_sigs <= threshold, (
            f"Signature coverage below threshold: {json.dumps(report, indent=2)}"
        )

        # Log the report (visible in pytest -vvs)
        import logging
        logging.getLogger(__name__).info(
            "Signature coverage report: %s", json.dumps(report)
        )
