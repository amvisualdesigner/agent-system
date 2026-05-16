# Agent System

Sistema de agente de codigo que convierte tareas en lenguaje natural en operaciones Git aisladas mediante un orquestador LangGraph, backend FastAPI y LLM local.

---

## Core Principle

> **LLM for intention; deterministic system for execution.**

El modelo propone. La plataforma valida. El runtime ejecuta deterministicamente. El sistema persiste y audita cada paso.

El sistema NO delega ejecucion critica al LLM:

| El LLM hace | El sistema hace |
|-------------|-----------------|
| Interpretar intencion | Validar plan contra policy |
| Proponer acciones | Ejecutar filesystem + Git |
| Generar contenido | Asegurar aislamiento (worktree) |
| - | Persistir y auditar cada paso |
| - | Reintentar con degradado graceful |

**Precision**: la ejecucion es determinista **una vez aceptado un plan valido**. La generacion del plan sigue siendo probabilistica porque depende del LLM. El sistema no intenta hacer deterministico lo que no puede ser; en su lugar, rodea el componente probabilistico con capas deterministicas de validacion y ejecucion.

Validacion, ejecucion, filesystem, Git, seguridad y persistencia son **deterministas y auditables**.

---

## Specification Levels

Este documento describe el sistema en 3 niveles. Es importante distinguirlos para no confundir intencion arquitectonica con comportamiento real del runtime.

### 🔵 IMPLEMENTED — Codigo que ejecuta el sistema hoy

Comportamiento real verificado en el runtime. Lo que ocurre cuando corres el sistema.

### 🟡 PARTIALLY IMPLEMENTED — Features existentes pero incompletas

Funcionalidad que existe parcialmente: backend lo soporta pero el orchestrator no lo expone, o viceversa. Funciona con limitaciones conocidas.

### 📄 DESIGN / FUTURE — Intencion arquitectonica

Estado deseado, modelo conceptual, o proximas fases. Documentado para mantener coherencia de diseno, pero aun no implementado en runtime.

---

Cada seccion del documento etiqueta explicitamente a que nivel pertenece.

---

## Design Philosophy

El sistema prioriza:

- **Simplicidad sobre complejidad** — no introducir infraestructura hasta que sea estrictamente necesaria
- **Auditabilidad sobre autonomia** — cada paso debe poder inspeccionarse, no ejecutarse ciegamente
- **Determinismo sobre improvisacion** — una vez aceptado un plan, la ejecucion debe ser reproducible
- **Aislamiento sobre conveniencia** — cada run en su worktree, sin efectos laterales entre ejecuciones
- **Degradacion graceful sobre magia** — si algo falla, que falle visiblemente y con la mayor cantidad de estado posible
- **Small models + strong runtime sobre large models + weak runtime** — preferimos un modelo modesto con un sistema robusto a un modelo poderoso sin guardrails

La arquitectura asume que los LLMs son componentes probabilisticos y falibles. Por tanto, toda ejecucion critica debe estar contenida dentro de capas deterministas y verificables.

---

## Non-Goals

El sistema NO es ni intenta ser:

- **AGI** — no razona, no comprende, no tiene conciencia
- **Auto-modificante** — no modifica el core del sistema (orchestrator, backend, policies)
- **Ejecutor arbitrario** — no ejecuta comandos shell sin pasar por policy
- **Autonomo** — no tiene memoria persistente entre runs, no aprende, no mejora solo
- **Recursive self-improvement** — no escribe su propio codigo ni se modifica a si mismo
- **Reemplazo de revision humana** — los cambios generados deben ser revisados antes de mergear
- **Multi-agente** — no hay coordinacion entre agentes ni routing de tareas
- **Temporal** — no hay planificacion a largo plazo ni dependencias entre runs

Esto evita scope creep y mantiene el sistema en su rol: **asistente de generacion de codigo con supervisión**.

---

## Orchestrator Role

El **Orchestrator** (`/opt/agent-system/orchestrator/`) NO ejecuta operaciones directamente.

