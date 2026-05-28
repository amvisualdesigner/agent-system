"""State adapter — leer workspace actual como baseline."""
from dataclasses import dataclass, field
import os


@dataclass
class ComponentInstanceInfo:
    """Identidad de un componente en el workspace.

    capability: nombre completo (e.g. "presentation.kpi_row")
    path: component_instance_path (e.g. "dashboard.sales.kpi_row")
    anchor: posición layout (opcional, para multi-instancia).
            Cambia cuando MOVE reordena.
            Ej: {"parent": "Page", "index": 0}
    slot_id: identidad estructural estable (opcional).
             NO cambia con MOVE. Permite tracking entre runs.
             Ej: "main_kpi", "sales_table"
    """
    capability: str
    path: str
    anchor: dict | None = None
    slot_id: str | None = None


def load_current_state(workspace_root: str) -> dict[str, ComponentInstanceInfo]:
    """Scan workspace y retorna {capability: ComponentInstanceInfo}.

    Mantiene identidad estable entre runs: mismo archivo → mismo path.
    """
    if not workspace_root or not os.path.isdir(workspace_root):
        return {}

    from app.engine.apply_engine import _build_name_map
    name_map = _build_name_map()
    state: dict[str, ComponentInstanceInfo] = {}

    for root, _dirs, files in os.walk(workspace_root):
        for fn in files:
            name, _ext = os.path.splitext(fn)
            if not name:
                continue
            name_lower = name.lower()

            capability = None
            if name_lower in name_map:
                capability = name_map[name_lower]
            else:
                for pattern, cap_name in name_map.items():
                    if pattern in name_lower:
                        capability = cap_name
                        break

            if capability is None:
                continue

            short_name = capability.rsplit(".", 1)[-1]
            state[capability] = ComponentInstanceInfo(
                capability=capability,
                path=short_name,
                slot_id=short_name,
            )

    return state
