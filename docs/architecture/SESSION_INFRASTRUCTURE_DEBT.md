# SESSION_INFRASTRUCTURE_DEBT.md

> Estado: post-Fase S1-B (Session Lifecycle implementado; merge migrado).
> Documento de deuda histórica y de contratos de seguridad vigentes.
> S1-B no limpió la deuda histórica; sí completó la migración del merge.

## 1. Deuda histórica no migrable

S1-A/S1-B no modificaron el historial ni limpiaron la deuda acumulada. Permanece intacto:

- **372 branches `agent-*`** en `REPO_ROOT` (`/opt/agent-repos/agent-test-repo`).
- **390 git worktrees** bajo `{RUNS_DIR}`.
- **~400 run-state JSONs** bajo `{STATE_DIR}`.
- Commits `agent:{run_id}` legacy previos a S1-A.

## 2. Contrato de seguridad compartido (S1-A.7)

Backend (`backend/app/utils/run_id.py`, `backend/app/utils/path_guard.py`) y
orchestrator (`orchestrator/run_id.py`) son **contenedores separados sin
dependencia de código compartida** — decisión deliberada para no acoplar las
imágenes (no crear `shared/`). Mantienen **implementaciones locales** que
cumplen las **mismas invariantes**:

```text
ID validation:
- backend: local implementation  (run_id y session_id)
- orchestrator: local implementation
- same security invariants
- no shared runtime dependency
```

Cadena obligatoria:

```text
run_id / session_id
    ↓
validación estricta del formato
    ↓
resolución del path
    ↓
guard_within(root, path)
```

Reglas:
- `validate_run_id` rechaza cualquier valor que no sea UUID `8-4-4-4-12`
  lowercase-hex (mensaje idéntico en ambos lados, alineado en S1-A).
- `validate_session_id` (`backend/app/utils/session_id.py`, S1-B) aplica la
  misma invariante UUID v4 lowercase-hex.
- `guard_within` rechaza cualquier path resuelto (`realpath`) fuera del root:
  path absoluto externo, `../`, traversal equivalente.
- El formato de `run_id` NO cambió. `session_id` se añadió en S1-B (mismo
  formato, propósito distinto: identidad de Session, ≠ run_id).
- Tests equivalentes: `tests/unit/test_run_id_security.py` (backend),
  `tests/unit/test_session_identity.py` (session) y
  `tests/orchestrator/test_run_id_security.py` (orchestrator).

## 3. Restricciones operativas vigentes (post S1-B)

| Tema | Estado |
|------|--------|
| `git commit` | **Única** llamada productiva: `_run_git_flow()` (`backend/app/engine/apply_engine.py`). Locks: `tests/e2e/test_s1a_infra_locks.py`. |
| `git merge` | **Única** llamada productiva: `--no-ff` en `POST /session/{id}/merge` (`backend/app/api/session_routes.py`). Además el `git merge --abort` obligatorio post-conflicto. Locks: `test_s1a_infra_locks.py`, `test_session_lifecycle.py` (S10). |
| `POST /runs/{id}/approve` | Eliminado (era commit+merge+branch-delete). `approve_run` y `get_base_branch` eliminados. |
| `POST /runs/{id}/reject` | Inexistente desde siempre; `agent_reject` (MCP) eliminado por apuntar a él. |
| `GET /agent/latest` | Eliminado (selección lexicográfica de UUID arbitraria, sin consumidores). |
| `GET /maintenance/cleanup` | Eliminado (destructivo, sin auth, sin consumidores reales). |
| MCP `agent_run` | Interpret-only → `awaiting_confirmation`. No puede generar `confirmed/applying/completed`. |
| MCP `agent_approve` | **Eliminado en S1-B** (migración `pending_session_merge_migration` completada). Sustituido por `agent_create_session`, `agent_session_status`, `agent_session_merge`. |
| MCP `agent_interpret` / `agent_run` | Aceptan `session_id` opcional (S1-B). |
| `scripts/migrate_runs.py` | Eliminado (migración one-off superada). |
| Seed `.opencode/` en `create_worktree` | Eliminado (no había `.opencode` en el repo objetivo; seed muerto). |
| `import subprocess` duplicado | Eliminado (`diff_generator.py`). |
| Creación de worktree de Session | `git worktree add ws -b session-branch` SIN `checkout master`/`reset --hard` (S1-B). El path legacy `create_worktree()` con checkout/reset solo aplica al modo run-only. |

## 4. Lifecycle del Run tras Apply (S1-A.2/S1-A.3)

```text
apply exitoso + verificacion ok + cambios            → COMPLETED  (status ok)
apply exitoso + sin cambios efectivos                → COMPLETED  (status no_changes, sin commit vacio)
apply + verificacion fallida (verify_failed)         → FAILED  (sin commit; cambios staged preservados)
apply rechazado / clarification_needed / error       → FAILED
dry_run                                              → preview (ZERO writes, nunca commit)
```

- `apply_result` se persiste en run-state al terminar el Apply.
- `verify_failed` NO ejecuta `git reset --hard` / `git clean` / `checkout`:
  el estado físico del worktree queda visible para inspección (bloqueado, no
  destruido). El bloqueo del Run es la fase `FAILED`.

## 5. Riesgos y decisiones remitidas a Session (estado S1-B)

1. Deuda histórica del §1: la limpieza se diseñará explícitamente en una fase
   futura (iteración, re-validación) — nunca una migración automática.
2. Concurrencia: S1-B introdujo locks en-proceso sobre la Session
   (`session_apply_lock`), compartidos entre apply real y merge. NO hay lock
   distribuido; la recuperación es por archivos + git (ver SESSION_LIFECYCLE.md §6–7).
3. Session Lifecycle implementado (S1-B): S2–S10 en `tests/e2e/test_session_lifecycle.py`;
   una Session = 1 worktree + 1 branch `agent/session-{sid[:8]}` + N Runs; única
   integración `session_merge` (`--no-ff`).
4. Modo run-only heredado: `create_worktree()` (`worktree_manager.py`) conserva
   `git checkout master` + `git reset --hard master` en `REPO_ROOT` por cada
   run SIN session. El path de Session nunca hace checkout/reset. Alineación del
   run-only con el modelo de Session queda fuera del alcance de S1-B.
5. Memoria (`<worktree>/.opencode/semantic_memory.json`) es gitignored y nunca
   se persistentizó en el historial — no usarla como continuidad de sesión.

## 6. Donde mirar

- Locks de comportamiento/arquitectura: `tests/e2e/test_s1a_infra_locks.py`.
- Ciclo de vida de Session (S1-B): `docs/architecture/SESSION_LIFECYCLE.md` y
  `tests/e2e/test_session_lifecycle.py`.
- Contrato de seguridad: `tests/unit/test_run_id_security.py`,
  `tests/unit/test_session_identity.py`,
  `tests/orchestrator/test_run_id_security.py`.
- Auditoría completa de Session Lifecycle previa:
  `tmp/S1-Fase0-Auditoria-Session-Lifecycle-2026-09-27.md`.