Su rol es exclusivamente de coordinacion:

| Responsabilidad | Descripcion |
|-----------------|-------------|
| Coordinar flujo | Orquestar los 4 nodos LangGraph en secuencia |
| Mantener estado | AgentState vivo durante la ejecucion del run |
| Emitir eventos | SSE streaming con 9 eventos por run |
| Aplicar routing | validate_plan decide: `call_apply`, retry `call_plan`, o abort |
| Persistir snapshots | RunSnapshot en disco al completar |
| Manejar retries | get_run con 3 intentos, validate_plan con max 1 retry |
| Recuperar en startup | Escanear `runs/` y exponer snapshots via API |
| Degradar graceful | Si get_run falla, completar sin diff en vez de abortar |

Toda ejecucion real (filesystem, Git, diff, commit) ocurre en el **Backend**. El orchestrator es el conductor, no el motor.

---

## Layered Architecture

```
┌────────────────────────────────────────────┐
│           Intent Layer                     │
│  UI / CLI / API ── tarea en lenguaje natural│
└──────────────────┬─────────────────────────┘
                   ▼
┌────────────────────────────────────────────┐
│           Planning Layer                   │
│  LLM (vLLM Qwen 3B) ──> plan estructurado  │
│  call_plan node                            │
└──────────────────┬─────────────────────────┘
                   ▼
┌────────────────────────────────────────────┐
│          Validation Layer                  │
│  validate_plan node ──> sanity guard +      │
│  policy check + retry                      │
└──────────────────┬─────────────────────────┘
                   ▼
┌────────────────────────────────────────────┐
│          Execution Layer                   │
│  call_apply node ──> backend apply +       │
│  Git worktree + diff + commit             │
└──────────────────┬─────────────────────────┘
                   ▼
┌────────────────────────────────────────────┐
│         Persistence Layer                  │
│  return_result node ──> RunSnapshot JSON   │
│  (atomic write, restart-safe)              │
└──────────────────┬─────────────────────────┘
                   ▼
┌────────────────────────────────────────────┐
│        Observability Layer                 │
│  structured logs + SSE events + metrics    │
│  GET /runs + GET /runs/{id}               │
└────────────────────────────────────────────┘
```

---

## Arquitectura de Servicios

```
┌──────────────┐     POST /run       ┌──────────────────┐    POST /agent/plan    ┌──────────────┐
│   UI (5173)  │ ──────────────────> │  Orchestrator    │ ────────────────────> │   Backend    │
│  HTML/JS     │                     │  LangGraph       │                       │  FastAPI     │
│  Node.js     │ <── SSE stream ──── │  FastAPI :9000   │    GET /runs/{id}     │  :8000       │
└──────────────┘                     │  4 nodes         │ <──────────────────── │              │
                                     │  persistence     │                       │  Git worktree│
                                     └──────────────────┘                       │  + artifacts │
                                                                                └──────┬───────┘
                                                                                       │
                                                                              POST /v1/chat/completions
                                                                                       │
                                                                                ┌──────▼───────┐
                                                                                │  vLLM :7000  │
                                                                                │  Qwen 3B     │
                                                                                └──────────────┘
```

### Puertos

| Servicio | Puerto | Descripcion |
|----------|--------|-------------|
| Backend | 8000 | API de planificacion y ejecucion |
| vLLM | 7000 | LLM OpenAI-compatible |
| Orchestrator | 9000 | LangGraph orquestador |
| UI | 5173 | Chat web |

### Componentes

**Backend** (`/opt/agent-system/backend/`)
- FastAPI con endpoints para planificar (`/agent/plan`), ejecutar (`/agent/apply`) y consultar runs (`/runs/{id}`)
- Crea worktrees Git aislados por run
- Genera diff y artifacts en disco
- NO debe modificarse manualmente

