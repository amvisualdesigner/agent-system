"""State adapter — leer workspace actual como baseline."""
from dataclasses import dataclass, field
import os


@dataclass
class ComponentInstanceInfo:
    """Identidad de un componente en el workspace.

    capability: nombre completo (e.g. "presentation.kpi_row")
    path: component_instance_path (e.g. "dashboard.sales.kpi_row")
    instance_id: discriminante numérico auto-incremental por capability.
                 Técnico, no semántico. 0 para la primera instancia.
    anchor: posición layout (opcional, para multi-instancia).
            Cambia cuando MOVE reordena.
            Ej: {"parent": "Page", "index": 0}
    slot_id: identidad estructural estable (opcional).
             NO cambia con MOVE. Permite tracking entre runs.
             Ej: "main_kpi", "sales_table"
    """
    capability: str
    path: str
    instance_id: str = "0"
    anchor: dict | None = None
    slot_id: str | None = None


def load_current_state(workspace_root: str) -> dict[str, list[ComponentInstanceInfo]]:
    """Scan workspace y retorna {capability: [ComponentInstanceInfo, ...]}.

    Soportar múltiples instancias por capability.
    Cada instancia recibe un instance_id auto-incremental y un path único.
    """
    if not workspace_root or not os.path.isdir(workspace_root):
        return {}

    from app.engine.apply_engine import _build_name_map
    from app.engine.apply_engine import _match_file_to_capability
    name_map = _build_name_map()
    state: dict[str, list[ComponentInstanceInfo]] = {}

    for root, _dirs, files in os.walk(workspace_root):
        for fn in files:
            name, _ext = os.path.splitext(fn)
            if not name:
                continue
            name_lower = name.lower()

            capability = _match_file_to_capability(name_lower, name_map)
            if capability is None:
                continue

            short_name = capability.rsplit(".", 1)[-1]
            instances = state.setdefault(capability, [])
            instance_id = str(len(instances))
            instance_path = short_name if instance_id == 0 else f"{short_name}:{instance_id}"
            instances.append(ComponentInstanceInfo(
                capability=capability,
                path=instance_path,
                instance_id=instance_id,
                slot_id=instance_path,
            ))

    return state
