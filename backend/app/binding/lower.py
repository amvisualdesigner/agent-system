"""Fase 6, Subfase 6.2 — lowering determinista del source confirmado a la
representacion fisica de la pagina.

La autoridad de WHAT es el Confirmed Plan (`ConfirmedPlan.actions[i].binding`).
Esta frontera traduce SOLO el `source` de ese binding a la fuente fisica del
Page (`DataSourceIR` con `hook_name`/`hook_import`): es un lowering puro, sin
LLM, sin discovery, sin seleccion de fuente ni mutacion del Plan o del registry.

Reglas fijadas por el usuario (FASE 6.2):

  * kind == "hook"  -> soportado.
  * kind in {slice, service, symbol, query} -> repository_conflict retryable
    (NUNCA cae al registry; correccion obligatoria sobre el preliminar).
  * hook unico + identico al hook del registry -> identidad: se conserva la
    representacion registry (un solo `_pageData`, sin decl/import duplicados).
  * hook unico + distinto del registry -> override fisico: DataSourceIR con
    `hook_name`/`hook_import` sobre los slices del registry.
  * varios hooks confirmados distintos -> CONFLICT multiple_hooks.
  * resolución de import DETERMINISTA: el simbolo del hook debe exportarse
    exactamente en exactamente UN modulo de `frontend/src/**/*.{ts,tsx}`.
    0 -> missing_hook, >1 -> ambiguous_hook. Nunca fuzzy, scoring, o LLM.

FASE 6.2.1 (decision ratificada):

  * kind == "slice" -> soportado SOLO como validacion estructural contra el Page
    data source del registry: el slice confirmado (`component`/`selector`/prop
    de mapping) debe COINCIDIR exactamente con un `DataSlice` declarado en
    `data_access.json` (`composition.Page.dataSource.slices`). El registry es
    EVIDENCIA: el slice confirmado NO se transforma en otro hook ni se sustituye
    por otro binding; solo se reutiliza la fuente fisica existente que ya lo
    declara (`ir=None`, se conserva la representacion registry). Se registra
    como lowerings del binding confirmado (`LoweringResult.slices`), nunca como
    fallback ni consulta fuzzy. Sin coincidencia -> repository_conflict
    retryable (missing_slice / unrepresentable_mapping / missing_target), sin
    escribir nada.
  * kind in {service, symbol, query} -> repository_conflict retryable
    unsupported_kind (fuera de alcance 6.2.1).
  * slice confirmado + hook confirmado distinto del registry -> CONFLICT
    slice_hook_conflict: la pagina tiene un solo `_pageData` root y el override
    re-cablearia el host fisico del slice.

FASE 6.3 (decision D2-A ratificada, SOLO en el override/Caso C):

  * cada mapping confirmado `_pageData.<selector>` debe enraizar en el
    snapshot FISICO FRESCO: el root del selector debe figurar entre los campos
    desestructurados del hook confirmado (`scan_calls`, los mismos campos que
    discovery) o quedar enraizado por la extraccion `_pageData = hook(...)`
    (mismo nivel de certeza estructural que usa el registry para componer).
    Sin raiz verificada -> CONFLICT `missing_field` retryable, sin rediscovery.
  * selectores anidados validan SOLO el root (R6: sin heuristica profunda).
  * la extraccion `_pageData = hook(...)` enraiza todos los `_pageData.<sel>`
    del hook: es la representacion POST-apply, asi el re-apply del mismo Plan
    confirmado NO produce un falso-positivo (la operacion es "binding congelado
    -> verificacion fisica", nunca rediscovery ni re-escritura).
  * Caso B (identidad) NO se re-valida: el registry ya materializo esa
    representacion; un re-check sobre paginas compuestas (p.ej. FilterPanel
    registrado en forma `_pageData`) seria un falso-positivo. Solo el override
    que RE-CABLEA la pagina fisica exige prueba de campos en el momento.

Funciones puras: los contenidos llegan ya leidos (`repo_files`, snapshot de la
aplicacion). `collect_repo_files()` es la unica lectura acotada de disco.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Mapping

from app.binding.discovery import rooted_at, scan_calls
from app.intent.models import DataBinding
from app.signature.prop_mapper import DataSourceIR, _REACT_HOOK_MAP

logger = logging.getLogger(__name__)

# Causa estructurada de los conflicts de lowering (details.cause). Los conflict
# se emiten en el envelope post-confirmacion existente (`repository_conflict`),
# plan_retryable=True, Run=CONFIRMED: el Plan NO se invalida ni se reinterpreta.
SOURCE_LOWERING_CONFLICT = "source_lowering_conflict"

# kinds representables fisicamente: "hook" (F6.2) y "slice" (F6.2.1).
HOOK_KIND = "hook"
SLICE_KIND = "slice"

# Alcance del probe de import: igual que el registry real (frontend/src).
_PROBE_ROOT = os.path.join("frontend", "src")
_PROBE_EXTENSIONS = (".ts", ".tsx")
_SKIP_DIRS = frozenset({"node_modules", ".git", "dist", "build", ".next", "__pycache__"})

_EXPORT_FUNCTION = re.compile(r"export\s+(?:async\s+)?function\s+(\w+)")
_EXPORT_CONST = re.compile(r"export\s+(?:const|let|var)\s+(\w+)")
_EXPORT_NAMED = re.compile(r"export\s*\{([^}]*)\}")
_PAGE_DATA_ASSIGN = re.compile(r"\b_pageData\s*=\s*(\w+)\s*\(")


@dataclass(frozen=True)
class SourceDriftItem:
    """Diagnostico: el source confirmado difiere del source del registry.

    El registry es evidencia fisica; el binding confirmado gana SIEMPRE.
    Estrictamente informativo: nunca bloquea y nunca produce CONFLICT por si
    mismo (los conflict son del lowering, no del drift).
    """

    component: str
    confirmed: str
    registry: str | None = None
    reason: str = "confirmed_source_differs"

    def describe(self) -> str:
        if self.registry is None:
            return (
                f"{self.component}: confirmed source {self.confirmed!r} has no "
                "registry page source"
            )
        return (
            f"{self.component}: confirmed source {self.confirmed!r} overrides "
            f"registry {self.registry!r}"
        )

    def to_dict(self) -> dict:
        return {
            "component": self.component,
            "source": {"confirmed": self.confirmed, "registry": self.registry},
        }


class SourceLoweringConflict(Exception):
    """La fuente confirmada no se puede bajar fielmente.

    SIEMPRE es un `repository_conflict` retryable: el Confirmed Plan sigue
    siendo la unica autoridad y el retry recaptura el snapshot fisico. No hay
    fallback al registry y no se reinterpreta la fuente confirmada.
    """

    def __init__(
        self,
        reason: str,
        hook: str | None,
        detail: str,
        components: list[str] | None = None,
    ):
        self.reason = reason  # unsupported_kind | missing_hook | ambiguous_hook | multiple_hooks | missing_target | missing_slice | unrepresentable_mapping | slice_hook_conflict | ambiguous_target | missing_field
        self.hook = hook
        self.detail = detail
        self.components = sorted(set(components or ()))
        super().__init__(f"{reason} (hook={hook}): {detail}")


@dataclass(frozen=True)
class SliceLowering:
    """Registro de un slice confirmado bajado a su slice fisico declarado.

    NO es un fallback ni una sustitucion: es la evidencia de que el binding
    confirmado (`kind=slice`) se corresponde 1:1 con un slice declarado en la
    Page data source del registry (`component`/`target_prop`/`selector`), por lo
    que la representacion fisica del registry se conserva tal cual.
    """

    component: str
    selector: str
    target_prop: str
    hook_name: str | None = None

    def to_dict(self) -> dict:
        return {
            "component": self.component,
            "selector": self.selector,
            "target_prop": self.target_prop,
            "hook": self.hook_name,
        }


@dataclass(frozen=True)
class LoweringResult:
    """Salida del lowering del source confirmado.

    ir:
      None   -> conservar la representacion fisica (registry) tal cual:
               Caso A (sin hook confirmado), identidad (hook == registry), o
               slice confirmado valido (F6.2.1: el slice vive en el registry).
      DataSourceIR -> representacion fisica del hook confirmado (override).
    drift:
      Items diagnosticos source-level (no bloqueantes) cuando el source
      confirmado difiere del registry.
    slices:
      Slices confirmados validados contra la Page data source del registry
      (F6.2.1). Diagnostico: registra el lowering, nunca sustituye el binding.
    """

    ir: DataSourceIR | None = None
    drift: tuple[SourceDriftItem, ...] = ()
    slices: tuple[SliceLowering, ...] = ()


def collect_repo_files(workspace_root: str | None) -> dict[str, str]:
    """Snapshot fisico: archivos relativos `.ts`/`.tsx` bajo frontend/src.

    Es la EVIDENCIA del probe de import. Lectura acotada a frontend/src,
    read-only; dict vacio si el root o el subarbol no existen (las llamadas
    de test pasan `repo_files` directamente).
    """
    if not workspace_root or not os.path.isdir(workspace_root):
        return {}
    probe_root = os.path.join(workspace_root, *_PROBE_ROOT.split(os.sep))
    if not os.path.isdir(probe_root):
        return {}
    out: dict[str, str] = {}
    for root, dirs, files in os.walk(probe_root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in sorted(files):
            if not name.endswith(_PROBE_EXTENSIONS):
                continue
            full = os.path.join(root, name)
            rel = os.path.relpath(full, workspace_root).replace(os.sep, "/")
            try:
                with open(full, encoding="utf-8", errors="replace") as fh:
                    out[rel] = fh.read()
            except OSError:
                continue
    return out


def _registry_hook_name(ir: DataSourceIR | None) -> str | None:
    """Nombre del hook del registry: declarado en data_access.json o mapa estatico."""
    if ir is None:
        return None
    return getattr(ir, "hook_name", None) or _REACT_HOOK_MAP.get(ir.type)


def _ident(raw: str) -> str:
    """Nombre local de un export: 'a as b' -> 'b'."""
    name = raw.split(" as ", 1)[-1]
    name = name.split(":", 1)[-1]
    return name.strip()


def _module_exports_symbol(content: str, symbol: str) -> bool:
    """El modulo exporta EXACTAMENTE `symbol` (named export). Sin scoring."""
    if not content:
        return False
    for block in _EXPORT_FUNCTION.findall(content):
        if block == symbol:
            return True
    for block in _EXPORT_CONST.findall(content):
        if block == symbol:
            return True
    for body in _EXPORT_NAMED.findall(content):
        for raw in body.split(","):
            if _ident(raw) == symbol:
                return True
    return False


def _module_stem(rel: str) -> str:
    """Modulo relativo a `@/` sin extension:
    'frontend/src/hooks/useSalesData.ts' -> 'hooks/useSalesData'."""
    rel = rel.replace(os.sep, "/")
    if rel.startswith("frontend/src/"):
        rel = rel[len("frontend/src/"):]
    for ext in _PROBE_EXTENSIONS:
        if rel.endswith(ext):
            rel = rel[: -len(ext)]
            break
    return rel.strip("/")


def _hook_call_fields(hook: str, repo_files: Mapping[str, str]) -> frozenset[str]:
    """Campos desestructurados de `hook` en el snapshot fisico (union).

    Mismos regex/campos que discovery (`scan_calls`): la EVIDENCIA de que el
    hook confirmado esta cableado en la aplicacion. Union determinista sobre
    `frontend/src/**/*.{ts,tsx}`, exactamente el alcance del probe.
    """
    fields: set[str] = set()
    for rel, content in sorted(repo_files.items()):
        rel = rel.replace(os.sep, "/")
        if not rel.startswith("frontend/src/") or not rel.endswith(_PROBE_EXTENSIONS):
            continue
        if not content:
            continue
        for callee, flds in scan_calls(content):
            if callee == hook:
                fields.update(flds)
    return frozenset(fields)


def _page_data_extracted_from(hook: str, repo_files: Mapping[str, str]) -> bool:
    """La pagina extrae el root entero: `_pageData = hook(...)`.

    Es la representacion POST-apply del override (y la que usa el registry para
    componer): enraiza TODOS los `_pageData.<selector>` del hook con el mismo
    nivel de certeza estructural que los campos desestructurados. Permite que
    el re-apply de un Plan ya materializado NO produzca un falso `missing_field`.
    """
    for content in repo_files.values():
        if not content:
            continue
        for callee in _PAGE_DATA_ASSIGN.findall(content):
            if callee == hook:
                return True
    return False


def _missing_confirmed_field_roots(
    confirmed_bindings: Mapping[str, DataBinding],
    hook: str,
    repo_files: Mapping[str, str],
) -> tuple[str, ...]:
    """Roots de `_pageData.<selector>` no enraizados en el snapshot (F6.3 D2-A).

    Solo los mappings `_pageData.` son representables fisicamente por lowering
    (los demas los cubre translate con `UntranslatableBinding`). Se valida el
    ROOT del selector (R6: sin heuristica profunda) contra los campos
    desestructurados/extraidos del hook confirmado. Determinista y puro.
    """
    fields = _hook_call_fields(hook, repo_files)
    if _page_data_extracted_from(hook, repo_files):
        return ()
    missing: set[str] = set()
    for binding in confirmed_bindings.values():
        for entry in binding.mapping:
            if not entry.from_field.startswith("_pageData."):
                continue
            selector = entry.from_field[len("_pageData."):]
            if not rooted_at(selector, fields):
                root = selector.split(".", 1)[0]
                if root:
                    missing.add(root)
    return tuple(sorted(missing))


def probe_hook_modules(
    hook: str, repo_files: Mapping[str, str]
) -> list[str]:
    """Modulos que exportan exactamente `hook`, sin duplicar por modulo.

    Determinista (orden alfabetico). Invariante I3: el hook confirmado debe
    materializarse en UN solo modulo; 0 o >1 son CONFLICT, nunca fuzzy.
    """
    found: list[str] = []
    for rel in sorted(repo_files):
        rel = rel.replace(os.sep, "/")
        if not rel.startswith("frontend/src/") or not rel.endswith(_PROBE_EXTENSIONS):
            continue
        if not _module_exports_symbol(repo_files[rel], hook):
            continue
        stem = _module_stem(rel)
        if stem not in found:
            found.append(stem)
    return found


def _slice_conflict(
    component: str,
    ref: str | None,
    components_of: list[str],
    reason: str,
    detail: str,
) -> SourceLoweringConflict:
    return SourceLoweringConflict(
        reason=reason,
        hook=(ref or None),
        detail=f"{component}: {detail} (no registry fallback).",
        components=components_of,
    )


def _lower_confirmed_slices(
    slice_bindings: Mapping[str, DataBinding],
    registry_ds: object | None,
    components_of: list[str],
) -> tuple[SliceLowering, ...]:
    """Valida los slices confirmados contra la Page data source del registry.

    SOLO evidencia estructural: cada binding `kind=slice` debe corresponderse
    1:1 con un `DataSlice` declarado (`component` + `selector` + `target_prop`).
    No hay fuzzy matching, scoring, rediscovery ni sustitucion por el registry.
    Devuelve los lowerings registrados; sin coincidencia exacta -> CONFLICT
    retryable (`repository_conflict`).
    """
    if not slice_bindings:
        return ()

    if registry_ds is None:
        component, binding = next(iter(sorted(slice_bindings.items())))
        ref = (getattr(binding.source, "ref", "") or "").strip()
        raise _slice_conflict(
            component, ref, components_of, "missing_slice",
            "confirmed DataSourceRef kind 'slice' has no registry page data "
            "source to host it; the slice is not physically materializable",
        )

    declared = tuple(getattr(registry_ds, "slices", ()) or ())
    registry_hook = _registry_hook_name(registry_ds)
    lowerings: list[SliceLowering] = []

    for component, binding in sorted(slice_bindings.items()):
        src = binding.source
        ref = (getattr(src, "ref", "") or "").strip()
        selector = (getattr(src, "selector", None) or "").strip()
        wanted = selector or ref

        candidates = [
            s for s in declared if getattr(s, "component", None) == component
        ]
        if not candidates:
            raise _slice_conflict(
                component, ref, components_of, "missing_slice",
                f"registry declares no data slice for component {component!r}",
            )

        match = next(
            (s for s in candidates if getattr(s, "selector", None) == wanted),
            None,
        )
        if match is None:
            available = sorted(str(getattr(s, "selector", "")) for s in candidates)
            raise _slice_conflict(
                component, ref, components_of, "missing_slice",
                f"registry declares no slice {wanted!r} for component "
                f"{component!r} (available selectors: {available})",
            )

        # Mapping confirmado INTACTO: cada `_pageData.<sel>` debe apuntar
        # exactamente al slice declarado y a su target_prop. Reinterpretar el
        # mapping seria cambiar la autoridad del Confirmed Plan.
        for entry in binding.mapping:
            from_field = entry.from_field or ""
            if not from_field.startswith("_pageData."):
                raise _slice_conflict(
                    component, ref, components_of, "unrepresentable_mapping",
                    f"{component}.{entry.prop}: from_field {from_field!r} is not a "
                    "_pageData.* slice path; the confirmed mapping cannot be "
                    "materialized from a data slice",
                )
            path = from_field[len("_pageData."):]
            if path != match.selector:
                raise _slice_conflict(
                    component, ref, components_of, "unrepresentable_mapping",
                    f"{component}.{entry.prop}: confirmed selector {path!r} does "
                    f"not match the declared slice selector {match.selector!r}",
                )
            if entry.prop != match.target_prop:
                raise _slice_conflict(
                    component, ref, components_of, "missing_target",
                    f"{component}.{entry.prop}: the declared slice targets prop "
                    f"{match.target_prop!r}, not {entry.prop!r}",
                )

        lowerings.append(
            SliceLowering(
                component=component,
                selector=match.selector,
                target_prop=match.target_prop,
                hook_name=registry_hook,
            )
        )

    return tuple(lowerings)


def lower_confirmed_page_source(
    confirmed_bindings: dict[str, DataBinding],
    resolved_bindings: object | None = None,
    repo_files: Mapping[str, str] | None = None,
) -> LoweringResult:
    """Lowering del source confirmado a la fuente fisica de la pagina.

    Entradas:
        confirmed_bindings: {capability: DataBinding} del Confirmed Plan.
        resolved_bindings: ResolvedBindings (registry fisico) o None.
        repo_files: snapshot {rel_path: content} de frontend/src.

    Raises:
        SourceLoweringConflict: la fuente confirmada no es representable con
        fidelidad (unrepresentable en la envolvente `repository_conflict`).

    No lee disco, no toca el Plan ni el registry: lowering puro.
    """
    repo_files = repo_files or {}

    # Apuntes por componente para el diagnostico de conflicto.
    components_of = sorted(confirmed_bindings)

    registry_ds = (
        getattr(resolved_bindings, "page_data_source", None)
        if resolved_bindings
        else None
    )

    # ── Kind gate (I3/matriz F6.2/F6.2.1): hook y slice son representables.
    # service/symbol/query NUNCA caen al registry: son CONFLICT retryable,
    # incluso cuando conviven otros sources confirmados.
    hook_refs: dict[str, list[str]] = {}
    slice_bindings: dict[str, DataBinding] = {}
    for component, binding in sorted(confirmed_bindings.items()):
        src = binding.source
        if src is None:
            continue
        if src.kind == HOOK_KIND:
            ref = (src.ref or "").strip()
            if not ref:
                raise SourceLoweringConflict(
                    reason="unsupported_kind",
                    hook=None,
                    detail=f"{component}: hook with empty ref (malformed)",
                    components=components_of,
                )
            hook_refs.setdefault(ref, []).append(component)
        elif src.kind == SLICE_KIND:
            slice_bindings[component] = binding
        else:
            raise SourceLoweringConflict(
                reason="unsupported_kind",
                hook=src.ref,
                detail=(
                    f"{component}: DataSourceRef kind {src.kind!r} is not lowerable "
                    f"in Fase 6.2 (only {HOOK_KIND!r} and {SLICE_KIND!r}); no "
                    "registry fallback."
                ),
                components=components_of,
            )

    # ── F6.2.1: slices confirmados contra la Page data source del registry. ──
    # Evidencia estructural pura: sin coincidencia exacta -> CONFLICT retryable.
    slice_lowerings = _lower_confirmed_slices(
        slice_bindings, registry_ds, components_of,
    )

    # ── Caso A: sin hook confirmado → no se inventa fuente. ──
    # Un slice confirmado valido conserva la representacion registry (el slice
    # ya vive en su Page data source), registrado en `slice_lowerings`.
    if not hook_refs:
        return LoweringResult(ir=None, slices=slice_lowerings)

    # ── Un solo hook confirmado, un solo root _pageData (I4). ──
    if len(hook_refs) > 1:
        raise SourceLoweringConflict(
            reason="multiple_hooks",
            hook=", ".join(sorted(hook_refs)),
            detail=(
                f"distinct confirmed hooks {sorted(hook_refs)} would require more "
                "than one page data root; a page has exactly one _pageData"
            ),
            components=components_of,
        )

    hook = next(iter(hook_refs))
    hook_components = hook_refs[hook]
    registry_hook = _registry_hook_name(registry_ds)

    # ── F6.2.1: slice + hook override incompatible. ──
    # El slice confirmado se materializa en el Page data source del registry;
    # un hook override re-cablearia ese root unico. Sin fallback: CONFLICT.
    if slice_lowerings and registry_hook != hook:
        raise SourceLoweringConflict(
            reason="slice_hook_conflict",
            hook=hook,
            detail=(
                f"confirmed slice lowering requires the registry page data source "
                f"{registry_hook!r} to host it, but the confirmed hook {hook!r} "
                "would replace that single _pageData root"
            ),
            components=components_of,
        )

    # ── Caso B: identidad → conservar la representacion registry. ──
    # El registry ya materializa el mismo hook: duplicar el decl/import
    # introduciria un segundo _pageData. NADA que resolver.
    if registry_hook == hook:
        logger.debug(
            "F6.2 source lowering: confirmed hook %s is identical to registry — "
            "keeping registry representation (single _pageData root)",
            hook,
        )
        return LoweringResult(ir=None, slices=slice_lowerings)

    # ── Caso C: override confirmado → probe de import determinista. ──
    matches = probe_hook_modules(hook, repo_files)
    if len(matches) == 0:
        raise SourceLoweringConflict(
            reason="missing_hook",
            hook=hook,
            detail=(
                f"no module under frontend/src exactly exports {hook!r}; the "
                "confirmed source is not physically materializable"
            ),
            components=components_of,
        )
    if len(matches) > 1:
        raise SourceLoweringConflict(
            reason="ambiguous_hook",
            hook=hook,
            detail=(
                f"exactly {len(matches)} modules export {hook!r}: {matches}; "
                "the confirmed source is physically ambiguous"
            ),
            components=components_of,
        )

    # ── D2-A (F6.3): enraizar los campos confirmados en el snapshot fresco ──
    # Solo en override (Caso C): la pagina fisica debe seguir desestructurando
    # el ROOT de cada `_pageData.<selector>` confirmado (o extraer _pageData del
    # hook). Evidencia = el MISMO snapshot fisico (repo_files), via scan_calls:
    # la operacion es "binding congelado -> verificacion fisica", SIN discovery.
    missing_fields = _missing_confirmed_field_roots(confirmed_bindings, hook, repo_files)
    if missing_fields:
        raise SourceLoweringConflict(
            reason="missing_field",
            hook=hook,
            detail=(
                "confirmed binding fields are no longer rooted in the physical "
                f"snapshot: _pageData<.{', _pageData.'.join(missing_fields)}> "
                f"not destructured/extracted from confirmed hook {hook!r}. No "
                "registry fallback; retry re-captures the physical snapshot."
            ),
            components=components_of,
        )

    ir = DataSourceIR(
        type=HOOK_KIND,
        selector=registry_ds.selector if registry_ds else None,
        slices=registry_ds.slices if registry_ds else (),
        hook_name=hook,
        hook_import=f"import {{ {hook} }} from '@/{matches[0]}'",
    )
    drift = tuple(
        SourceDriftItem(
            component=component,
            confirmed=hook,
            registry=registry_hook,
        )
        for component in hook_components
    )
    logger.info(
        "F6.2 source lowering: confirmed hook %s overrides registry %s → %s",
        hook, registry_hook, f"@/{matches[0]}",
    )
    return LoweringResult(ir=ir, drift=drift, slices=slice_lowerings)


def retarget_confirmed_to_component_types(
    confirmed: Mapping[str, DataBinding],
    capability_to_type: Mapping[str, str] | None = None,
) -> dict[str, DataBinding]:
    """Matricula el binding confirmado por `node.type` en vez de la capability.

    FRONTERA: es lowering FISICO, exclusivamente `intent_capability -> node.type`
    del GraphIR. No selecciona capability, target, binding ni reinterpreta el
    WHAT: los `DataBinding` de entrada se trasladan INTACTOS (misma identidad) y
    el UNICO efecto es el cambio de clave del indice de materializacion, porque
    `resolved_bindings.component_props` y el GraphIR compiler estan indexados por
    `node.type` ("KpiRow") mientras que el Confirmed Plan lleva la capability
    ("presentation.kpi_row"). Sin esta matricula el binding confirmado quedaria
    INERTE en el render (latente en F6.1) y el registry volveria a mandar.

    Nunca se cae en silencio: si un binding confirmado no tiene `node.type`
    fisico o dos capabilities confirmadas colisionan en el mismo `node.type`,
    la fuente confirmada no puede materializarse fisicamente -> `CONFLICT`
    (`missing_target` / `ambiguous_target`), no una seleccion discrecional.
    Pura: la unica entrada externa es `capability_to_type`; no lee disco.
    """
    if not confirmed:
        return {}
    mapped = capability_to_type or {}
    out: dict[str, DataBinding] = {}
    for component, binding in confirmed.items():
        node_type = mapped.get(component)
        if node_type is None:
            raise SourceLoweringConflict(
                reason="missing_target",
                hook=None,
                detail=(
                    f"confirmed capability {component!r} has no physical GraphIR "
                    "node.type to lower to; the confirmed binding cannot be "
                    "materialized (no silent registry fallback)"
                ),
                components=[component],
            )
        if node_type in out and out[node_type] is not binding:
            raise SourceLoweringConflict(
                reason="ambiguous_target",
                hook=None,
                detail=(
                    f"confirmed capabilities collide on physical node.type "
                    f"{node_type!r}; two confirmed bindings cannot fuse into one "
                    "physical node (no discretionary selection)"
                ),
                components=[
                    c for c, n in mapped.items()
                    if n == node_type and c in confirmed
                ],
            )
        out[node_type] = binding
    return out


__all__ = [
    "SOURCE_LOWERING_CONFLICT",
    "HOOK_KIND",
    "SLICE_KIND",
    "SliceLowering",
    "SourceDriftItem",
    "SourceLoweringConflict",
    "LoweringResult",
    "collect_repo_files",
    "probe_hook_modules",
    "lower_confirmed_page_source",
    "retarget_confirmed_to_component_types",
]