**Orchestrator** (`/opt/agent-system/orchestrator/`)
- LangGraph StateGraph con 4 nodos: `call_plan` -> `validate_plan` -> `call_apply` -> `return_result`
- Comunicacion asincrona: `POST /run` devuelve `run_id`; `GET /stream/{run_id}` emite SSE
- 9 eventos SSE por run: node_start, tool_call, node_end (x3) + result
- Persiste RunSnapshot en `/opt/agent-system/orchestrator/runs/{run_id}.json` (escritura atomica tmp+fsync+rename)
- Snapshot indexing on startup: escanea `runs/`, carga snapshots, expone via `GET /runs` y `GET /runs/{run_id}` (sin recovery de runs interrumpidos)

**UI** (`/opt/agent-system/ui/`)
- Pagina estatica HTML/JS servida por Node.js
- Tema oscuro, textarea, boton Run, log SSE en vivo
- Muestra result, diff, archivos, y worktree path al completar

**vLLM** (`docker-compose.yml`)
- `Qwen/Qwen2.5-Coder-3B-Instruct`
- GPU memory utilization: 0.75
- Max model len: 4096
- Tool-call parser: openai

---

## Flujo de Ejecucion 🔵 IMPLEMENTED

### Diagrama

```
POST /run {"task": "..."}
  │
  ▼
call_plan ──> POST /agent/plan ──> plan con acciones
  │
  ▼
validate_plan ──> sanity guard (acciones > 0, file_path no vacio)
  │                  │                     │
  │               valido              invalido (retry <= 1)
  │                  │                     │
  ▼                  ▼                     ▼
call_apply ──> POST /agent/apply + GET /runs/{id}
  │               (con retry 3 intentos para get_run)
  ▼
return_result ──> snapshot a disco + SSE result event
```

### Lifecycle del Run (State Machine) 🔵 IMPLEMENTED

```
              ┌────────────┐
              │  planning   │ ◄──── retry (max 1)
              └──────┬─────┘
                     │
              ┌──────▼─────┐
              │  executing  │
              └──────┬─────┘
                     │
        ┌────────────┼────────────┐
        ▼            ▼            ▼
  completed        error       cancelled
```

> **Nota**: `created`, `validated` y `persisted` son conceptos de diseno, no fases del runtime. El runtime real usa `planning`, `executing`, `completed`, `error`, `cancelled`.

### Transiciones Reales

| Desde | Hasta | Condicion |
|-------|-------|-----------|
| `planning` | `executing` | plan valido aceptado |
| `planning` | `planning` | retry (plan invalido, max 1) |
| `planning` | `error` | fallo en call_plan o validate_plan |
| `executing` | `completed` | apply exitoso + snapshot guardado |
| `executing` | `error` | fallo en apply o get_run |
| `*` | `cancelled` | kill switch activado

### Nodos LangGraph

| Nodo | Funcion |
|------|---------|
| `call_plan` | LLM planning via backend |
| `validate_plan` | Routing + sanity guard + retry |
| `call_apply` | Ejecuta plan, obtiene diff/files |
| `return_result` | Persiste snapshot, emite resultado |

### Eventos SSE (9 por run)

```
node_start  / call_plan       phase=planning
tool_call   / /agent/plan     phase=planning
node_end    / call_plan       phase=planning
node_start  / validate_plan   phase=planning
node_end    / validate_plan   phase=planning
node_start  / call_apply      phase=executing
tool_call   / /agent/apply    phase=executing
node_end    / call_apply      phase=executing
result      / return_result   phase=completed
```

### Gestión de Errores 🔵 IMPLEMENTED

- `validate_plan`: retry max 1 si el plan no pasa sanity guard
- `call_apply`: retry 3 intentos para `get_run` (500ms delay)
- `call_plan`/`call_apply`: emiten `node_end` incluso en error (stream completo)
- Cancelled flag kill switch comprobado en cada nodo
- Trace capado a 50 entradas
- `get_run` degrada graceful: si falla, el run se completa sin diff (con warning)

---

## Contrato del Planner 🔵 IMPLEMENTED

El contrato entre el LLM y el sistema es el plan estructurado JSON. Este es el ABI interno.

### Esquema

