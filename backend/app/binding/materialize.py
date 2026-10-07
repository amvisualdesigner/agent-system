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

FASE 6.3 (decisiones D2-B/D3 ratificadas):

    * D2-B  -> contradiccion de shape DECLARADO (binding.schema.shape vs
               registry type_info/arity) = DriftItem no bloqueante
               (`declared_shape_contradiction`); `unknown` nunca contradice.
    * D3    -> el registry resuelve un VALOR CONCRETO (no un path): DriftItem
               no bloqueante (`registry_concrete_value`) con preview de forma
               (list[n] / dict{k} / string / number / boolean / object), NUNCA
               el valor completo. Sin fallback, sin bloqueo, confirmed gana.

El registry es evidencia de repositorio: produce drift, nunca sustituye ni
corrige un binding confirmado. Aqui no se consulta el registry para decidir
nada: el drift se detecta comparando el binding confirmado con la
representacion fisica ya resuelta, que es un hecho, no una consulta.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from app.binding.models import ResolvedBindings
from app.binding.lower import SourceDriftItem
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

    reason:
        path_differs                  -> la ruta fisica del registry difiere.
        registry_concrete_value       -> el registry resuelve un valor concreto
                                         (no una ruta): no comparable como path,
                                         se informa con `registry_value_shape`
                                         (preview de forma, JAMAS el valor).
        declared_shape_contradiction  -> el shape declarado del binding
                                         confirmado contradice la declaracion
                                         del registry (type_info/arity).
    """

    component: str
    prop: str
    confirmed: str
    registry: str | None = None
    reason: str = "path_differs"
    registry_value_shape: str | None = None

    def describe(self) -> str:
        if self.reason == "registry_concrete_value":
            return (
                f"{self.component}.{self.prop}: registry has a concrete value "
                f"({self.registry_value_shape}) that is not a comparable physical "
                f"path; confirmed binding freezes {self.confirmed!r}"
            )
        if self.reason == "declared_shape_contradiction":
            return (
                f"{self.component}.{self.prop}: confirmed shape {self.confirmed!r} "
                f"contradicts the registry declaration {self.registry!r}"
            )
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
        payload = {
            "component": self.component,
            "prop": self.prop,
            "confirmed": self.confirmed,
            "registry": self.registry,
            "reason": self.reason,
        }
        if self.registry_value_shape is not None:
            payload["registry_value_shape"] = self.registry_value_shape
        return payload


@dataclass(frozen=True)
class DriftReport:
    """Binding confirmado que difiere de la representacion fisica del registry.

    Cubre el drift a nivel de PROPS (componente.prop) y a nivel de SOURCE de
    pagina (hook confirmado vs hook del registry, producido por el lowering de
    Fase 6.2). Puramente informativo: el binding confirmado prevalece. NUNCA
    bloquea y nunca se convierte en CONFLICT por si mismo.
    """

    items: tuple[DriftItem, ...] = ()
    sources: tuple[SourceDriftItem, ...] = ()

    @property
    def has_drift(self) -> bool:
        return bool(self.items) or bool(self.sources)

    def to_dict(self) -> list[dict]:
        return [i.to_dict() for i in self.items] + [
            s.to_dict() for s in self.sources
        ]

    def describe(self) -> tuple[str, ...]:
        return tuple(i.describe() for i in self.items) + tuple(
            s.describe() for s in self.sources
        )


def _shape_preview(value: object) -> str:
    """Preview de forma de un valor concreto del registry. NUNCA el valor."""
    if isinstance(value, list):
        return f"list[{len(value)}]"
    if isinstance(value, dict):
        keys = sorted(str(k) for k in value.keys())
        head = ", ".join(keys[:3])
        suffix = ", ..." if len(keys) > 3 else ""
        return f"dict{{{head}{suffix}}}"
    if isinstance(value, str):
        return "string"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "object"


def _shape_family(shape: str | None) -> str:
    """Familia determinista de una shape declarada: 'array' | 'non-array'.

    'unknown'/'any'/None/'' no permiten probar nada -> se devuelve 'unknown'.
    'array', 'array<X>', 'X[]' -> 'array'; todo lo demas conocido -> 'non-array'
    (string/number/boolean/object/scalar). Es la regla MINIMA de contradiccion:
    solo la dimension array vs no-array es demostrable entre capas sin
    heuristica profunda (R6). Sin scoring, sin fuzzy.
    """
    s = (shape or "").strip().lower()
    if not s or s in ("unknown", "any"):
        return "unknown"
    if s == "array" or s.startswith("array") or s.endswith("[]"):
        return "array"
    return "non-array"


def registry_declared_shapes() -> dict[str, dict[str, str]]:
    """Shapes declaradas del registry (type_info/arity) por node.type -> prop.

    Evidencia ACTUAL (data_access.json SSOT) exclusivamente para el diagnostico
    de contradiccion de shape declarado de F6.3 (D2-B): nunca decide, solo
    describe. `KpiRow.data` -> 'array<KpiItem>', `Timeseries.title` -> 'string'.
    """
    from app.signature.prop_mapper import load_v4_bindings

    out: dict[str, dict[str, str]] = {}
    for comp, props in load_v4_bindings().items():
        for prop, b in props.items():
            decl: str | None = None
            ti = b.type_info
            if isinstance(ti, dict) and ti.get("type"):
                t = str(ti["type"]).lower()
                if t == "array":
                    items = ti.get("items")
                    decl = f"array<{items}>" if items else "array"
                else:
                    decl = t
            elif b.arity:
                if b.arity.lower().startswith("array"):
                    decl = "array"
                else:
                    decl = "scalar"
            if decl is not None:
                out.setdefault(comp, {})[prop] = decl
    return out


def detect_shape_contradictions(
    confirmed: Mapping[str, DataBinding],
    declared: Mapping[str, Mapping[str, str]],
) -> tuple[DriftItem, ...]:
    """Contradicciones de shape declarado (F6.3 D2-B): SOLO diagnostico.

    Compara `binding.schema.shape` del binding confirmado contra la declaracion
    del registry para el mismo componente.prop. Sigo la regla minima: ambos
    deben ser conocidos y disgregar en la dimension array vs no-array.
    `unknown` en cualquiera de los lados NUNCA contradice (sin drift
    artificial). El resultado son DriftItems no bloqueantes: el binding
    confirmado prevalece y el conflicto no existe.
    """
    items: list[DriftItem] = []
    for component, binding in sorted(confirmed.items()):
        schema = binding.schema
        if schema is None:
            continue
        confirmed_family = _shape_family(schema.shape)
        if confirmed_family == "unknown":
            continue
        for entry in binding.mapping:
            raw_declared = (declared.get(component) or {}).get(entry.prop)
            if raw_declared is None:
                continue
            declared_family = _shape_family(raw_declared)
            if declared_family == "unknown":
                continue
            if confirmed_family != declared_family:
                items.append(
                    DriftItem(
                        component=component,
                        prop=entry.prop,
                        confirmed=schema.shape,
                        registry=raw_declared,
                        reason="declared_shape_contradiction",
                    )
                )
    return tuple(items)


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
            # drift: emitirla seria un falso positivo que alerta sin causa.
            if current is None:
                differs = True
            elif registry_name is None:
                # El registry resuelve un valor concreto, no un JSVariable: no
                # es comparable como ruta fisica. F6.3 (D3): se informa como
                # drift ESTRUCTURADO no bloqueante con preview de forma (nunca
                # el valor completo). El binding confirmado sigue ganando.
                items.append(
                    DriftItem(
                        component=component,
                        prop=entry.prop,
                        confirmed=entry.from_field,
                        registry=None,
                        reason="registry_concrete_value",
                        registry_value_shape=_shape_preview(current),
                    )
                )
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

    from app.graphir.backends.react_backend import ReactBackend

    # El binding confirmado ya bajo su `hook_name`/`hook_import` sobre la
    # representacion fisica (Fase 6.2): el backend es materializador y resuelve
    # la precedencia hook explicito -> registry hook -> mapa estatico.
    declaration = ReactBackend._page_hook_declaration(page)
    imports: list[str] = []
    if declaration:
        hook_import = ReactBackend._page_hook_import(page)
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
    "detect_shape_contradictions",
    "materialize_confirmed_bindings",
    "registry_declared_shapes",
    "required_materialization",
]