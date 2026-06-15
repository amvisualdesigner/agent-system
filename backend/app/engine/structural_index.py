from __future__ import annotations

import os
from collections import Counter
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
        """Retorna todos los instance paths conocidos para una capability."""
        return [inst.path for inst in self._capabilities.get(capability, [])]

    def resolve_all_file_paths(self, capability: str) -> list[str]:
        """Retorna todos los filesystem paths conocidos para una capability.

        Solo incluye aquellos con file_path no-None.
        """
        return [
            inst.file_path for inst in self._capabilities.get(capability, [])
            if inst.file_path is not None
        ]

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

    def detect_component_prefixes(self) -> dict[str, str]:
        """Return {component_name: file_path} for all known component files.

        Uses instance file_paths to detect the actual repo layout
        (e.g. ``frontend/`` prefix). Only includes entries with
        a ``.tsx`` file_path.
        """
        result: dict[str, str] = {}
        for instances in self._capabilities.values():
            for inst in instances:
                fp = inst.file_path
                if fp and fp.endswith(".tsx"):
                    name = os.path.splitext(os.path.basename(fp))[0]
                    result[name] = fp
        return result

    def most_common_component_prefix(self) -> str | None:
        """Return the most common directory prefix among component files.

        E.g. ``frontend/src/components`` if most files live under that tree.
        Used as fallback when creating new files whose path is unknown.
        Observational only — does not impose architecture.
        """
        dirs: Counter = Counter()
        for instances in self._capabilities.values():
            for inst in instances:
                fp = inst.file_path
                if fp and not fp.endswith("/"):
                    d = os.path.dirname(fp)
                    if d:
                        dirs[d] += 1
        if not dirs:
            return None
        return dirs.most_common(1)[0][0]