```json
{
  "actions": [
    {
      "type": "create",
      "file_path": "ruta/relativa/al/repo/archivo.ext",
      "description": "que hace esta accion",
      "content": "contenido del archivo para create/modify"
    }
  ]
}
```

### Campos

| Campo | Tipo | Obligatorio | Descripcion |
|-------|------|-------------|-------------|
| `type` | string | si | Tipo de operacion |
| `file_path` | string | si | Ruta relativa al repo |
| `description` | string | no | Explicacion de la accion |
| `content` | string | no | Contenido (create/modify) |

### Tipos de accion validos

| Tipo | Descripcion | Requiere content |
|------|-------------|------------------|
| `create` | Crear archivo nuevo | si |
| `modify` | Modificar archivo existente | si |
| `delete` | Eliminar archivo | no |

### Invariantes

- `file_path` debe ser relativo (no absoluto)
- `file_path` no debe contener `..`, `.git`, `node_modules`, `dist`, `build`, `.env`
- Extensiones permitidas: `.ts`, `.js`, `.py`, `.md`, `.json`, `.yaml`, `.yml`, `.txt`, `.html`, `.css`
- Maximo 20 acciones por plan
- Maximo 3 operaciones delete por plan
- Maximo 200KB por archivo

---

## Failure Philosophy 🔵 IMPLEMENTED

El sistema esta diseñado para fallar de forma **visible, rastreable y recuperable**.

### Principios

- **No ocultar errores** — `except Exception: pass` esta prohibido; todo error se loggea con `[run_id=...]`
- **Emitir eventos incluso en fallo** — los nodos emiten `node_end` con datos de error para que el stream SSE siempre este completo
- **Persistir estado parcial cuando sea posible** — si get_run falla, el run se completa sin diff pero con el resto de la informacion intacta
- **Degradar graceful antes que abortar silenciosamente** — reintentar operaciones transitorias (get_run con 3 intentos) antes de declarar fallo
- **Nunca asumir exito implicito** — toda respuesta del backend se valida; si falta un campo esperado, se loggea y se degrada
- **Preferir consistencia sobre disponibilidad** — un run parcialmente exitoso sigue siendo util si puede auditarse; un run con datos corruptos no lo es

### Que pasa cuando falla cada componente

| Componente | Comportamiento |
|------------|----------------|
| LLM timeout | call_plan error -> validate_plan detecta -> return_result con error |
| Plan invalido | validate_plan retry (max 1) -> si persiste, abort con error |
| Backend caido | call_apply error -> node_end emitido con error -> return_result |
| get_run falla | 3 retries -> warning -> run completo sin diff |
| Snapshot corrupto | warning en log -> run no listable pero no bloquea el sistema |
| Orchestrator crash | RunSnapshot persistido sobrevive -> GET /runs/{id} post-recovery |

**Un run parcialmente exitoso sigue siendo util si puede auditarse.**

---

## Security Model 🔵 IMPLEMENTED (validation pipeline) · 📄 DESIGN (future hardening)

### Operating Principle

El sistema opera con **confianza cero hacia el LLM** y **privilegio minimo hacia el backend**.

### Forbidden Operations

El sistema bloquea explicitamente:

- **Path escape**: salir del workspace (`../`, rutas absolutas)
- **Red**: no hay acceso a red desde el execution layer
- **Auto-modificacion**: no se puede modificar `orchestrator/`, `backend/`, policies
- **Shell arbitrario**: no se ejecuta codigo shell ni comandos del sistema
- **Secretos**: no se tocan `.env`, credenciales, keys
- **sudo**: no hay escalada de privilegios
- **Binarios**: solo archivos de texto con extensiones permitidas

### Validation Pipeline

1. **Plan policy** (`validate_plan_policy`): limita numero de operaciones y deletes
2. **Path safety** (`safe_path`): verifica que la ruta no escape del workspace
3. **Blocked patterns** (`is_path_safe`): bloquea `.git`, `node_modules`, `dist`, `build`, `.env`
4. **Extension whitelist** (`ALLOWED_EXTENSIONS`): solo tipos de archivo permitidos
5. **Size limit** (`MAX_FILE_SIZE`): 200KB por archivo
6. **Operation validation** (`validate_operation`): verifica action valida, path, diff obligatorio

