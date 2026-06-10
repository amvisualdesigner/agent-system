# Contexto Arquitectónico para Agentes

Planes detallados (vivos):

- `tmp/structural-grounding-fix-plan.md` — pipeline structural (Fase 3 infra)
- `tmp/ui-intent-plan.md` — producto UI + LangGraph (interpret → confirm → apply, catálogo, poda legacy)
- `tmp/phase-5-prop-binding-plan.md` — Phase 5: Semantic Prop Binding + Code Hygiene (✅ COMPLETED)
- `tmp/phase-6-composition-data-flow.md` — Phase 6: Composition Data Flow (Page-centric)
- `tmp/binding-v4-plan.md` — Binding v4: eliminar fuga intent→props, coverage gate, transform registry

---

## Fronteras de responsabilidad (leer antes de implementar)

Dos mundos: **intención** (qué quiere el usuario) vs **realidad** (qué hay en el worktree). Solo **ApplyEngine** los mezcla.

### Reglas no negociables

| Componente | Rol | PUEDE | NO PUEDE |
|------------|-----|-------|----------|
| **`capability_catalog.json`** | Vocabulario | Sinónimos, labels UI, ejemplos, `composition_map` | Leer disco; decidir CREATE/DELETE; saber si algo existe |
| **`IntentInterpreter`** | Propuesta de intención (LLM) | Mapear lenguaje → capability/verbo usando catálogo; **leer snapshot** del index solo para sugerir en UI | Conciliar lifecycle; escribir archivos; llamar `complete_structure` |
| **`PlanCompiler`** | Plan semántico | `ConfirmedIntent` + contrato → `plan` (`skill_ir`, `semantic_frame.actions`) | Importar `StructuralIndex`; scan worktree; inferir EXISTS → CREATE/DELETE |
| **`StructuralIndex`** | Realidad estructural | `exists(cap)`, `resolve_path`, scan worktree | Verbos; elegir contrato; LLM; decidir intención del usuario |
| **`complete_structure()`** | **Conciliador** (no planner) | Dada intención + index → lifecycle `CREATE\|MODIFY\|DELETE\|KEEP` por capability | Inventar nuevos objetivos; LLM; ejecutar sin `structural_index` cuando hay actions |
| **`complete_structure()`** | **Invariante 3B** | Extender MODIFY a hijos SOLO cuando la capability confirmada es un contenedor (`layout.page`, dashboard) | Añadir capabilities no pedidas cuando se modifica un hijo (`presentation.kpi_row` no expande a `presentation.timeseries`) |
| **`GraphIRBuilder`** | Materialización | `StructuralIR` + resolution → grafo | Index, worktree, elegir instancias (sin resolver) |
| **`ApplyEngine`** | **Único punto de mezcla** | `from_worktree()` + `complete_structure(..., index)` + GraphIR + FileOps + 3E + verify | Dejar que otros módulos importen index y decidan lifecycle |

### Flujo canónico

```
[Intención]
  UI → IntentInterpreter → usuario confirma → PlanCompiler → plan
  (catálogo = lenguaje; sin realidad en PlanCompiler)

[Realidad + conciliación]  — solo en ApplyEngine
  StructuralIndex.from_worktree(workspace)
  complete_structure(semantic, contract, structural_index)  ← conciliador
  → StructuralIR (CREATE/MODIFY/DELETE/KEEP)
  → GraphIR → FileOps → CompositionSync (3E) → verify (4)
```

### Qué agente debe hacer según tarea

| Si te piden… | Trabaja en… | No toques… |
|--------------|-------------|------------|
| Sinónimos “line chart”, chips UI | catálogo + `IntentInterpreter` | `complete_structure` lifecycle |
| “No detecta KpiRow en repo” | `StructuralIndex` / 3A aliases | PlanCompiler, LLM prompt |
| “modify dashboard no edita KPI” | 3B `_match_actions` + conciliación en `complete_structure` | Añadir scan a PlanCompiler |
| “Página queda rota tras delete” | 3E `CompositionSync` en ApplyEngine post-fileops | Interpreter |
| “ok con 0 ops” | 3D ApplyEngine + orchestrator | Catálogo |
| Nuevo endpoint que planea y aplica | **Prohibido** — separar interpret / confirm / apply | `POST /agent/plan` monolítico |

### Principio de refactorización agresiva

**No tenemos usuarios externos. No hay compatibilidad hacia atrás. No hay versiones.**

Cuando un cambio requiere eliminar código obsoleto:
- **NO** introducir fases de deprecación (warnings por 2 sprints, migración gradual, etc.)
- **SÍ** delete directo + arreglar tests
- Es más rápido, deja el sistema más fácil de razonar, y eliminamos la deuda técnica de inmediato

Esto aplica a:
- PARAM_ALIASES, heurísticas, exact-match → DELETE, no deprecate
- Caminos de resolución duplicados → DELETE, no coexistir
- Formatos v2 obsoletos → DELETE, no mantener compatibilidad

### Anti-patrones (rechazar en review)

- `PlanCompiler` que llama `StructuralIndex.from_worktree()`.
- `complete_structure` que invoca LLM o reinterpreta el task sin `semantic_frame` confirmado.
- `StructuralIndex` que elige DELETE porque el usuario dijo “remove” (eso es `_resolve_action` con action_map, no el index).
- Duplicar conciliación en orchestrator nodes o en UI.
- Confundir **catálogo** (qué significa “line chart”) con **index** (si `LineChart.tsx` está en disco).
- Introducir una fase de deprecación cuando no hay usuarios externos.
- Mantener código legacy "por si acaso" — si no se usa, se elimina.
- Coexistencia de dos caminos de resolución (v2 + v3) — se migra y se borra el viejo.

---

## Orden de implementación (obligatorio)

No saltar fases. No mantener rutas paralelas “por si acaso”. Detalle en los planes `tmp/`.

| Paso | ID | Qué | Dónde | Depende de |
|------|-----|-----|--------|------------|
| 1 | **3A + UI-0** | Aliases index + `capability_catalog.json` generado desde `skill_registry` | `apply_engine`, `state_adapter`, `scripts/generate_capability_catalog.py` | — |
| 2 | **3B** | Action binding alimentado por catálogo (sustituye `_OBJECT_KEYWORDS` sueltos) | `structural_completion.py`, `semantic_frame.py` | 1 |
| 3 | **UI-5 / UI-6** | `IntentInterpreter` (único LLM) + `PlanCompiler` (determinista) | `backend/app/intent/` | 1, 2 |
| 4 | **UI-2** | API `POST /interpret`, `/confirm`; apply solo con intent confirmado | `backend/app/api/` | 3 |
| 5 | **UI-3** | **LangGraph** nuevo grafo + interrupts humanos (ver abajo) | `orchestrator/` | 4 |
| 6 | **UI-4** | Cliente UI: frases, chips, ≤2 turnos, preview, confirmar | (cliente externo) | 5 |
| 7 | **3D** | Noops honestos + `worktree_capabilities` en context; **preview precisa** (`estimated_files` desde `structural_ops`) | `apply_engine.py`, `agent_confirm.py` | 4, 8 |
| 8 | **3C** | Anchor sin contaminar índice (`src/Page.tsx` espurio) | `structural_completion.py` | 1, 5 |
| — | **Orden actual** | 3C → 3D → E2E reales → LangGraph → UI → 3F → poda → Fase 4 | — | — |
| 5 | **UI-3** | **LangGraph** — grafo con interpret→confirm→apply + resume endpoints | `orchestrator/` | 4 |
| 9 | **3E** | **Page composition sync** — página compositora tras DELETE/CREATE hijo | `composition/sync.py`, `apply_engine.py` | 2, 8 |
| 10 | **3F** ✅ | Transparencia ruta fileops — `pipeline_route` campo directo + conteos preview | `models.py`, `apply_engine.py`, `agent_confirm.py` | 7 |
| 11 | **1.7** ✅ | E2E: 26 tests automatizados en `tests/e2e/`, 7 casos (remove/add KPI/timeseries, update dashboard/metrics, invalid, empty) + convergencia | `tests/e2e/` | 1–10 |
| 12 | **Fase 4** ✅ | **Production shell** — `verify_worktree.py` corre `tsc --noEmit`/`npm run build` post-apply; `verify` en `meta` del resultado | `verify_worktree.py`, `apply_engine.py` | 9, 11 |
| 13 | **UI-7** ✅ | **Poda legacy** — see table below | backend + orchestrator | 12 |
| — | **Fase 2 identity** | **Diferida** → **AHORA** | — | 13 |
| 14 | **Fase 2** ✅ | **Structural Identity** — activar `structural_resolver=True`, estabilizar node_id, evitar colisiones multi-instancia | `feature_flags.py`, `apply_engine.py` | 1–13 |
| 15 | **Phase 4.5** ✅ | **Component Signature Extraction** — parse .tsx, extraer props interface / defaults / data shape hints, alimentar renderer | `signature_extractor/`, renderer | 14 |
| 16 | **Phase 5** ✅ | **Semantic Prop Binding** — `prop_mapper.py` traduce params de contrato a props reales de componente; elimina `metric`→`data`, genera `fetchTimeseries` | `signature/prop_mapper.py`, `compiler.py`, `react_backend.py` | 15 |
| 17 | **Binding v4** ✅ | **Declarative binding system** — kill fallback `props=dict(node.data)`, v4 `data_access.json`, coverage gate, transform registry, 5-step validation | `binding/resolver.py`, `compiler.py`, `data_access.json`, `ui_ir.py` | 16 |

