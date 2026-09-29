# SESSION_LIFECYCLE.md

> Estado: implementado en S1-B (Fase 2 autorizada). Documento canónico del
> ciclo de vida de una Session. Complementa `SYSTEM_FLOW.md` y
> `DECISION_BOUNDARIES.md`.

## 1. Definición de autoridad

```text
Session        = continuidad física + lifecycle (N Runs, único worktree/branch).
Run            = unidad atómica de cambio (≤1 commit, Confirmed Plan semántico).
Git            = continuidad (branch + worktree + commits agent:{run_id}).
Confirmed Plan = autoridad semántica por Run.
Memory         = evidencia residual, NUNCA continuidad.
session_merge  = única operación de integración (D8a Opción A, --no-ff).
```

Regla dura: **una Session nunca es autoridad semántica**. No almacena intent /
plan / preview / FileOps / interpretación. Solo lifecycle + identidad física.

## 2. Identidad física

session_id: UUID v4 lowercase-hex (formato idéntico a run_id), validado por
`backend/app/utils/session_id.py` (misma invariante que run_id, implementación
local). `branch = agent/session-{session_id[:8]}`, `workspace = {RUNS_DIR}/{session_id}`.
Cada Session tiene **un único** worktree y **una única** branch (no hay
branch-per-run). `session_id ≠ run_id`; los run_ids se acumulan en
`SessionRecord.run_ids`.

## 3. Estados y transiciones

Persistidos: `ACTIVE`, `MERGED`, `CONFLICT`, `FAILED`.

```text
ACTIVE   → {MERGED, CONFLICT, FAILED}
CONFLICT → {MERGED, CONFLICT, FAILED}   (recuperable)
MERGED   → (terminal, no acepta más)
FAILED   → (terminal)
```

- `MERGE_READY` es **derivado en read-time** a partir de las precondiciones de
  merge; nunca se persiste ni se transiciona.
- Self-transition permitida (idempotencia, mismo contrato que RunPhase).

Persistencia: `{STATE_DIR}/sessions/{session_id}.json` (mismo mecanismo
JSON+fsync que run_state y misma resolución de STATE_DIR: env `STATE_DIR` →
env `RUNS_DIR` → `backend/run_states`; subdirectorio `sessions/`).

## 4. Flujo canónico

```text
POST /session                            → ACTIVE + worktree + branch (única creación)
POST /agent/interpret {run_id, session_id}
   → valida Session ACTIVE; resuelve workspace de la Session
   → interpret SLA branches al contexto de la Session
   → persiste run_state[run_id].session_id; registra run_id en run_ids
POST /agent/confirm {run_id}
   → session_id se lee DE run_state (no del request)
   → gate: Session no-ACTIVE → rechazo sin cambios
   → snapshot del workspace de la Session
POST /agent/apply {run_id}
   → gate: Session no-ACTIVE → rechazo; lock session_apply_lock(session_id)
   → re-fingerprint del snapshot DENTRO del lock (solo no-dry)
   → apply sobre workspace de la Session; ≤1 commit agent:{run_id}
POST /session/{id}/merge                  → única integración (--no-ff)
```

Encadenado: `interpret` recibe `session_id` opcional y lo fija en run_state;
`confirm`/`apply` lo leen de run_state — el Run queda vinculado a unívocamente
a su Session. Si `session_id` está ausente (run-only), el pipeline legacy
(worktree por run) queda intacto.

## 5. session_merge — contrato (D8a Opción A)

Único call-site productivo: `backend/app/api/session_routes.py`.

Precondiciones (todas, antes de mergear): Session existe y está
`ACTIVE`/`CONFLICT`; branch de Session existe; worktree de Session existe;
base_branch existe; `REPO_ROOT` está en `master`; sin runs no-terminales; sin
cambios sin commitear en el worktree (`git status --porcelain` vacío); branch
de Session ≠ base_branch; no ya `MERGED`.

Comportamiento:

```text
precondiciones OK → git merge --no-ff -m "session:{session_id[:8]}" branch
  OK              → Session = MERGED; merged_commit = HEAD
  conflicto       → git merge --abort (ANTES de responder)
                  → Session = CONFLICT; base restaurada y limpia
lock ocupado      → conflict session_busy (Session = CONFLICT)
```

Prohibido: `--ours/--theirs`, `reset --hard`, `pull --rebase`, auto-resolución,
segundo mecanismo de integración, merge por run. `CONFLICT` es terminal para el
merge EN CURSO pero recuperable: corregir/decidir y volver a llamar
`session_merge` (CONFLICT → MERGED).

## 6. Concurrencia

Locks en-proceso (`threading.Lock`, backend single-process):
`session_apply_lock(scope_key)` donde `scope_key = session_id` si existe, si no
`run_id` (`keyed_run_scope`). Comparten lock: apply real y merge de una misma
Session. MERGE usa `acquire(blocking=False)` → `session_busy` si hay un apply en
curso (nunca materializa sobre un workspace en uso). Sin lock distribuido en
S1-B; los locks caen en reinicio por diseño: la recuperación es por archivos +
git.

## 7. Recuperación (S7)

Reconstrucción con `session_id` + fichero de Session + branch + worktree + git
log. `create_session(session_id)` reutiliza SOLO si workspace+branch coinciden y
la branch existe; ante mismatch u huérfanos la Session se marca `FAILED` y se
lanza `SessionPhysicalError` — **nunca** borra/recrea (sin auto-reparación ni
resync silencioso). `ensure_session_worktree` nunca recrea en silencio.

## 8. Interfaz

HTTP (backend): `POST /session`, `GET /session/{session_id}` (estado +
`merge_ready`/`merge_reasons` derivados + runs con su phase),
`POST /session/{session_id}/merge`.

MCP (`backend/mcp-server/server.py`): `agent_create_session()`,
`agent_session_status(session_id)`, `agent_session_merge(session_id)`.
`agent_approve` fue ELIMINADO (migración `pending_session_merge_migration`
completada). `agent_run`/`agent_interpret` aceptan `session_id` opcional.

Orchestrator (transporte SOLO): `RunRequest.session_id`, snapshot inicia incluye
`session_id`, `call_interpret` lo reenvía. Sin endpoints de Session en el
orquestador.

## 9. Tests

- `tests/unit/test_session_identity.py` — identidad y máquina de estados.
- `tests/e2e/test_session_lifecycle.py` — S2–S10 + E2E (worktree/branch únicos,
  continuidad, aislamiento, 1 commit/run, NO_CHANGES, lock, recuperación, merge
  limpio/conflicto, single merge route).
- `tests/e2e/test_s1a_infra_locks.py` — locks de arquitectura (1 merge
  productivo, agent_approve eliminado).