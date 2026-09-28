# SESSION_INFRASTRUCTURE_DEBT.md

> Estado: post-Fase S1-A (infraestructura previa a Session Lifecycle).
> Documento de deuda histórica y de contratos de seguridad vigentes.
> No es un diseño de la fase Session; es solo el registro de restricciones.

## 1. Deuda histórica no migrable en S1-A

S1-A no modificó el historial ni limpió la deuda acumulada. Permanece intacto:

- **372 branches `agent-*`** en `REPO_ROOT` (`/opt/agent-repos/agent-test-repo`).
- **390 git worktrees** bajo `{RUNS_DIR}`.
- **~400 run-state JSONs** bajo `{STATE_DIR}`.
- Commits `agent:{run_id}` legacy previos a S1-A.

Regla: la limpieza de esta deuda se diseñará explícitamente en la fase Session
(iteración, re-validación, `session_merge`) — nunca una migración automática.

## 2. Contrato de seguridad compartido (S1-A.7)

Backend (`backend/app/utils/run_id.py`, `backend/app/utils/path_guard.py`) y
orchestrator (`orchestrator/run_id.py`) son **contenedores separados sin
dependencia de código compartida** — decisión deliberada para no acoplar las
imágenes (no crear `shared/`). Mantienen **implementaciones locales** que
cumplen las **mismas invariantes**:

```text
ID validation:
- backend: local implementation
- orchestrator: local implementation
- same security invariants
- no shared runtime dependency
```

Cadena obligatoria:

```text
run_id
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
- `guard_within` rechaza cualquier path resuelto (`realpath`) fuera del root:
  path absoluto externo, `../`, traversal equivalente.
- El formato de `run_id` NO cambió. `session_id` NO se introdujo todavía.
- Tests equivalentes: `tests/unit/test_run_id_security.py` (backend) y
  `tests/orchestrator/test_run_id_security.py` (orchestrator).

## 3. Restricciones operativas vigentes (post S1-A)

| Tema | Estado |
|------|--------|
| `git commit` | **Única** llamada productiva: `_run_git_flow()` (`backend/app/engine/apply_engine.py`). Locks: `tests/e2e/test_s1a_infra_locks.py`. |
| `git merge` | 0 llamadas en backend. Merge de ramas de sesión será tarea de `session_merge` (futuro). |
| `POST /runs/{id}/approve` | Eliminado (era commit+merge+branch-delete). `approve_run` y `get_base_branch` eliminados. |
| `POST /runs/{id}/reject` | Inexistente desde siempre; `agent_reject` (MCP) eliminado por apuntar a él. |
| `GET /agent/latest` | Eliminado (selección lexicográfica de UUID arbitraria, sin consumidores). |
| `GET /maintenance/cleanup` | Eliminado (destructivo, sin auth, sin consumidores reales). |
| MCP `agent_run` | Interpret-only → `awaiting_confirmation`. No puede generar `confirmed/applying/completed`. |
| MCP `agent_approve` | Conservado pero inerte: marcado `pending_session_merge_migration`. |
| `scripts/migrate_runs.py` | Eliminado (migración one-off superada). |
| Seed `.opencode/` en `create_worktree` | Eliminado (no había `.opencode` en el repo objetivo; seed muerto). |
| `import subprocess` duplicado | Eliminado (`diff_generator.py`). |

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

## 5. Riesgos conocidos remitidos a Session

1. `create_worktree()` hace `git checkout master` + `git reset --hard master`
   en `REPO_ROOT` en cada run (`worktree_manager.py`) — muta el repo base.
2. Sin lock de concurrencia sobre `_run_git_flow` / commit por run (un solo
   escritor hoy por diseño, pero sin cerrojo).
3. Una sesión reutilizará ramas de sesión y un único `session_merge`; el
   modelo A/B/C de creación de worktrees S1–S10 quedará definido por la fase
   Session (no implementado en S1-A).
4. Memoria (`<worktree>/.opencode/semantic_memory.json`) es gitignored y nunca
   se persistentizó en el historial — no usarla como continuidad de sesión.

## 6. Donde mirar

- Locks de comportamiento/arquitectura: `tests/e2e/test_s1a_infra_locks.py`.
- Contrato de seguridad: `tests/unit/test_run_id_security.py`,
  `tests/orchestrator/test_run_id_security.py`.
- Auditoría completa de Session Lifecycle previa:
  `tmp/S1-Fase0-Auditoria-Session-Lifecycle-2026-09-27.md`.