**Regla de catálogo:** paso 1 genera JSON; paso 3 es el primer uso en LLM; paso 2 en index/binding; paso 6 en UI. Nunca tres fuentes (md + registry + prompt).

### Binding v4 — Ejecutado 07-Jun-2026

Los 5 pasos del plan (`tmp/binding-v4-plan.md`) están **completos**:

| Paso | Qué | Resultado |
|------|-----|-----------|
| STEP 0 | Coverage gate | `tests/unit/test_binding_coverage.py` — verifica 12/12 componentes |
| STEP 3 | V4 bindings | `data_access.json` v4 con bindings para 12 componentes + `composition.Page.dataSource` |
| STEP 0b | Re-verify | 12/12 bindings presentes |
| STEP 1 | Kill switch | `compiler.py:185` cambia `props = dict(node.data)` → `props = {}`; `fallback_props` eliminado de `UIComponentNode` |
| STEP 2 | Hard rule | comentario + `tests/unit/test_compiler_hard_rule.py` (5 invariants: compiler no lee node.data para props) |

**Decisiones de diseño tomadas durante ejecución:**
- `required: true` eliminado de todos los bindings v4 — el SSOT gate del compiler (component signatures) ya maneja requiredness. El flag de binding era redundante y causaba errores con componentes no-usados en el grafo.
- V4 bindings respetan props ya resueltas por Page slices (no sobreescriben `_pageData.kpiData` con transforms locales).
- `resolve()` acepta `workspace` opcional para backward compat con tests Phase 6 (producción usa global SSOT).
- `load_workspace_data_source()` en `prop_mapper.py` para carga legacy de workspace v3.

**Test count:** 671 passed, 2 skipped (vs 540 antes del refactor).

**Anti-patrón (archivado):** eliminar `props = dict(node.data)` antes de que todos los componentes tengan binding — ya no aplica porque el kill switch se ejecutó con coverage 100%.

### Binding v5 — En progreso (07-Jun-2026)

**Objetivo:** Binding v4 como fuente de UI props para props escalares/UI, manteniendo
Page slices como fuente autoritativa para datos estructurados/API.

**Hallazgo crítico (F3 → REVERTIDO):** Binding-wins per-component es incorrecto.
Binding NO puede reemplazar API data (Timeseries.data espera `Point[]`, binding produce
`string[]`). Binding es *selection/mapping/light transform*, no *data computation*.

**Nuevos dominios:**

| Binding-native (props escalares/UI) | Data-driven (API/structured) |
|-------------------------------------|------------------------------|
| title, placeholder, format, label | data, series, structured datasets |
| KpiRow (metrics→items→label/value) | Timeseries.data (Point[]) |
| MetricCard.value, MetricCard.label | BarChart.data, AnalyticsTable.data |
| SearchBar.placeholder, Drilldown.label | Cualquier prop con shape complejo |

**Estrategia corregida:** Binding-wins por-prop, no por-componente. Solo props donde
binding produce el shape correcto (mismo tipo TypeScript).

| Fase | Qué | Enfoque | Estado |
|------|-----|---------|--------|
| **F0** | Dual-write | BindingResolver + slices en paralelo, solo logear divergencia | ✅ COMPLETED |
| **F1** | **Registry-driven diff** | Diff space registry-driven. binding_missing captura props no resolubles | ✅ COMPLETED |
| **F2** | Canonicalizar `from` fields | Timeseries.data.from: "metric" → "timeseries_metric" | ✅ COMPLETED |
| **F3** | ❌ REVERTIDO | Binding-wins per-component es incorrecto. Binding no puede reemplazar API data | ❌ CANCELED |
| **F3b** | Binding-wins por-prop | Solo props escalares/UI donde binding produce el shape correcto | 🔲 |
| **F4** | Slice elimination | Por prop, cuando binding wins está activo | 🔲 |
| **F5** | Registry inversion | SSOT real, intent desde registry, eliminar legacy | 🔲 |

**F0+F1+F2 implementación real:**
- `backend/app/binding/models.py`: `BindingDiffItem` + `BindingDiff` dataclasses con `divergent_count`/`binding_missing_count`/`total_overlap`
- `backend/app/binding/resolver.py`: `_MISSING` sentinel + `_classify_equivalence()` + `_get_declared_prop_names()` + `_compute_binding_diff()` acepta `declared_prop_names` para diff space registry-driven + bloque dual-write en `resolve()`
- `backend/config/data_access.json`: Timeseries.data.from canonicalizado a `timeseries_metric`
- Logs: `DUAL_WRITE_BINDING_MISSING` (warning), `DUAL_WRITE_DIVERGENT` (warning), `DUAL_WRITE_STRUCTURAL_EQ`/`_SEMANTIC_EQ` (debug)
- NO cambia output — solo mide y logea. **671 tests pass.**

**3 clases de equivalencia en dual-write (+1 para F1):**
- `binding_missing`: Binding registry declara el prop pero no puede resolverlo (from field mismatch) → BLOQUEA binding wins
- `structural_equivalent`: JSVariable vs resolved value (mismo slot, distinta representación) → NO bloquea, pero REQUIERE verificar type/shape
- `semantic_equivalent`: Mismo shape/tipo → NO bloquea
- `divergent`: Distinto valor/shape real → BLOQUEA binding wins

**Plan detallado:** `tmp/binding-v5-ownership-plan.md`

**Regla de poda:** paso 13 solo cuando pasos 1–12 pasen los 3 prompts UI + verify verde (ver validación abajo).

**Catálogo vs index vs composition:** catálogo = lenguaje (no lee repo); index = existencia; 3E = sincroniza página compositora; Fase 4 = valida build.

---

## Orchestrator — LangGraph (`orchestrator/`)

**Hoy (UI-3 completado 31-May-2026):** grafo con **human-in-the-loop** mediante fases separadas. Cada fase es una invocación independiente del mismo grafo, con conditional entry point:

```
entry ──┬→ interpret (calls /agent/interpret)
         ├→ confirm (calls /agent/confirm)
         ├→ call_apply (calls /agent/apply)
         └→ return_result (terminal)

interpret ──┬→ interpret (max 2 retry por clarification)
            └→ return_result → END (phase=awaiting_confirmation)

confirm → validate_plan ──┬→ return_result → END (error)
                           └→ return_result → END (phase=awaiting_apply)

call_apply → return_result → END (phase=completed/error)
```

