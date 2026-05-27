from __future__ import annotations

from typing import Any

from app.graphir.structure.models import ComponentNode


class StructuralRegistry:
    """Índice estructural construido desde el contrato.

    Fase 1b: solo contract-based. En Fase 2 se añadirán fuentes
    del repo real (extractores React/Vue/HTML).

    Responsabilidad única: resolve_candidates(capability) → list[ComponentNode].
    """

    def __init__(self) -> None:
        self._components: dict[str, list[ComponentNode]] = {}

    @classmethod
    def build_from_contract(cls, contract: Any) -> StructuralRegistry:
        """Construir registry desde ast_template del contrato.

        Cada capability definida en el template se registra como
        ComponentNode con component_instance_path derivado.

        Args:
            contract: SkillContract con ast_template.

        Returns:
            StructuralRegistry poblado.
        """
        registry = cls()
        template = getattr(contract, "ast_template", {})
        if not template:
            return registry

        capabilities: dict[str, str] = template.get("capabilities", {})
        contract_id = getattr(contract, "contract_id", "unknown")
        slots: list[dict] = template.get("slots", [])

        # Page node (root)
        page_cap = capabilities.get("Page")
        if page_cap:
            registry._register(
                component_instance_path=contract_id,
                type="Page",
                capability=page_cap,
                metadata={"source": "contract", "slot_type": "Page"},
            )

        # Slot-level nodes (presentation components)
        for slot in slots:
            slot_type: str = slot.get("type", "")
            cap_name: str | None = capabilities.get(slot_type)
            if not cap_name:
                continue
            short_name = cap_name.rsplit(".", 1)[-1]
            path = f"{contract_id}.{short_name}"
            registry._register(
                component_instance_path=path,
                type=slot_type,
                capability=cap_name,
                metadata={"source": "contract", "slot_type": slot_type},
            )

        return registry

    def _register(
        self,
        component_instance_path: str,
        type: str,
        capability: str,
        metadata: dict | None = None,
    ) -> None:
        node = ComponentNode(
            component_instance_path=component_instance_path,
            type=type,
            capability=capability,
            metadata=metadata or {},
        )
        self._components.setdefault(capability, []).append(node)

    def resolve_candidates(self, capability: str) -> list[ComponentNode]:
        """Retornar ComponentNode registrados para una capability.

        Args:
            capability: Nombre de capability ("presentation.kpi_row").

        Returns:
            Lista de nodos candidatos (vacía si no hay ninguno).
        """
        return list(self._components.get(capability, []))
