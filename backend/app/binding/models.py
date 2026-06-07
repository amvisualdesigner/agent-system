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
