from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ComponentNode:
    """Entidad estructural mínima que el registry conoce.

    Representa un componente existente derivado del contrato.
    """
    component_instance_path: str
    type: str
    capability: str
    metadata: dict = field(default_factory=dict)


@dataclass
class StructuralResolution:
    """Output del resolver — mapping capability → path resuelto.

    Un solo objeto con:
      - capability_to_path: lookup directo para el builder
      - confidence: confianza global de la resolución
      - trace: por qué se eligió cada camino
      - reason: None si éxito, str con motivo si vacío/falló
    """
    capability_to_path: dict[str, str]
    confidence: float
    trace: list[str]
    instance_mapping: dict[str, str] = field(default_factory=dict)
    reason: Optional[str] = None