- Estado: `AgentState` en `state.py` (+ `start_node`, `interpretation`, `confirmed_intent`, `plan_preview`).
- Compilado: `graph.py` → `compiled_graph.ainvoke()` desde `main.py` (invocación por fase).
- **Pausa entre fases:** el estado se persiste en `return_result` vía `store.save_snapshot()`.
- **Reanudación:** endpoints `POST /run/{id}/confirm` y `POST /run/{id}/apply` leen el snapshot, construyen nuevo estado inicial con `start_node` apropiado, e invocan el grafo de nuevo.
- **SSE:** `interpretation_ready` (tras interpret), `plan_preview_ready` (tras confirm), `result` (tras apply).
- **Responsabilidad:** LangGraph solo **ordena**; lógica de intent en backend (`IntentInterpreter` / `PlanCompiler`). Los nodos son thin wrappers sobre `backend_client`.

**Anti-patrón:** duplicar planner o semantic en nodos orchestrator — prohibido.

---

### Máquina de estados: RunPhase

Antes de construir LangGraph, se introdujo una **máquina de estados explícita** que gobierna el lifecycle de cada run. Todos los endpoints la validan.

**Fases:**

| Fase | Significado | Transiciones válidas hacia |
|------|-------------|---------------------------|
| `interpreting` | Procesando interpretación LLM | `awaiting_confirmation`, `cancelled` |
| `awaiting_confirmation` | Draft listo, esperando confirmación humana | `confirmed`, `cancelled` |
| `confirmed` | Plan compilado, listo para aplicar | `applying`, `cancelled` |
| `applying` | ApplyEngine en ejecución | `completed`, `failed`, `cancelled` |
| `completed` | Apply exitoso | *(terminal)* |
| `failed` | Apply falló | *(terminal)* |
| `cancelled` | Usuario canceló | *(terminal)* |

**Self-transitions** (misma fase → misma fase) siempre permitidas para idempotencia.

**Implementación:**

| Archivo | Rol |
|---------|-----|
| `backend/app/intent/models.py` | `RunPhase` enum, `validate_transition()`, `RunState` dataclass |
| `backend/app/state/run_state.py` | `save_run_state()`, `load_run_state()`, `transition_phase()` — store por `run_id` en JSON files |
| `backend/app/api/agent_interpret.py` | Persiste `interpretation_draft`, transiciona a `awaiting_confirmation` |
| `backend/app/api/agent_confirm.py` | Valida fase `awaiting_confirmation` o `confirmed`; persiste plan; transiciona a `confirmed`; **idempotente** (re-confirm retorna cache) |
| `backend/app/api/agent_apply.py` | Valida fase `confirmed` + `confirmed_intent` existe + `gate.blocked=False`; transiciona `confirmed→applying→completed\|failed` |

**Decisiones de diseño:**

1. **Source of truth:** `InterpretationDraft` persistido es la base. `/confirm` recibe `ConfirmedIntent` (edits del usuario sobre el draft). NO re-interpreta el mensaje original.
2. **Idempotencia en confirm:** si ya está en `confirmed` y tiene `compiled_plan` cacheados, devuelve el mismo plan. No recompila.
3. **Gate en apply:** `apply_engine()` rechaza si `gate.blocked == True` (validado antes de llamar al engine).
4. **Terminal states:** `completed`, `failed`, `cancelled` no admiten transiciones salientes.

**Validación (29 tests en `tests/intent/test_state_machine.py`):**

| Test | Lo que valida |
|------|---------------|
| 1: `interpret_sets_awaiting_confirmation` | POST /interpret → phase=`awaiting_confirmation`, NO apply |
| 2: `confirm_without_draft_rejected` | POST /confirm sin draft previo → `rejected` |
| 3: `apply_without_confirmed_intent` | POST /apply sin `confirmed_intent` → fail |
| 4: `apply_gate_blocked` | POST /apply con `gate.blocked=True` → fail |
| 5: `double_confirm_idempotent` | Re-confirm devuelve cached plan, no cambia estado |
| Transición matrix | Todas las allowed/forbidden transiciones exhaustivamente cubiertas |
| Full flow happy path | interpret → confirm → apply (con state validation en cada paso) |
| Cancel mid-flow | Cancel desde `awaiting_confirmation` funciona; luego no se puede confirmar |

---

## Estado del pipeline

| Fase | Estado | Notas |
|------|--------|-------|
| **1 — Structural grounding** | ✅ | `StructuralIndex`, `has_resolved_keep_state`, builder puro |
| **Stabilize — Multi-instance** | ✅ | `list[ComponentInstanceInfo]`, exact match, node_id estable |
| **2 — Identity layering** | ✅ | Builder usa `instance_id`; `structural_resolver=True` en runtime |
| **2.5 — Graph viability + contract guard** | ✅ | Anchor + injection guard; ver efectos colaterales abajo |
| **1.7 — Regression E2E** | ✅ | `tests/e2e/test_full_pipeline.py` + `test_real_repo_integration.py` (521 lines, 15 tests contra repos reales) |
| **3A — Repo aliases** | ✅ 30-May-2026 | `FILENAME_ALIASES` centralizado, `_build_name_map` integrado |
| **3B — Action binding** | ✅ 30-May-2026 | `line`→timeseries en `_OBJECT_KEYWORDS`; post-passes metrics→kpi\_row y modify dashboard→hijos del contrato |
| **UI-5 — IntentInterpreter** | ✅ 30-May-2026 | `backend/app/intent/interpreter.py` — LLM 7B + catálogo + post-validation; único entrypoint LLM |
| **UI-6 — PlanCompiler** | ✅ 30-May-2026 | `backend/app/intent/plan_compiler.py` — determinista, sin LLM, sin StructuralIndex |
| **UI-1 — Models** | ✅ 30-May-2026 | `InterpretationDraft`, `ConfirmedIntent`, `IntentAction`, `CompiledPlan` |
| **UI-2 — API + state machine** | ✅ 30-May-2026 | `POST /agent/interpret`, `/confirm`, `/apply` con guard de `RunPhase`; `backend/app/state/run_state.py`; 29 tests |
| **3C — Anchor sin contaminar** | ✅ 30-May-2026 | `_ensure_graph_viability()` prefiere existing caps via `StructuralIndex`; 5 tests E2E (AddTrendChart) |
| **3D — UX honesta** | ✅ 30-May-2026 | `estimated_files` derivado de `structural_operations` via `_build_capability_file_map()`; no sobrestimación del contrato completo; 5 tests de convergencia |
| **3E — Composition sync** | ✅ 30-May-2026 | `_sync_composition_parents()` post-pass C promueve parent page KEEP→MODIFY cuando child es CREATE/DELETE; `_build_contract_composition_map()` deriva mapa desde `ast_template.slots` + `capabilities` |
| **UI-3 — LangGraph** | ✅ 31-May-2026 | Grafo con `interpret → confirm → validate_plan → call_apply`; conditional entry point; resume via `POST /run/{id}/confirm` y `/apply`; SSE `interpretation_ready` / `plan_preview_ready` |
| **3F — Route transparency** | ✅ 31-May-2026 | `pipeline_route` en `FileOp` como campo directo; `"constraint" \| "renderer" \| "delete_inject"`; conteos `routes` en `plan_preview` |
| **File path overrides** | ✅ 30-May-2026 | `BackendConfig.file_path_overrides` mapea `GraphIRNode.type` → full relative path desde `_discover_repo_capability_files()`; `FilePathResolver.resolve()` usa override primero |
| **Backward compat apply** | ✅ 30-May-2026 | `_auto_init_run_state()` bridge para planes legacy (orchestrator sin /interpret); auto-inicializa run state desde `semantic_frame` |
| **CompiledPlan.actions** | ✅ 30-May-2026 | `actions` field en `CompiledPlan` + zero-loss invariant (semantic_action_count > 0 ⇒ plan_actions no vacío) + preflight guard en ApplyEngine |
| **4 — Production shell** | ✅ 01-Jun-2026 | `verify_worktree.py` — `tsc --noEmit` / `npm run build` post-apply; fail-closed (no commit si falla); `verify` en `meta` del resultado |
| **Phase 4.5 — Signature Extraction** | ✅ COMPLETED 01-Jun-2026 | `backend/app/signature/extractor.py` + override en renderer — eliminar `verify_failed` por typing |
| **Phase 5 — Semantic Prop Binding** | ✅ COMPLETED 05-Jun-2026 | `backend/app/signature/prop_mapper.py` — BindingIR + DataSourceIR (framework-agnostic) + `resolve_props()` (solo BindingIR resolution tras PR1) + phantom param drift detection + provenance tracking con `semantic_fidelity_score` honesto. Hook hoisting + JSVariable + `_inject_data_imports()`. 45 tests. |
| **Phase 6 — Composition Data Flow** | ✅ COMPLETED 06-Jun-2026 | `prop_mapper.py`: DataSourceIR.slices + DataSlice dataclass + v3 parsing + `load_page_data_source()`; `data_access.json` v3 (Page-only dataSource + slices); `react_backend.py`: `_page_hook_declaration()` elimina `_hoist_hook_bindings` general; `compiler.py`: `validate_node()` enforce children prop-only bajo Page, children reciben workspace=None; `ui_ir.py`: `page_data_source` field; 62 tests. Scope guard: Page solo slices, no transformaciones. |
| **Binding Resolution Architecture** | ✅ COMPLETED 06-Jun-2026 | `binding/models.py` + `binding/resolver.py` (CREADOS); `prop_mapper.py` despojado de PARAM_ALIASES/exact match/heuristic; `compiler.py` recibe ResolvedBindings (NO contract_params); `react_backend.py` + `constraint/renderer.py` reciben resolved_bindings; `_distribute_page_slices()` eliminado. 663 tests. |

