"""Fase 6, Subfase F — materializacion determinista del binding confirmado.

Punto de inyeccion (inspeccionado antes de tocar generacion):

    apply_engine.py:1762  resolved_bindings = resolve_bindings(contract_params)
    apply_engine.py:1766  fileops = renderer.render(..., resolved_bindings=...)

Ese es el UNICO punto donde la autoridad semantica debe entrar a la
representacion fisica: `renderer.render()` recibe `resolved_bindings` y de ahi
`UIIRCompiler.compile()` -> `_build_node()` (compiler.py:183) copia
`component_props[node.type]` a `props`, y el renderer emite la declaracion del
hook (`ReactBackend._page_hook_declaration`) y los imports desde ahi.

`materialize_confirmed_bindings()` se aplica a `ResolvedBindings` ANTES de
render: es la unica frontera donde la autoridad semantica se convierte en
representacion fisica. Ni el renderer ni el compilerneed to know about it.

Reglas fijadas por el usuario:

    Confirmed Binding != Registry Binding  -> reportar drift
    Confirmado sigue materializable        -> continuar SOLO con el confirmado
    Drift impide representar fielmente     -> CONFLICT, sin fallback

El registry es evidencia de repositorio: produce drift, nunca sustituye ni
corrige un binding confirmado. Aqui no se consulta el registry para decidir
nada: el drift se detecta comparando el binding confirmado con la
representacion fisica ya resuelta, que es un hecho, no una consulta.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.binding.models import ResolvedBindings
from app.binding.translate import (
    UntranslatableBinding,
    apply_confirmed_bindings,
    confirmed_bindings_from_actions,
)
from app.graphir.backends.react_backend import JSVariable
from app.intent.models import DataBinding, IntentAction

# conflict_type nuevo: el binding confirmado no se puede representar fielmente.
CONFLICT_UNREPRESENTABLE_BINDING = "invalid_confirmed_plan"


@dataclass(frozen=True)
class DriftReport:
    """Binding confirmado que difiere de la representacion fisica del registry.

    Puramente informativo: el binding confirmado prevalece. Solo escalate a
    CONFLICT cuando el drift hace imposible representarlo fielmente, lo que se
    detecta en `materialize_confirmed_bindings`, no aqui.
    """

    items: tuple[str, ...] = ()

    @property
    def has_drift(self) -> bool:
        return bool(self.items)


def detect_registry_drift(
    resolved: ResolvedBindings, confirmed: dict[str, DataBinding]
) -> DriftReport:
    """Compara el binding confirmado con la resolucion del registry.

    El registry es evidencia: si dice otra cosa, se REGISTRA. Nunca gana.
    """
    items: list[str] = []
    for component, binding in sorted(confirmed.items()):
        for entry in binding.mapping:
            current = (resolved.component_props.get(component) or {}).get(entry.prop)
            current_name = getattr(current, "name", None)
            if current is None:
                items.append(
                    f"{component}.{entry.prop}: registry has no physical value; "
                    f"confirmed binding uses {entry.from_field!r}"
                )
            elif current_name is not None and current_name != entry.from_field:
                items.append(
                    f"{component}.{entry.prop}: registry resolves {current_name!r} "
                    f"but confirmed binding freezes {entry.from_field!r}"
                )
    return DriftReport(tuple(sorted(items)))


def materialize_confirmed_bindings(
    resolved: ResolvedBindings | None,
    actions: list[IntentAction] | None = None,
    confirmed: dict[str, DataBinding] | None = None,
) -> tuple[ResolvedBindings | None, DriftReport]:
    """Aplica el binding confirmado a la representacion fisica. UNICO punto.

    Raises:
        UntranslatableBinding: el binding confirmado no se puede representar sin
            reinterpretarlo. El llamador debe convertir esto en CONFLICT; aqui
            no hay fallback ni sustitucion por el registry.
    """
    if confirmed is None:
        confirmed = confirmed_bindings_from_actions(list(actions or []))
    if resolved is None or not confirmed:
        return resolved, DriftReport()

    drift = detect_registry_drift(resolved, confirmed)
    materialized = apply_confirmed_bindings(resolved, confirmed)
    return materialized, drift


def required_materialization(
    materialized: ResolvedBindings | None,
) -> tuple[str | None, list[str]]:
    """Declaracion del hook e imports que el binding confirmado exige.

    Derivado del propio binding confirmado, no del registry: `_pageData.<sel>`
    requiere que `_pageData` exista, y su declaracion la produce el hook de la
    fuente. Si no hay `page_data_source` en la representacion fisica, el binding
    confirmado NO es materializable y el llamador debe emitir CONFLICT.
    """
    if materialized is None:
        return None, []
    page = materialized.page_data_source
    if page is None:
        needs_page = any(
            getattr(v, "name", "").startswith("_pageData.")
            for props in materialized.component_props.values()
            for v in props.values()
        )
        if needs_page:
            return None, [
                "confirmed binding requires _pageData but the physical "
                "representation has no page_data_source"
            ]
        return None, []

    from app.graphir.backends.react_backend import (
        ReactBackend,
        _HOOK_IMPORT_MAP,
        _REACT_HOOK_MAP,
    )

    declaration = ReactBackend._page_hook_declaration(page)
    imports: list[str] = []
    if declaration:
        hook_name = _REACT_HOOK_MAP.get(page.type)
        hook_import = _HOOK_IMPORT_MAP.get(hook_name) if hook_name else None
        if hook_import:
            imports.append(hook_import)
    return declaration, sorted(set(imports))


__all__ = [
    "CONFLICT_UNREPRESENTABLE_BINDING",
    "DriftReport",
    "UntranslatableBinding",
    "detect_registry_drift",
    "materialize_confirmed_bindings",
    "required_materialization",
]