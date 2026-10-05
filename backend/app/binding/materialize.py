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

# El binding confirmado es valido; lo que falla es la materializacion fisica.
# Se reutiliza la envolvente post-confirmacion existente (`repository_conflict`),
# que NO terminaliza el Run: `_settle_phase` devuelve el run a CONFIRMED con
# plan_retryable=True. La causa especifica se conserva en `details.cause`, sin
# crear un tipo publico nuevo ni tocar el contrato de API.
CONFLICT_UNREPRESENTABLE_BINDING = "repository_conflict"
UNREPRESENTABLE_BINDING_CAUSE = "unrepresentable_binding"


@dataclass(frozen=True)
class DriftItem:
    """Un prop donde el binding confirmado discrepa de la resolucion del registry.

    Estrictamente diagnostico. El binding confirmado prevalece siempre.
    """

    component: str
    prop: str
    confirmed: str
    registry: str | None = None

    def describe(self) -> str:
        if self.registry is None:
            return (
                f"{self.component}.{self.prop}: registry has no physical value; "
                f"confirmed binding uses {self.confirmed!r}"
            )
        return (
            f"{self.component}.{self.prop}: registry resolves {self.registry!r} "
            f"but confirmed binding freezes {self.confirmed!r}"
        )

    def to_dict(self) -> dict:
        return {
            "component": self.component,
            "prop": self.prop,
            "confirmed": self.confirmed,
            "registry": self.registry,
        }


@dataclass(frozen=True)
class DriftReport:
    """Binding confirmado que difiere de la representacion fisica del registry.

    Puramente informativo: el binding confirmado prevalece. NUNCA bloquea y
    nunca se convierte en CONFLICT por si mismo.
    """

    items: tuple[DriftItem, ...] = ()

    @property
    def has_drift(self) -> bool:
        return bool(self.items)

    def to_dict(self) -> list[dict]:
        return [i.to_dict() for i in self.items]

    def describe(self) -> tuple[str, ...]:
        return tuple(i.describe() for i in self.items)


def detect_registry_drift(
    resolved: ResolvedBindings, confirmed: dict[str, DataBinding]
) -> DriftReport:
    """Compara el binding confirmado con la resolucion del registry.

    El registry es evidencia: si dice otra cosa, se REGISTRA. Nunca gana.
    """
    items: list[DriftItem] = []
    for component, binding in sorted(confirmed.items()):
        for entry in binding.mapping:
            current = (resolved.component_props.get(component) or {}).get(entry.prop)
            registry_name = getattr(current, "name", None)
            # Solo se registra cuando HAY discrepancia. Coincidencia no es
            # drift: emitirla seria un falso positivo que报警 sin causa.
            if current is None:
                differs = True
            elif registry_name is None:
                # El registry resuelve un valor concreto, no un JSVariable: no
                # es comparable como ruta fisica, pero tampoco lo contradice.
                continue
            else:
                differs = registry_name != entry.from_field
            if not differs:
                continue
            items.append(
                DriftItem(
                    component=component,
                    prop=entry.prop,
                    confirmed=entry.from_field,
                    registry=registry_name,
                )
            )
    return DriftReport(tuple(items))


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


@dataclass(frozen=True)
class MaterializationRequirements:
    """Lo que el binding CONFIRMADO exige generar, y lo que no se puede.

    Se devuelven los tres campos explicitamente porque un retorno posicional
    ambiguo hacia que el llamador lea `imports` como `missing` y convierta una
    materializacion correcta en un CONFLICT falso.
    """

    declaration: str | None = None
    imports: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()


def _confirmed_requires_page_data(confirmed: dict[str, DataBinding]) -> bool:
    return any(
        entry.from_field.startswith("_pageData.")
        for binding in confirmed.values()
        for entry in binding.mapping
    )


def required_materialization(
    materialized: ResolvedBindings | None,
    confirmed: dict[str, DataBinding] | None = None,
) -> MaterializationRequirements:
    """Declaracion del hook, imports y bloqueos, derivados del binding confirmado.

    Derivado del propio binding confirmado, no del registry: `_pageData.<sel>`
    requiere que `_pageData` exista, y su declaracion la produce el hook de la
    fuente. Si no hay `page_data_source` en la representacion fisica, el binding
    confirmado NO es materializable: se informa en `missing`.

    `confirmed` es necesario cuando `materialized` es None: el requisito se lee
    del binding confirmado, no de la representacion ausente. Sin el, un binding
    confirmado que exige `_pageData` se saltaria en silencio.
    """
    if materialized is None:
        if confirmed and _confirmed_requires_page_data(confirmed):
            return MaterializationRequirements(
                missing=(
                    "confirmed binding requires _pageData but there is no "
                    "physical representation to materialize it into",
                )
            )
        return MaterializationRequirements()
    page = materialized.page_data_source
    if page is None:
        needs_page = _confirmed_requires_page_data(confirmed) if confirmed else any(
            getattr(v, "name", "").startswith("_pageData.")
            for props in materialized.component_props.values()
            for v in props.values()
        )
        if needs_page:
            return MaterializationRequirements(
                missing=(
                    "confirmed binding requires _pageData but the physical "
                    "representation has no page_data_source",
                )
            )
        return MaterializationRequirements()

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
    return MaterializationRequirements(
        declaration=declaration, imports=tuple(sorted(set(imports)))
    )


__all__ = [
    "CONFLICT_UNREPRESENTABLE_BINDING",
    "UNREPRESENTABLE_BINDING_CAUSE",
    "DriftItem",
    "MaterializationRequirements",
    "DriftReport",
    "UntranslatableBinding",
    "detect_registry_drift",
    "materialize_confirmed_bindings",
    "required_materialization",
]