### Archivos modificados recientemente (UI-3 / 31-May-2026)

| Archivo | Cambio |
|---------|--------|
| `orchestrator/state.py` | + `start_node`, `interpretation`, `confirmed_intent`, `plan_preview` |
| `orchestrator/graph.py` | Conditional entry point + nodos `interpret`/`confirm` |
| `orchestrator/models.py` | + `ConfirmRequest`, `ApplyRequest` |
| `orchestrator/backend_client.py` | + `call_interpret()`, `call_confirm()`; elimina `call_plan()` |
| `orchestrator/main.py` | + `POST /run/{id}/confirm`, `/apply` resume endpoints |
| `orchestrator/nodes/interpret.py` | Nuevo — llama `/agent/interpret`, max 2 clarification retries |
| `orchestrator/nodes/confirm.py` | Nuevo — llama `/agent/confirm`, emite `plan_preview_ready` |
| `orchestrator/nodes/validate_plan.py` | Simplificado: sin retry al legacy planner |
| `orchestrator/nodes/return_result.py` | Persiste `interpretation`, `confirmed_intent`, `plan_preview` |
| `orchestrator/nodes/call_plan.py` | Eliminado (reemplazado por `interpret.py`) |
| `backend/app/api/agent_apply.py` | Fix `_auto_init_run_state`: `asdict` + legacy `"object"` field |

### Archivos modificados recientemente (3C/3D/3E / path overrides / actions)

| Archivo | Rol |
|---------|-----|
| `backend/app/engine/structural_completion.py` | 3C `_ensure_graph_viability()` + 3E `_sync_composition_parents()`, `_build_contract_composition_map()` post-pass C |
| `backend/app/api/agent_apply.py` | `_auto_init_run_state()` backward compat + preflight guard (confirmed_intent.actions) |
| `backend/app/graphir/path_resolver.py` | `FilePathResolver.resolve()` con `file_path_overrides` precedence |
| `backend/app/graphir/backends/base.py` | `BackendConfig.file_path_overrides` field |
| `backend/app/intent/models.py` | `CompiledPlan.actions` field |
| `backend/app/intent/plan_compiler.py` | `_build_actions()` + zero-loss invariant guard |
| `backend/app/engine/apply_engine.py` | Preflight guard (`no_semantic_frame`), `file_path_overrides` construction |
| `backend/app/graphir/models.py` | `PipelineRoute` type + `FileOp.pipeline_route` field directo |
| `backend/app/graphir/constraint/context.py` | `PipelineRoute` literal `"legacy"` → `"renderer"` |
| `backend/app/graphir/constraint/renderer.py` | Tagging via `fop.pipeline_route = route` (no metadata) |
| `backend/app/graphir/backends/react_backend.py` | `FileOp(..., pipeline_route="renderer")` |
| `backend/app/api/agent_confirm.py` | + `routes` count en `plan_preview` |

### Archivos eliminados (UI-7 poda legacy, 31-May-2026)

| Módulo | Archivos | Razón |
|--------|----------|-------|
| Legacy planner | `app/api/agent_plan.py`, `app/planner/` (3 files), `semantic_engine/` (4 files), `utils/workspace.py`, `contracts/plan_request.py` | Reemplazado por IntentInterpreter + PlanCompiler |
| Intent decomposition | `graphir/intent_decomposition.py`, `param_extractor.py`, `intent_structure.py`, `intent_embedding.py`, `intent_coverage.py`, `intent_governance.py` | Reemplazado por IntentInterpreter |
| Semantic frame builder | `graphir/semantic_frame.py` → `build_frame_from_decomposition`, `_extract_*`, `_compute_frame_confidence` | Ahora es solo dataclasses + _OBJECT_KEYWORDS |
| Deprecated intent models | `IntentNode`, `IntentType`, `IntentExtensionRegistry`, `_INTENT_TO_*` maps | No usados en nuevo pipeline |
| Bridge functions | `_auto_init_run_state`, `graphir_ready_to_intent_plan` | No necesarios con nuevo flujo |
| Dead keywords | `OBJECT_KEYWORDS_EXTRA` en `aliases.py` | Nunca importado |
| Dead handlers | `call_plan` en `ui/index.html` | Nodo eliminado |
| Test files | `tests/legacy_contract/`, `test_graphir.py`, `test_semantic_frame.py`, `test_graphir_phase3.py`, `test_graphir_phase4.py`, `test_bi_editor.py`, partes de `test_gate.py`/`test_structural_coverage.py` | Probaban código eliminado |
| Legacy imports | `main.py` → `agent_plan_router`, `validate_plan.py` → formato plan legacy | No necesarios |

### Archivos nuevos (UI-5 / UI-6 / state machine)

| Archivo | Rol |
|---------|-----|
| `backend/app/intent/models.py` | `InterpretationDraft`, `ConfirmedIntent`, `IntentAction`, `CompiledPlan`, `RunPhase`, `RunState` |
| `backend/app/intent/interpreter.py` | IntentInterpreter — contrato por keywords + LLM actions + post-validation |
| `backend/app/intent/plan_compiler.py` | PlanCompiler — `ConfirmedIntent` + contrato → `CompiledPlan` (skill_ir, semantic_frame, intents) |
| `backend/app/intent/llm_client.py` | LLM client limpio (no depende de legacy planner) |
| `backend/app/catalog/loader.py` | Runtime loader para `capability_catalog.json` |
| `backend/app/state/run_state.py` | State store by run_id (JSON files) |
| `backend/app/api/agent_interpret.py` | `POST /agent/interpret` + state persistence |
| `backend/app/api/agent_confirm.py` | `POST /agent/confirm` + phase validation + idempotency |
| `backend/app/api/agent_apply.py` | `POST /agent/apply` + phase guard + gate check |
| `tests/intent/test_interpreter.py` | Contract selection + action detection + validation (21 tests) |
| `tests/intent/test_plan_compiler.py` | PlanCompiler (5 tests) |
| `tests/intent/test_catalog_loader.py` | Catalog loader (6 tests) |
| `tests/intent/test_state_machine.py` | State machine transitions + store + 5 validation scenarios (29 tests) |
| `tests/intent/test_e2e_flow.py` | 4 E2E casos (remove, update metrics, update dashboard, add trend chart) + gate validation (23 tests) |