### Isolation

- **Git worktree por run**: cada ejecucion tiene su propio directorio aislado
- **Branch aislada**: `agent-{run_id[:8]}` desde `master`
- **Artifacts separados**: por run_id dentro de `RUNS_DIR`
- **Snapshot persistido**: fuera del worktree, en el orchestrator

### Future Hardening

- seccomp para restringir syscalls del backend
- Docker sandbox por ejecucion
- Firejail para aislamiento de procesos
- Rootless execution
- Firma de snapshots para integridad

---

## Context Policy 🟡 PARTIALLY IMPLEMENTED

El contexto se construye en el backend antes de llamar al LLM.

### Como se construye

1. El backend recibe el task
2. Construye un `RunContext` con `run_id` y `workspace_root`
3. Opcionalmente recolecta contexto del repositorio (archivos relevantes, estructura)
4. Envia el prompt completo al LLM via `/v1/chat/completions`

### Limites actuales

| Limite | Valor | Notas |
|--------|-------|-------|
| Max model len | 4096 tokens | Limitacion del 3B |
| Max acciones por plan | 20 | Policy |
| Max deletes por plan | 3 | Policy |
| Max file size | 200 KB | Policy |
| Max workspace files | 200 | Configurable |
| Max trace entries | 50 | En orchestrator |

### Limitaciones conocidas

- No hay RAG ni retrieval aumentado
- No hay analisis de imports/dependencias
- No hay contexto diferencial (solo el prompt directo)
- El truncation es implicito (modelo limita a 4096)
- No hay ranking de archivos relevantes

---

## Observabilidad 🔵 IMPLEMENTED (logs + snapshots + SSE) · 📄 DESIGN (metrics)

### Logging

Formato estructurado con `[run_id=...]` en cada linea:

```
[2026-05-15T16:57:11+0200] [INFO] [orchestrator.nodes.call_apply] [run_id=9e54b5f1...] call_apply ok latency=38ms status=ok
[2026-05-15T16:57:11+0200] [INFO] [orchestrator.store] [run_id=9e54b5f1...] snapshot saved to ... (3342 bytes)
```

Componentes: `orchestrator.main`, `orchestrator.sse`, `orchestrator.store`, `orchestrator.backend_client`, `orchestrator.nodes.*`

### Metricas clave

El sistema deberia exponer:

| Metrica | Descripcion |
|---------|-------------|
| `plan_success_rate` | % de planes generados exitosamente |
| `apply_success_rate` | % de applies exitosos |
| `avg_plan_latency` | Latencia promedio de planificacion |
| `avg_apply_latency` | Latencia promedio de ejecucion |
| `retry_rate` | Frecuencia de reintentos |
| `diff_size` | Tamano promedio del diff generado |
| `files_modified` | Archivos por run |
| `cancellation_rate` | Tasa de cancelaciones |
| `error_rate_by_node` | Errores por nodo LangGraph |
| `snapshot_persist_time` | Tiempo de persistencia |

Estado actual: logs estructurados implementados. Metricas numericas: no implementadas (pendiente).

### Trazabilidad

Cada run genera:

- **Trace**: lista de entradas con nodo, input, output, latencia (max 50)
- **Snapshot**: estado completo persistido en JSON
- **SSE stream**: eventos en tiempo real durante la ejecucion
- **Artifacts del backend**: plan, execution, summary, diff en disco

---

## Persistencia 🔵 IMPLEMENTED

### Backend (por run_id interno)

```
/opt/agent-repos/worktrees/{backend_run_id}/
  ├── workspace/               # Git worktree aislado
  │   └── ...                  # Archivos del repo
  └── artifacts/
       ├── plan.json           # Plan ejecutado
       ├── execution.json      # Operaciones + resultados
       ├── summary.json        # Resumen del run
       └── diff.patch          # Git diff generado
```

