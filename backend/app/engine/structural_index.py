from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

from app.engine.state_adapter import ComponentInstanceInfo


@dataclass(frozen=True)
class StructuralIndex:
    """Query interface sobre el worktree real.

    Se construye desde el worktree una vez al inicio del pipeline.
    NO se pasa al GraphIRBuilder. Solo se usa en:
      - StructuralCompletion (lifecycle decisions)
      - StructuralResolver (target resolution, si activo)
      - apply_engine (replace_pairs, validaciones)

    Soportar múltiples instancias por capability via list.
    instance_id=0 es la instancia primaria (compatibilidad).
    """

    _capabilities: dict[str, list[ComponentInstanceInfo]] = field(default_factory=dict)

    @classmethod
    def from_worktree(cls, workspace_root: str) -> StructuralIndex:
        from app.engine.state_adapter import load_current_state
        return cls(load_current_state(workspace_root))

    @classmethod
    def from_mapping(cls, mapping: dict[str, list[ComponentInstanceInfo]]) -> StructuralIndex:
        return cls(mapping)

    @classmethod
    def empty(cls) -> StructuralIndex:
        return cls({})

    def exists(self, capability: str) -> bool:
        instances = self._capabilities.get(capability)
        return instances is not None and len(instances) > 0

    def resolve_path(self, capability: str) -> str | None:
        """Retorna path de la instancia primaria (instance_id=0). Compatibilidad."""
        instances = self._capabilities.get(capability)
        if instances:
            return instances[0].path
        return None

    def resolve_all_paths(self, capability: str) -> list[str]:
        """Retorna todos los paths conocidos para una capability."""
        return [inst.path for inst in self._capabilities.get(capability, [])]

    def get_instances(self, capability: str) -> list[ComponentInstanceInfo]:
        """Retorna todas las instancias conocidas para una capability."""
        return list(self._capabilities.get(capability, []))

    def __contains__(self, capability: str) -> bool:
        return self.exists(capability)

    def __iter__(self) -> Iterator[str]:
        return iter(self._capabilities)

    def __len__(self) -> int:
        return len(self._capabilities)

    @property
    def capability_set(self) -> set[str]:
        return set(self._capabilities.keys())

    @property
    def is_empty(self) -> bool:
        return not self._capabilities

    @staticmethod
    def derive_create_path(contract_id: str | None, capability: str) -> str | None:
        """Generate structural path for a new (CREATE) capability.

        Único lugar del sistema donde se genera un path para un
        componente que aún no existe. Determinístico del contrato.
        """
        if not contract_id:
            return None
        if capability == "layout.page":
            return contract_id
        short_name = capability.rsplit(".", 1)[-1]
        return f"{contract_id}.{short_name}"
