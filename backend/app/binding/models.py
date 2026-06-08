from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BindingProvenance:
    """Trazabilidad de cómo se resolvió un prop de componente.

    Attributes:
        source: Origen del binding ("slice", "binding_ir", "contract_default")
        selector: Selector del DataSlice (e.g. "kpiData")
        contract_params: Lista de contract params que originaron este binding
    """
    source: str
    selector: str | None = None
    contract_params: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        parts = [self.source]
        if self.selector:
            parts.append(self.selector)
        if self.contract_params:
            parts.append(f"from:{','.join(self.contract_params)}")
        return ":".join(parts)


@dataclass
class ResolvedBindings:
    """Contrato formal entre BindingResolver y Compiler.

    Contiene todas las props resueltas para cada componente del árbol,
    más metadatos de trazabilidad y params no consumidos.

    Attributes:
        component_props: component_type → {prop_name → resolved_value}
        provenance: component_type → {prop_name → BindingProvenance}
        consumed_params: Contract params consumidos por al menos un slice
        unconsumed_params: Contract params NO consumidos por ningún slice (warning)
        page_data_source: DataSourceIR para el Page (si existe), necesario
                         para que el renderer genere el hook declaration
        imports: Import statements necesarios (e.g. hook imports)
    """
    component_props: dict[str, dict[str, Any]] = field(default_factory=dict)
    provenance: dict[str, dict[str, str]] = field(default_factory=dict)
    consumed_params: set[str] = field(default_factory=set)
    unconsumed_params: set[str] = field(default_factory=set)
    page_data_source: Any | None = None
    imports: list[str] = field(default_factory=list)


@dataclass
class BindingDiffItem:
    """Single prop-level diff entry from dual-write (F0) comparison.

    Attributes:
        component: Component type (e.g. "Timeseries")
        prop: Prop name (e.g. "data")
        slice_value: Value resolved via Page slice path
        binding_value: Value resolved via v4 binding path
        classification: Equivalence class — "structural_equivalent",
                       "semantic_equivalent", or "divergent"
    """
    component: str
    prop: str
    slice_value: Any = None
    binding_value: Any = None
    classification: str = "divergent"


@dataclass
class BindingDiff:
    """Result of dual-write comparison between Page slices and v4 bindings.

    Attributes:
        items: List of per-prop diff entries
    """
    items: list[BindingDiffItem] = field(default_factory=list)

    @property
    def divergent_count(self) -> int:
        return sum(1 for i in self.items if i.classification == "divergent")

    @property
    def binding_missing_count(self) -> int:
        """Props que el binding registry declara pero no puede resolver."""
        return sum(1 for i in self.items if i.classification == "binding_missing")

    @property
    def total_overlap(self) -> int:
        return len(self.items)