### Reglas de integración (UI-5/UI-6 / state machine)

- **IntentInterpreter** es el ÚNICO entrypoint LLM. Reemplaza `skill_ir_planner.py` + `decompose_task` + `build_frame_from_decomposition` en el nuevo flujo.
- **PlanCompiler** es determinista. Recibe `ConfirmedIntent` + contrato → `CompiledPlan` con `skill_ir`, `semantic_frame`, `intents`.
- **Ninguno** llama `complete_structure()`, `StructuralIndex`, ni escribe archivos.
- **POST /agent/interpret** devuelve `InterpretationDraft` **y persiste** draft + fase `awaiting_confirmation`.
- **POST /agent/confirm** valida fase `awaiting_confirmation` o `confirmed`; es **idempotente** (cached si ya compilado). Persiste plan + preview.
- **POST /agent/apply** valida fase `confirmed` + `confirmed_intent` existe + `gate.blocked=False` antes de ejecutar.
- El lifecycle (CREATE/MODIFY/DELETE) lo decide `complete_structure()` dentro de `ApplyEngine`.
- Tests: ~84 nuevos tests en `tests/intent/` (incluyendo 29 de state machine + 23 E2E + 8 composition sync + 5 FilePathResolver + actualizados regression). 804 total (3 skipped).

### Validación UI (29-May-2026, `agent-test-repo`)

| Run | Prompt | Resultado |
|-----|--------|-----------|
| `085d1979` | Remove KPI row | DELETE `KpiRow.tsx` OK; side effect `src/components/Page.tsx` (anchor) |
| `a8b5bebc` | Update KPI metrics | Solo scaffold `Page.tsx`; **no** edita `KpiRow.tsx` |
| `887d09ab` | Remove line chart | `ok` + 0 ops (noop silencioso) |

**Causa:** `StructuralIndex` no indexa `LineChart.tsx` / `SalesOverviewPage.tsx`; `remove`+`line` → `action_map={}`; `modify dashboard` → solo `layout.page`. `repo_snapshot=[]` en noop **no** implica worktree vacío.

---

## Pipeline Estructural — Bugs Conocidos y Decisiones

### Bug 1 (Fase 1 COMPLETADA): Structural Grounding roto

**Síntoma:** `clarification_needed` con `repo_snapshot=[]` cuando el worktree tiene estructura válida.

**Causa raíz:** El pipeline no reenvía `repo_state` al GraphIR builder (dead code en `_add_intent_node`). Cuando todas las capabilities se resuelven a KEEP (request declarativo sobre repo existente), `operations=[]` → `AmbiguousStructuralTargetError` → `clarification_needed`.

**Fix (implementado Fase 1):**
1. `StructuralIndex` (`backend/app/engine/structural_index.py`) — interfaz explícita worktree → pipeline. Única fuente de verdad del worktree.
2. `StructuralIR.has_resolved_keep_state` — distingue KEEP real de SAFE_SKIP. Early return en `apply_engine.py` cuando all-KEEP.
3. `GraphIRBuilder` limpio — eliminado `repo_state`, eliminado `_derive_component_instance_path`. Builder es puro: solo recibe `StructuralIR + StructuralResolution`.
4. `StructuralResolver` con fallback por action type — CREATE usa `StructuralIndex.derive_create_path()`, MODIFY/DELETE/KEEP usan `structural_index.resolve_path()`.
5. `apply_engine.py` migrado — `StructuralIndex.from_worktree()` reemplaza `load_current_state()`, early return `has_resolved_keep_state`, `structural_index` se pasa a todas las funciones.

**Archivos modificados:**
- `backend/app/engine/structural_index.py` (CREADO)
- `backend/app/engine/structural_completion.py` — todas las funciones aceptan `structural_index` en vez de `repo_state`
- `backend/app/graphir/builder.py` — eliminado `repo_state`, `_derive_component_instance_path`, `contract_id` de `_add_intent_node`
- `backend/app/graphir/structure/resolver.py` — fallback por action type via `StructuralIndex`
- `backend/app/engine/apply_engine.py` — early return all-KEEP, migrado a `StructuralIndex`

### Bug 2 (Fase Stabilize COMPLETADA 29-May-2026): Multi-instance capability

**Síntoma:** Archivos duplicados ignorados, identity colapsada.

**Puntos de fallo originales:**
- `state_adapter.py`: `dict[str, ComponentInstanceInfo]` → overwrite en multi-instancia
- `apply_engine.py`: `_build_name_map()` con substring matching → colisiones
- `builder.py`: `_node_id_for_capability()` → node_id = graphir_type → identity colapsada

**Fix (implementado Appendix A + consultor review):**
1. **`state_adapter.py`**: `dict[str, list[ComponentInstanceInfo]]`, `instance_id: int` auto-incremental por capability, paths únicos con índice (`kpi_row` → `kpi_row:1`).
2. **`structural_index.py`**: type interno adaptado, `resolve_all_paths()` → `list[str]`, `resolve_path()` compatible retorna instance 0.
3. **`builder.py`**: `node_id = f"{graphir_type}:{instance_id}"` (Fase 2 parcial en builder); Stabilize usaba `capability` — ver plan Fase 2.
4. **`apply_engine.py` (x2)**: substring matching eliminado en `_discover_repo_capabilities` y `_discover_repo_capability_files` → exact match + normalized exact match (strip `_`, `-`). Helper `_match_file_to_capability()` unificado.
5. **`structural_completion.py`**: `_capability_from_path()` migrado al helper unificado.
6. **`resolver.py`**: deterministic fallback (sorted path) antes de `AmbiguousStructuralTargetError`.
7. **Tests**: 4 test helpers adaptados a `dict[str, list[...]]`, assertions actualizados a nuevo `node_id` formato. **649 passed, 0 failures** de Fase Stabilize (57 pre-existing failures por `REPO_ROOT` env var).

**Decisiones clave:**
- Rechazado `instance_idx` como node_id (depende de orden inestable). Aceptado `f"{graphir_type}:{capability}"`.
- Rechazado identidad cegada en `list`. Aceptado `instance_id: int = 0` auto-incremental.
- Rechazado substring matching. Aceptado exact + normalized exact. Sin NLP.
- Rechazado ambiguity raising. Aceptado deterministic fallback sorted path.

### Fase 2.5 COMPLETADA 29-May-2026: Graph viability + Contract injection guard

**Síntomas:** 
- `remove KPI row from dashboard` → elimina KPI row pero también crea Page.tsx y Timeseries.tsx no pedidos (overgeneration vía contract expansion)
- `remove chart` con page existente → builder recibe 0 nodos → `clarification_needed` (graph vacío)

**Fix (implementado 29-May-2026):**

**1. Contract injection guard** (`complete_structure()`: línea 762):
```
Si hay actions del usuario + structural_index conocido + capability sin action_verb
y no existe en repo → SAFE_SKIP (no CREATE no pedido)
```
Solo aplica en modo operacional (el usuario explicitó actions). En modo declarativo (sin actions), todo CREATE como antes.

**2. Anchor preservation** (`_ensure_graph_viability()`, post-loop):
```
Si 0 builder nodes (ningún CREATE/MODIFY) + mix de KEEP+DELETE
→ preservar anchor más estructural como MODIFY/SAFE_COMPLETE
Prioridad: layout.page > domain.* > primera capability válida
```
No toca: all-KEEP (noop capturado por `has_resolved_keep_state`) ni all-DELETE (válido).

**Resultado:** `remove KPI row` → DELETE kpi_row + MODIFY/SAFE_COMPLETE layout.page → builder produce 1 nodo → graph viable.