### Orchestrator (por run_id del orquestador)

```
/opt/agent-system/orchestrator/runs/{orchestrator_run_id}.json
```

Contenido del snapshot:

```json
{
  "run_id": "uuid",
  "task": "descripcion",
  "phase": "completed",
  "status": "ok",
  "created_at": "ISO8601",
  "updated_at": "ISO8601",
  "plan": { "actions": [...] },
  "execution": { "status": "ok", "workspace": "...", "operations": [...] },
  "diff": "diff --git a/...",
  "files": ["path/to/file"],
  "trace": [...],
  "error": null
}
```

Escritura atomica: tmp file -> fsync -> os.replace (rename). Los snapshots sobreviven reinicios del orquestador.

---

## Levantar el Sistema

### 1. vLLM (solo si no esta corriendo)

```bash
cd /opt/agent-system
docker compose up -d vllm
```

Health check: `curl http://localhost:7000/health`

### 2. Todos los servicios

```bash
cd /opt/agent-system
docker compose up -d
```

Health checks:
```bash
curl -s http://localhost:8000/health   # Backend
curl -s http://localhost:9000/health   # Orchestrator
curl -s http://localhost:7000/health   # vLLM
curl -s -o /dev/null -w "%{http_code}" http://localhost:5173/  # UI
```

Logs:
```bash
docker compose logs -f            # Todos
docker logs agent-orchestrator -f # Solo orchestrator
docker logs agent-backend -f      # Solo backend
```

### 3. Refrescar sistema (tras cambios en código)

```bash
cd /opt/agent-system
docker compose up -d --build
```

Esto reconstruye las imágenes que tienen cambios y reinicia solo los containers necesarios.

### 4. Detener sistema

```bash
cd /opt/agent-system
docker compose down
```

Para detener todo y liberar puertos. Los datos persisten (worktrees, artifacts, snapshots).

### Verificar todo

```bash
curl -s http://localhost:8000/health   # Backend
curl -s http://localhost:9000/health   # Orchestrator
curl -s http://localhost:7000/health   # vLLM
curl -s -o /dev/null -w "%{http_code}" http://localhost:5173/  # UI
```

---

## API surface
8000 → backend API
7000 → vLLM inference
9000 → orchestrator API
5173 → UI frontend
8050 → Docker admin (portainer)

## Uso

### Via UI

1. Abrir `http://localhost:5173`
2. Escribir tarea en lenguaje natural
3. Presionar "Run"
4. Ver SSE streaming en vivo
5. Al completar: diff, archivos, y worktree path

### Via CLI

```bash
# Crear run
RUN_OUT=$(curl -s -X POST http://localhost:9000/run \
  -H "Content-Type: application/json" \
  -d '{"task":"create file hello.txt with content HELLO"}')
RUN_ID=$(echo "$RUN_OUT" | python3 -c "import sys,json;print(json.load(sys.stdin)['run_id'])")

# Ver streaming
curl -N http://localhost:9000/stream/$RUN_ID

# Ver snapshot persistido
curl -s http://localhost:9000/runs/$RUN_ID | python3 -m json.tool

# Listar todos los runs
curl -s http://localhost:9000/runs | python3 -m json.tool
```

### Via Backend directo

```bash
# Planificar
curl -s -X POST http://localhost:8000/agent/plan \
  -H "Content-Type: application/json" \
  -d '{"task":"create file test.txt with hello"}'

# Ejecutar (con run_id del plan)
curl -s -X POST http://localhost:8000/agent/apply \
  -H "Content-Type: application/json" \
  -d '{"run_id":"...","plan":{"actions":[...]},"dry_run":false}'

# Consultar run
curl -s http://localhost:8000/runs/{backend_run_id}
```

---

## MCP Server 🔵 IMPLEMENTED

El sistema incluye un servidor MCP (Model Context Protocol) en `/opt/agent-system/backend/mcp-server/server.py` para ser usado desde asistentes MCP-compatibles (Claude Desktop, clients MCP, o LLMs con tool-calling).

### Herramientas expuestas

