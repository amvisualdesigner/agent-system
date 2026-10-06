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

Funciones puras: los contenidos llegan ya leidos (`repo_files`, snapshot de la
aplicacion). `collect_repo_files()` es la unica lectura acotada de disco.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Mapping

from app.intent.models import DataBinding
from app.signature.prop_mapper import DataSourceIR, _REACT_HOOK_MAP

logger = logging.getLogger(__name__)

# Causa estructurada de los conflicts de lowering (details.cause). Los conflict
# se emiten en el envelope post-confirmacion existente (`repository_conflict`),
# plan_retryable=True, Run=CONFIRMED: el Plan NO se invalida ni se reinterpreta.
SOURCE_LOWERING_CONFLICT = "source_lowering_conflict"

# solo kinds "hook" son representables fisicamente en Fase 6.2.
HOOK_KIND = "hook"

# Alcance del probe de import: igual que el registry real (frontend/src).
_PROBE_ROOT = os.path.join("frontend", "src")
_PROBE_EXTENSIONS = (".ts", ".tsx")
_SKIP_DIRS = frozenset({"node_modules", ".git", "dist", "build", ".next", "__pycache__"})

_EXPORT_FUNCTION = re.compile(r"export\s+(?:async\s+)?function\s+(\w+)")
_EXPORT_CONST = re.compile(r"export\s+(?:const|let|var)\s+(\w+)")
_EXPORT_NAMED = re.compile(r"export\s*\{([^}]*)\}")


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
        self.reason = reason  # unsupported_kind | missing_hook | ambiguous_hook | multiple_hooks | missing_target | ambiguous_target
        self.hook = hook
        self.detail = detail
        self.components = sorted(set(components or ()))
        super().__init__(f"{reason} (hook={hook}): {detail}")


@dataclass(frozen=True)
class LoweringResult:
    """Salida del lowering del source confirmado.

    ir:
      None   -> conservar la representacion fisica (registry) tal cual:
               Caso A (sin hook confirmado) o identidad (hook == registry).
      DataSourceIR -> representacion fisica del hook confirmado (override).
    drift:
      Items diagnosticos source-level (no bloqueantes) cuando el source
      confirmado difiere del registry.
    """

    ir: DataSourceIR | None = None
    drift: tuple[SourceDriftItem, ...] = ()


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

    # ── Kind gate (I3/matriz F6.2): solo hook es representable. ──
    # Un source no-hook NUNCA cae al registry: es CONFLICT retryable, incluso
    # cuando conviven otros hooks confirmados.
    hook_refs: dict[str, list[str]] = {}
    for component, binding in sorted(confirmed_bindings.items()):
        src = binding.source
        if src is None:
            continue
        if src.kind != HOOK_KIND:
            raise SourceLoweringConflict(
                reason="unsupported_kind",
                hook=src.ref,
                detail=(
                    f"{component}: DataSourceRef kind {src.kind!r} is not lowerable "
                    f"in Fase 6.2 (only {HOOK_KIND!r}); no registry fallback."
                ),
                components=components_of,
            )
        ref = (src.ref or "").strip()
        if not ref:
            raise SourceLoweringConflict(
                reason="unsupported_kind",
                hook=None,
                detail=f"{component}: hook with empty ref (malformed)",
                components=components_of,
            )
        hook_refs.setdefault(ref, []).append(component)

    # ── Caso A: sin hook confirmado → no se inventa fuente. ──
    if not hook_refs:
        return LoweringResult(ir=None)

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

    registry_ds = getattr(resolved_bindings, "page_data_source", None) if resolved_bindings else None

    # ── Caso B: identidad → conservar la representacion registry. ──
    # El registry ya materializa el mismo hook: duplicar el decl/import
    # introduciria un segundo _pageData. NADA que resolver.
    if _registry_hook_name(registry_ds) == hook:
        logger.debug(
            "F6.2 source lowering: confirmed hook %s is identical to registry — "
            "keeping registry representation (single _pageData root)",
            hook,
        )
        return LoweringResult(ir=None)

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
            registry=_registry_hook_name(registry_ds),
        )
        for component in hook_components
    )
    logger.info(
        "F6.2 source lowering: confirmed hook %s overrides registry %s → %s",
        hook, _registry_hook_name(registry_ds), f"@/{matches[0]}",
    )
    return LoweringResult(ir=ir, drift=drift)


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
    "SourceDriftItem",
    "SourceLoweringConflict",
    "LoweringResult",
    "collect_repo_files",
    "probe_hook_modules",
    "lower_confirmed_page_source",
    "retarget_confirmed_to_component_types",
]