**Efecto colateral conocido (Fase 3C):** si `layout.page` no está en index, anchor crea `src/components/Page.tsx` y puede ser la **única** instancia indexada tras el run — envenena worktrees siguientes.

### Fase 1–13 COMPLETADAS (01-Jun-2026)

| Fase | Estado | Logro |
|------|--------|-------|
| **3A — Repo alignment** | ✅ | `FILENAME_ALIASES` centralizado, `_build_name_map` integrado |
| **3B — Action binding** | ✅ | `line`→timeseries en keywords; post-passes metrics→kpi\_row y dashboard→hijos |
| **3C — Anchor sin contaminar** | ✅ | Prefiere existing caps via `StructuralIndex` |
| **3D — UX honesta** | ✅ | `estimated_files` derivado de structural_ops, no contrato completo |
| **3E — Composition sync** | ✅ | `_sync_composition_parents()` post-pass C |
| **3F — Route transparency** | ✅ | `pipeline_route` directo en `FileOp` + conteos preview |
| **4 — Production shell** | ✅ | `verify_worktree.py` — `tsc --noEmit` / `npm run build`; fail-closed |
| **UI-1–7, LangGraph, poda** | ✅ | Pipeline completo interpret→confirm→apply; state machine; SSE |

---

## Phase 4.5 — Component Signature Extraction (✅ COMPLETED 01-Jun-2026)

**Problema resuelto:** El renderer generaba código TypeScript con tipos genéricos (`any[]`, `ReactNode`) porque desconocía las interfaces reales de los componentes existentes en el repo. `verify_failed` por type mismatch (`KpiRow` espera `KpiItem[]`, no `any[]`).

**Solución:** Extraer firmas de componentes desde los `.tsx` existentes y alimentar al renderer como **override** (no transformación del IR).

### Diseño MVP

| Capa | Archivo | Qué hace |
|------|---------|----------|
| **Extractor** | `backend/app/signature/extractor.py` | Regex sobre `.tsx` del worktree: captura `interface XxxProps` / `type Props` como texto crudo + **todos los bloques type/interface** como `extra_types` + nombres de props como `prop_names` + imports del encabezado |
| **Registry** | `BackendConfig.component_signatures` | `{ComponentType: {props, extra_types, prop_names, imports}}` |
| **Injection** | `apply_engine.py` (~L666) | Antes de construir `BackendConfig`, llama al extractor con el workspace |
| **Override** | `react_backend.py: _render_signature()` | Genera archivo completo: imports filtrados + extra_types + props_block + export con `_props` |
| **Prop filtering** | `react_backend.py: _render_children()` + constraint `_materialize_composition` | Filtra props de hijos contra `prop_names` conocidos para evitar pasar props que no existen en la interfaz real |

### Regla cardinal

```
SI signature existe → usar como SOURCE OF TRUTH (override completo)
NO existe → hardcoded fallback
NO mezclar signature con lógica del IR
```

Esto evita que Phase 4.5 contamine el sistema de planning. No hay "adaptar IR a signature", solo "render signature as override".

### MVP scope (entrega 1)

- [x] Extraer `interface XxxProps { ... }` y `type Props = { ... }` como bloque crudo
- [x] Capturar imports del archivo (líneas `import ... from ...`)
- [x] Registry en `BackendConfig`
- [x] Override en generadores
- [ ] Extraer destructure names de function signature (aplazado a entrega 2)
- [ ] Extraer default values (aplazado a entrega 2)

**MVP completado 01-Jun-2026.** Todos los generadores en `react_backend.py` usan `_render_signature()` para override de interfaz. `apply_engine.py` inyecta signatures via `extract_signatures(workspace)`. Tests: 467 passed, 2 skipped.

### Fixes post-MVP (01-Jun-2026, tras E2E `verify_failed`)

| Bug | Síntoma | Fix |
|-----|---------|-----|
| Tipos dependientes faltantes | `Cannot find name 'Point'` / `'KpiItem'` | Extractor ahora incluye `extra_types`: **todos** los bloques type/interface del `.tsx` |
| Imports no usados en stub | `'styles' declared but never read` | `_render_signature()` filtra imports: solo `React` y `import type` |
| Props incorrectas a hijos | `metric no existe en Props` (Timeseries espera `data`, `title`) | `_render_children()` + constraint renderer filtran props contra `prop_names` conocidos |
| Parámetro no usado | `'props' declared but never read` | Stub firma usa `_props` (convención TS para unused) |

**No negociable:** Es una **capa extractora sobre el worktree**, no confundir con `StructuralIndex` (paths) ni `capability_catalog.json` (lenguaje). NO hace razonamiento cross-component ni inferencia de intención global.

**Dependencia:** Fase 2 completada (✅ 01-Jun-2026).

---

## Phase 5 — Semantic Prop Binding (✅ COMPLETED 02-Jun-2026)

**Problema resuelto:** El renderer generaba `<Timeseries />` sin datos porque el prop filtering eliminaba `metric` (no existe en `TimeseriesProps`). `tsc` pasaba pero la UI era semánticamente vacía.

**Solución:** `prop_mapper.py` traduce params de contrato (`timeseries_metric: "revenue"`) a props reales de componente (`data={fetchTimeseries("revenue")}`) usando 3 capas: `PARAM_ALIASES` determinista + `data_access.json` (tabla de símbolos explícita) + fallback heurístico.

### Diseño

| Capa | Archivo | Qué hace |
|------|---------|----------|
| **PropMapper** | `backend/app/signature/prop_mapper.py` | `resolve_props()` con pipeline 5 reglas: exact match → alias → data_access → auto title → skip |
| **Symbol table** | `{workspace}/.opencode/data_access.json` | Registro explícito de `{component: {prop: {imports, expression}}}` con templates `{param}` |
| **Alias map** | `PARAM_ALIASES` | `metric→data`, `timeseries_metric→data`, `kpi_metrics→data`, `chart_type→type` |
| **Integration** | `UIIRCompiler.compile()` | PropMapper se invoca durante `GraphIR → UIComponentTree`, no como post-paso |
| **Import injection** | `ReactBackend._inject_data_imports()` | Post-pass inserta imports de `data_access.json` en header del archivo generado |
| **Data carrier** | `UIComponentNode.data_imports` | `tuple[str, ...]` separado de `props` para no violar bloqueo de `__post_init__` |

### Pipeline de binding

```
contract_params
    │
    ▼
┌──────────────────────────────┐
│  PARAM_ALIASES (determinista)│  ← alias map fijo
└──────────┬───────────────────┘
           ▼
┌──────────────────────────────┐
│  data_access.json            │  ← registro explícito (si existe)
│  {Timeseries: {data: {       │     source of truth de data access
│    expression: "fetchTS({m})"│
│  }}}                         │
└──────────┬───────────────────┘
           ▼
┌──────────────────────────────┐
│  Fallback heurístico         │  ← solo si no existe data_access.json
│  con WARNING log             │
└──────────────────────────────┘
```

### BindingResult

```python
@dataclass
class BindingResult:
    props: dict[str, Any]       # Props resueltas para JSX
    imports: list[str]          # Imports de data_access.json
    warnings: list[str]         # Drift warnings
```

### Regla cardinal

```
PropMapper traduce params semánticos → props de componente.
NUNCA decide qué componente crear ni qué contrato elegir.
NO toca StructuralIndex, PlanCompiler, ni IntentInterpreter.
```

### Punto de integración (decisión arquitectónica clave)

**NO** se modifica `GraphIRNode.data` (es inmutable, `frozen=True`).

**SÍ** se integra en `UIIRCompiler.compile()`:

```
GraphIR
   ↓
UIIRCompiler.compile(graph, layout, contract_params, component_signatures)
   ↓
resolve_props(component_name, contract_params, component_signature)
   ↓
BindingResult(props, imports, warnings)
   ↓
UIComponentNode(props=..., data_imports=...)
   ↓
ReactBackend.render_tree()
   ↓
_inject_data_imports() mergea signature_imports + data_imports
   ↓
FileOp(content)
```