| Tool | Params | Descripcion |
|------|--------|-------------|
| `agent_plan` | `prompt: str` | Generar plan de ejecucion |
| `agent_apply` | `run_id: str, plan: dict, dry_run: bool` | Ejecutar plan en sandbox |
| `get_run` | `run_id: str` | Obtener metadata del run |
| `agent_review` | `run_id: str` | Inspeccionar plan, diff, ejecucion |
| `agent_run` | `prompt: str, dry_run: bool` | One-shot plan + apply |
| `agent_approve` | `run_id: str` | Aprobar y mergear cambios |
| `agent_reject` | `run_id: str` | Rechazar y limpiar |

### Puerto

El servidor MCP corre en `127.0.0.1:8510` con transporte `stdio`.

### Conexion desde MCP client

Configuracion tipica para Claude Desktop u otros clients MCP:

```json
{
  "mcpServers": {
    "agent-runtime": {
      "command": "python",
      "args": ["/opt/agent-system/backend/mcp-server/server.py"],
      "env": {
        "PYTHONPATH": "/opt/agent-system/backend"
      }
    }
  }
}
```

O desde linea de comandos:

```bash
cd /opt/agent-system/backend
python mcp-server/server.py
```

Esto expone las 7 herramientas para que un modelo MCP-compatible pueda planificar, ejecutar, revisar y aprobar cambios directamente.

### Backup Docker compose
```bash
sudo bash /opt/agent-system/scripts/backup_system.sh
```

---

## Variables de Entorno

### Backend (`backend/.env`)

| Variable | Default | Descripcion |
|----------|---------|-------------|
| `REPO_ROOT` | - | Ruta al repo Git |
| `RUNS_DIR` | `/tmp/agent-runs` | Directorio de worktrees y artifacts |
| `LLM_BASE_URL` | `http://localhost:7000` | URL de vLLM |
| `LLM_MODEL` | `Qwen/Qwen2.5-Coder-3B-Instruct` | Modelo LLM |
| `MAX_WORKSPACE_FILES` | `200` | Limite de archivos por workspace |

### Orchestrator

| Variable | Default | Descripcion |
|----------|---------|-------------|
| `BACKEND_URL` | `http://localhost:8000` | URL del backend |

---

## Estructura de Directorios

```
/opt/agent-system/
  ├── backend/                   # FastAPI (NO modificar)
  │   ├── main.py
  │   ├── app/
  │   │   ├── api/               # Endpoints
  │   │   ├── engine/            # Logica de apply
  │   │   ├── executor/          # Worktree, patch
  │   │   ├── planner/           # Compilacion de plan
  │   │   ├── policy/            # Seguridad y validacion
  │   │   ├── config/            # Settings
  │   │   └── contracts/         # Modelos Pydantic
  │   └── mcp-server/            # MCP bridge (legacy)
  │
  ├── orchestrator/              # LangGraph orquestador
  │   ├── main.py                # FastAPI entrypoint + endpoints
  │   ├── graph.py               # StateGraph wiring
  │   ├── state.py               # AgentState TypedDict
  │   ├── models.py              # Pydantic models
  │   ├── sse.py                 # InMemoryEventEmitter
  │   ├── store.py               # RunSnapshot persistence
  │   ├── backend_client.py      # HTTP client al backend
  │   ├── logger.py              # Logging config
  │   ├── nodes/
  │   │   ├── call_plan.py
  │   │   ├── validate_plan.py
  │   │   ├── call_apply.py
  │   │   └── return_result.py
  │   └── runs/                  # Snapshots persistidos
  │
  ├── ui/                        # Chat web
  │   ├── index.html             # Single-page app
  │   └── server.mjs             # Static file server
  │
  ├── docker-compose.yml         # vLLM container
  ├── scripts/                   # Utilidades
  └── docs/                      # Documentacion adicional
```

---

## Configuracion del Repositorio

El backend clona/usa un repo Git en `REPO_ROOT` (`/opt/agent-repos/agent-test-repo`). Cada run:

1. Crea un branch `agent-{run_id[:8]}` desde `master`
2. Crea un worktree en `{RUNS_DIR}/{run_id}/workspace/`
3. Aplica operaciones (create, modify, delete)
4. Hace `git add -A` y `git commit -m "agent:{run_id}"`
5. Genera diff

// TODO: crear un garbage
Los worktrees persisten en disco. Para limpiar:

```bash
curl -X POST http://localhost:8000/maintenance/cleanup
git branch | grep -v "master" | xargs git branch -D (OJO, elimina todas las ramas excepto master)
```

---

## Limitaciones del Modelo 3B 🔵 IMPLEMENTED

El sistema usa `Qwen/Qwen2.5-Coder-3B-Instruct`, un modelo pequeno para ejecucion local en GPU de consumo.

Limitaciones conocidas:

- **Poca profundidad de razonamiento**: planes simples, pocos pasos
- **Comprension limitada de contexto largo**: 4096 tokens maximo
- **Pobre recuperacion autonoma**: no se autocorrige ante errores complejos
- **Refactors grandes no confiables**: tareas que abarcan multiples archivos tienden a ser inconsistentes
- **Alucinacion de paths**: puede inventar rutas que no existen
- **Sin conocimiento del repositorio**: no entiende la estructura actual del proyecto sin contexto explicito

Esto no es un bug — es una **restriccion de diseno consciente**. Preferimos un modelo pequeno, deterministico y predecible a uno grande, lento e impredecible. Para tareas complejas, el sistema puede ampliarse a modelos mas grandes via API conforme evolucione.

---

## Roadmap

### Completado

- [x] MVP funcional: UI + Orchestrator + Backend + vLLM
- [x] 4 nodos LangGraph con SSE streaming
- [x] Persistencia de RunSnapshot (atomic write, restart-safe)
- [x] Event sourcing ligero con recovery en startup
- [x] Manejo de errores: retry + degraded graceful + node_end en fallo
- [x] Structured logging con correlation IDs
- [x] Security model con path safety + extension whitelist
- [x] Worktree isolation por run

### Context & Retrieval

- [ ] `expand_prompt` node: enriquecer task con contexto del repo antes de planificar
- [ ] Deterministic context selector: seleccion de archivos relevantes por path + AST + grep (no semantico, no ML)
- [ ] Repository structure map: snapshot estatico de la estructura del proyecto
- [ ] Import graph analysis: analisis estatico de dependencias entre archivos (input context only, no decision layer)
- [ ] Deterministic truncation policy: recorte explícito de contexto para respetar ventana del modelo (no adaptive reasoning)

### Review & Safety

- [ ] Human approval mode: paso opcional antes de apply (optional gating flag, no core runtime)
- [ ] Dry-run mode: diff preview sin escritura en filesystem
- [ ] Centralized policy engine con reglas declarativas
- [ ] Rule-based risk scoring: heuristicas sobre operaciones, deletes, file count, diff size (no ML)
- [ ] Policy audit logs: registro de todas las decisiones de policy

### Workspace Awareness

- [ ] Detectar archivos modificados previamente entre runs
- [ ] Contexto incremental: saber que cambió desde el ultimo run
- [ ] Awareness de estado Git actual (branch, cambios sin commit)

### Quality

- [ ] Tests unitarios por nodo LangGraph
- [ ] Tests de integracion: POST /run -> SSE -> 9 eventos
- [ ] E2E test desde UI
- [ ] Metrics collection: plan_success_rate, apply_success_rate, latencias
- [ ] Benchmark suite para prompts/tasks

### Infrastructure

- [ ] Database persistence (SQLite / Postgres)
- [ ] Docker Compose para todo el stack (backend + orchestrator + UI + vLLM)
- [ ] Multi-user isolation

### Future Research

- Redis pub/sub para SSE horizontal (cuando haya multi-instancia)
- Automatic merge policies (experimental, disabled by default — requiere tests + rollback + sandboxing)
- Deterministic retry strategies (bounded, no auto-recovery)