**No negociable:** Sin AST parsing. Sin inferencia de tipos profunda. Sin naming convention como source of truth. Scope detallado en `tmp/phase-5-prop-binding-plan.md`.

### Archivos creados/modificados (Phase 5 + Semantic Binding Closure)

| Archivo | Acción |
|---------|--------|
| `backend/app/signature/prop_mapper.py` | CREADO — `BindingResult`, `resolve_props()`, `PARAM_ALIASES`, `_load_data_access_config()`, `DataSourceIR`, phantom drift detection, `compile_binding()` con framework lowering |
| `backend/app/graphir/ui_ir.py` | MOD — `data_imports`, `binding_missing_props`, `provenance`, `consumed_params` |
| `backend/app/graphir/compiler.py` | MOD — `compile()` acepta `contract_params` + `component_signatures`, invoca `resolve_props()`, union-based fidelity, provenance agg |
| `backend/app/graphir/backends/react_backend.py` | MOD — `render()` acepta `contract_params`, `_inject_data_imports()`, `_hoist_hook_bindings()`, `JSVariable`, `_normalize_export_name` |
| `backend/app/engine/apply_engine.py` | MOD — pasa `contract_params` a `backend.render()` |
| `backend/config/data_access.json` | CREADO — BindingIR v2 con Page/KpiRow/Timeseries bindings |
| `tests/unit/test_prop_mapper.py` | CREADO — 45 tests (BindingIR, state machine, drift, backward compat) |

### Phase 5 data model

```
Contract params
    ↓
DataSourceIR (framework-agnostic: type="dashboard_data", selector="kpiData")
    ↓ compile_binding(framework="react")
HookBinding (hook_name="useDashboardData", transform="kpiData")
    ↓ _hoist_hook_bindings() + _inject_hook_declarations()
JSVariable (_KpiRow_data) en componente padre
    ↓ render_tree()
FileOp con TSX correcto
```

### Invariantes arquitectónicos

1. `FileOpApplier` (executor.py) es el ÚNICO writer de archivos capability (3E escribe solo `page_file` declarado)
2. `GraphIRDraft.freeze()` es el único enforcement point
3. 1 capability → 1 node (sin binding redistribution)
4. `EdgeRole` es puramente semántico (CONTAINS/PRIMARY/SUPPORTING)
5. BackendRenderer es puro: recibe GraphIR + GraphIRLayout, produce FileOps
6. StructuralIR NO es estado deseado — es **salida del conciliador** (`complete_structure`), plan de diff (CREATE/MODIFY/DELETE/KEEP)
7. Ningún layer puede inferir identidad implícitamente. Builder no deriva paths. Renderer no infiere targets.
8. **PlanCompiler no conoce realidad; StructuralIndex no decide intención; ApplyEngine es el único mezclador** (ver sección Fronteras arriba)
9. **Solo `CompiledPlan.actions` es ejecutable.** `semantic_frame.actions` es input bruto (no se ejecuta directo); `SemanticResolution.actions` es debug trace. Prohibido leer `semantic_frame.actions` como fuente de verdad después de PlanCompiler.
10. **`instance_only`** — `ResolvedCapability` con `action=KEEP, instance_only=True` preserva la intención CREATE sobre una capability existente sin regenerar su archivo. El nodo participa en composición (parent page se promueve a MODIFY) pero el renderer salta su generación. `StructuralIR.operations` lo expone como `INSTANCE`.

### Instance-only: separación capability lifecycle vs instance lifecycle

**Problema resuelto:** CREATE capability existente destruía la implementación del componente.
Ej: `create kpi_row` + `KpiRow.tsx` existe → MODIFY KpiRow.tsx → stub destructivo.

**Solución:** `instance_only=True` en `ResolvedCapability`. El lifecycle cambia de MODIFY a KEEP
pero preserva la intención original (create). El renderer sabe:
- Participa en composición (parent importa la instancia)
- NO genera archivo (implementación existente intacta)

**Flujo correcto:**
```
"create kpi_row" + KpiRow.tsx existe
    ↓
_resolve_action → MODIFY (existe)
complete_structure loop detecta create-on-existing:
    → override: action=KEEP, instance_only=True
    → params se resuelven igual (necesarios para composición)
    ↓
composition sync: rc.instance_only=True → promueve parent layout.page a MODIFY
    ↓
GraphIR: layout.page MODIFY + KpiRow INSTANCE + Timeseries INSTANCE
    ↓
Renderer: layout.page se regenera con <KpiRow> y <Timeseries>
         KpiRow.tsx y Timeseries.tsx NO se tocan
```

**Archivos modificados (02-Jun-2026):**

| Archivo | Cambio |
|---------|--------|
| `structural_completion.py:ResolvedCapability` | + `instance_only: bool = False` |
| `structural_completion.py:StructuralIR.operations` | Incluye INSTANCE entries con `instance_only=True` |
| `structural_completion.py:complete_structure` | Loop detecta create-on-existing (action=MODIFY + verb CREATE + exists) → KEEP + instance_only |
| `structural_completion.py:has_resolved_keep_state` | No incluye instance_only en el KEEP check |
| `structural_completion.py:_sync_composition_parents` | Promueve parent también para instance_only children |
| `graphir/builder.py` | Acepta INSTANCE operations, marca `instance_only` en metadata del nodo |
| `graphir/ui_ir.py:UIComponentNode` | + `instance_only: bool = False` |
| `graphir/compiler.py` | Propaga `node.metadata.instance_only` → `UIComponentNode.instance_only` |
| `graphir/backends/react_backend.py:render_tree` | Salta generación de archivo si `uinode.instance_only` (traza como `skipped_instance_only`) |
| `graphir/constraint/renderer.py` | Salta generación de archivo si `uinode.instance_only` |

### Feature flags relevantes

```python
FEATURE_FLAGS = {
    "structural_resolver": True,       # Si True: activa resolver + ambiguity gate
    "constraint_graph": True,          # Si True: activa pipeline constraint-aware
}
```

---

## Required Props SSOT Gate — Hardening Post-Incident (06-Jun-2026)

**Incidente:** El extractor de signatures (`extractor.py`) producía `prop_names=[]` para `KpiRow` porque el regex `_PROP_DETAIL_PATTERN` **requería `;`** al final de cada prop, pero `KpiRow.tsx` usa `interface KpiRowProps { data: KpiItem[] }` sin punto y coma. Esto causó que el SSOT gate nunca se activara — required props nunca se verificaban, y el renderer generaba `<KpiRow />` sin `data` prop.

**Causa raíz — dos bugs en el regex:**

| Bug | Regex original | Fix |
|-----|---------------|-----|
| 1. Semicolon required | `([^;]+);` | `.+?;?\s*$` — `;` opcional |
| 2. Inline object types no soportados | `[^;]+` se detiene en `;` dentro de `Array<{ label: string; … }>` | `.+?` captura hasta fin de línea |

### Hardening en 4 Fases

| Fase | Archivo | Cambio | Tests |
|------|---------|--------|-------|
| **1 — Root cause closed** | `extractor.py:_PROP_DETAIL_PATTERN` | Regex `[^;\n]+` → `.+?` con `;?\s*$`; cubre semicolon, optional, inline object types | 44 syntax tests |
| **2 — Metrics + traceability** | `extractor.py` | `SIGNATURE_EMPTY` warning log + `get_signature_metrics()` report | `TestSignatureCoverage` |
| **3 — Instrumentation** | `compiler.py:169`, `constraint/renderer.py:362` | `SEMANTIC_FIDELITY` warning (fidelity < 1.0) + `PROP_FILTER` debug log (unrecognized props) | Trace assertions |
| **4 — Regression guard** | `test_real_repo_integration.py` | Golden E2E test `test_golden_required_props_never_stripped` — 4 invariants: KpiRow.data, Timeseries.title+data, verify passes, no prop loss | 1 E2E golden |

### Latent bug exposed

El test `test_verify_after_modify_timeseries` (que envía `params={"metrics": ...}` directamente a KpiRow sin `data_access.json`) ahora es **correctamente rechazado** (`MISSING_REQUIRED_PROPS`) por el SSOT gate. Antes del fix, `prop_names=[]` permitía que el plan pasara silenciosamente produciendo `<KpiRow />` roto. Por diseño, `metrics → data` NO está en `PARAM_ALIASES` (type safety: contract strings vs `KpiItem[]`); requiere `data_access.json` binding.

### Binding chain: contract.metrics → KpiRow.data

Diagnóstico post-incidente — dos causas concurrentes:

| Caso | Qué | Binding existe? |
|------|-----|----------------|
| **A** | KpiRow como standalone (action target directo) sin `.opencode/data_access.json` | ❌ No — ni v2 ni v3 cargados |
| **B** | KpiRow como standalone con `.opencode/data_access.json` v3 presente | ✅ Se carga `Page.dataSource.slices[0]` que mapea `metrics→KpiRow.data`. Pero `resolve_props()` no lo ve (v3 es Page-centric). El compiler lo salva vía `page_ds_has_slice()` + `_distribute_page_slices()` en el renderer. |

**Cadena completa (v3 presente):**

```
contract.metrics=["revenue", "growth"]
  ↓ _load_data_access_config(workspace) → {workspace}/.opencode/data_access.json
  ↓ _parse_bindings() → {}  (Page con dataSource → skip)
  ↓ load_page_data_source() → DataSourceIR(slices=[KpiRow.data])
  ↓
resolve_props("KpiRow", {metrics,...}):
  1. _find_binding("KpiRow", "data", {}) → None   (bindings_map vacío)
  2. "data" not in contract_params → skip
  3. "data" not in alias_targets → skip
  4. not title → skip
  5. _is_binding_missing_guard → False (v3: no per-component bindings)
     → FALLBACK_ALLOWED (no error, props={})
  ↓
compiler required-prop gate (stage 2):
  A) data NOT in props → skip
  B) data NOT in contract_params → skip
  C) page_ds_has_slice(page_ds, "KpiRow", "data") → True ✓
  ↓ OK — renderer llenará via _distribute_page_slices()
  ↓
Renderer._distribute_page_slices() → data={_pageData.kpiData}
```

**Sin v3 presente** (`agent_test_repo_copy` solo copia `frontend/`, no `.opencode/`):
```
load_page_data_source(workspace) → None
page_ds = None
compiler required-prop gate:
  C) page_ds_has_slice(None, "KpiRow", "data") → False ✗
  ↓ MISSING_REQUIRED_PROPS → rejected
```

**Regla:** `agent_test_repo_copy` es para tests estructurales (StructuralIndex, complete_structure). `phase6_repo_copy` (que añade `data_access.json` v3) es para tests de binding + verify. El fixture correcto depende de lo que se testee.

**Fuente de verdad:** `backend/config/data_access.json` (v3). El `.opencode/data_access.json` en `agent-test-repo` era v2 stale — movido a backup.

### Archivos modificados (06-Jun-2026)

| Archivo | Cambio |
|---------|--------|
| `backend/app/signature/extractor.py:20-23` | Regex fix: `[^;\n]+` → `.+?`; `+ SIGNATURE_EMPTY` warning; `+ get_signature_metrics()` |
| `backend/app/graphir/compiler.py:169-175` | `+ SEMANTIC_FIDELITY` warning log |
| `backend/app/graphir/constraint/renderer.py:360-368` | `+ PROP_FILTER` debug log en `_materialize_composition` |
| `tests/unit/test_required_props.py` | **CREADO** — 44 tests: 16 syntax (Fase 1.1) + 6 real components (Fase 1.2) + 2 coverage (Fase 1.3) + 18 compiler gate (Fase 1.4) + 2 SSOT override |
| `tests/e2e/test_real_repo_integration.py` | `+ test_golden_required_props_never_stripped`; fix `test_verify_after_modify_timeseries` assertion |

### Coverage actual de signatures (agent-test-repo)

| Componente | prop_names | required | optional | Antes del fix |
|------------|-----------|----------|----------|---------------|
| **KpiRow** | `["data"]` | `["data"]` | `[]` | ❌ `prop_names=[]` |
| **BarChart** | `["data"]` | `["data"]` | `[]` | ❌ `prop_names=[]` |
| **LineChart** | `["data"]` | `["data"]` | `[]` | ❌ `prop_names=[]` |
| **DataTable** | `["columns", "data"]` | `["columns", "data"]` | `[]` | ❌ `prop_names=[]` |
| **Timeseries** | `["data", "title"]` | `[]` | `["data", "title"]` | ✅ |

---

## Binding Resolution Architecture — Plan Activo

**Plan detallado:** `tmp/binding-resolution-architecture.md`

**Objetivo:** Eliminar toda inferencia semántica del compilador. Contract params nunca llegan al compilador ni al renderer. Toda resolución pasa por BindingResolver → ResolvedBindings.

### Estado: 3 PRs ✅ COMPLETOS (06-Jun-2026)

| PR | Qué | Estado | Archivos |
|----|-----|--------|----------|
| **1** | BindingResolver + ResolvedBindings + DELETE alias/heuristic/exact-match. `_distribute_page_slices` dormida. | ✅ | `binding/models.py`, `binding/resolver.py` (CREADOS); `prop_mapper.py`, `compiler.py`, `react_backend.py`, `constraint/renderer.py`, `apply_engine.py` (MODIFICADOS); 655 tests pass |
| **2** | Golden test `test_renderer_receives_fully_resolved_props` | ✅ | `tests/e2e/test_binding_resolution_golden.py` (CREADO) — 8 tests: sin page_ds, con page_ds, multi-child, duplicados, provenance, node.data fallback, hook declaration |
| **3** | DELETE `_distribute_page_slices()` | ✅ | `react_backend.py`, `constraint/renderer.py` — call site + definition eliminados; 663 tests pass |

### Pipeline actual (PR3)

```
Intent → Contract → Structural → GraphIR
                                      ↓
                               BindingResolver.resolve()
                                 ├── load_page_data_source(workspace) → DataSourceIR
                                 ├── Resuelve slices → JSVariable refs
                                 ├── page_ds_has_slice() (señal diagnóstica)
                                 └── Produce ResolvedBindings
                                      ↓
                               Compiler (determinista puro)
                                 ├── Recibe ResolvedBindings (NO contract_params)
                                 ├── Valida: required ⊆ props
                                 └── NO resolve_props(), NO page_ds_has_slice()
                                      ↓
                               Renderer (puro)
                                 ├── Recibe UIComponentNode.props completos
                                 ├── Hook declaration desde page_data_source
                                 └── Solo imprime JSX (sin binding, sin slices)
```

### Responsabilidades

| Componente | Responsabilidad | Conoce contract_params? |
|------------|----------------|------------------------|
| **Contract** | Intención de negocio | Sí |
| **Structural** | CREATE/MODIFY/DELETE qué componentes | No |
| **BindingResolver** | Datos → Props. Único que traduce intención a props. | **Sí** |
| **Compiler** | Validación: `required ⊆ props`? | **No** |
| **Renderer** | Stringify JSX. Sin binding, sin slices. | **No** |

### Invariantes PR1 (vigentes)

1. Contract params nunca llegan al compilador ni al renderer. Solo BindingResolver los conoce.
2. Todo required prop debe estar en ResolvedBindings.props o es MISSING_REQUIRED_PROPS.
3. `page_ds_has_slice()` se mueve a BindingResolver (señal diagnóstica, no se borra).
4. No existen aliases globales, heurísticas, ni exact-match. Toda resolución pasa por data_access.json v3.
5. `unconsumed_params` es warning, no error.
6. `_distribute_page_slices()` eliminado — renderer es puro, sin binding.