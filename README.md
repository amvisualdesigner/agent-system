# Agent System

Sistema que convierte tareas en lenguaje natural en operaciones Git aisladas mediante un pipeline determinístico de transformación semántica.

**Principio:** *LLM for intention; deterministic system for execution.*

---

## Flujo Canónico

```
Usuario ──→ /agent/interpret ──→ Draft ──→ /agent/confirm ──→ Plan ──→ /agent/apply ──→ Worktree + verify
                (LLM + catálogo)        (humano edita)      (PlanCompiler)           (ApplyEngine)
```

Tres fases separadas, cada una con su propio endpoint y guard de `RunPhase`:

| Fase | Endpoint | Estado | Qué produce |
|------|----------|--------|-------------|
| Interpretación | `POST /agent/interpret` | `interpreting → awaiting_confirmation` | `InterpretationDraft` (contrato + acciones propuestas) |
| Confirmación | `POST /agent/confirm` | `awaiting_confirmation → confirmed` | `CompiledPlan` (skill_ir + frame + actions) |
| Ejecución | `POST /agent/apply` | `confirmed → applying → completed/failed` | FileOps + git commit + verify result |

Terminal states: `completed`, `failed`, `cancelled`.

---

## Arquitectura en Dos Planos

```
  PLANO SEMÁNTICO                    PLANO REPOSITORIO
  ───────────────                    ─────────────────
  Intenciones                        Archivos en disco
  Capabilities                       Paths relativos
  GraphIR (grafo inmutable)          Git worktrees
  UIComponentNode tree               FileOperations
  Firmas de componentes (TSX)        Workspace
  Memoria semántica                  REPO_ROOT

         │                                   │
         └──── ApplyEngine (único mezclador) ─┘
```

**Frontera de pureza** (`graphir/boundary.py`): impide que claves del plano repositorio (`file_path`, `workspace`, `fs_*`) aparezcan dentro del grafo semántico.

---

## Pipeline de Ejecución (ApplyEngine)

```
Intención confirmada (CompiledPlan.actions)
       │
       ▼
StructuralIndex.from_worktree()     ← escanea repo real
       │
       ▼
complete_structure(semantic, contract, index)  ← conciliador
       │  CREATE / MODIFY / DELETE / KEEP
       ▼
StructuralIR
       │
       ▼
GraphIRBuilder + GraphIRPipeline     ← grafo semántico
       │
       ▼
enforce_graph_purity()               ← frontera
       │
       ▼
BackendRenderer (ReactBackend)       ← FileOp[]
        │  - Signature override (firmas reales del worktree)
        │  - Prop filtering contra prop_names
       ▼
FileOpApplier + CompositionSync (3E) ← escribe archivos
       │
       ▼
VerifyWorktree (tsc --noEmit)        ← fail-closed
       │
       ▼
Git commit (solo si verify pasa)
```

### Componentes clave

| Componente | Rol |
|-----------|-----|
| **StructuralIndex** | Realidad estructural: `exists(cap)`, `resolve_path`, scan worktree |
| **complete_structure()** | Conciliador: dada intención + index → lifecycle por capability |
| **GraphIRBuilder** | Materializa StructuralIR + resolution → grafo semántico |
| **ApplyEngine** | Único punto de mezcla entre intención y realidad |
| **ReactBackend** | GraphIR → TSX code, con override de firmas reales del worktree |
| **VerifyWorktree** | `tsc --noEmit` post-apply, fail-closed |

---

## Sistema de Intención

Tres módulos que NO conocen la realidad del repo:

| Módulo | Archivo | Input → Output |
|--------|---------|----------------|
| **IntentInterpreter** | `intent/interpreter.py` | Lenguaje natural + catálogo → `InterpretationDraft` (único LLM) |
| **PlanCompiler** | `intent/plan_compiler.py` | `ConfirmedIntent` + contrato → `CompiledPlan` (determinista, sin LLM) |
| **Catalog** | `catalog/loader.py` | `capability_catalog.json` → vocabulario + sinónimos |

**Anti-patrón:** PlanCompiler NO llama `StructuralIndex`. StructuralIndex NO decide intención. ApplyEngine es el único mezclador.

### Máquina de estados

```
RunPhase enum: interpreting → awaiting_confirmation → confirmed → applying → completed
                                                                          → failed
                                                          → cancelled (desde cualquier fase)
```

Validada por `validate_transition()` en cada endpoint. 29 tests.

---

## Orquestador (LangGraph)

`orchestrator/` — grafo con human-in-the-loop:

```
entry ──┬→ interpret (POST /agent/interpret)
         ├→ confirm (POST /agent/confirm)
         ├→ call_apply (POST /agent/apply)
         └→ return_result (terminal)
```

- Estado persistido en `AgentState` → `store.save_snapshot()`
- Reanudación via `POST /run/{id}/confirm` y `/apply`
- SSE events: `interpretation_ready`, `plan_preview_ready`, `result`



## Estructura del Proyecto

```
backend/
├── app/
│   ├── api/                   # FastAPI endpoints (/agent/interpret, /confirm, /apply)
│   ├── intent/                # IntentInterpreter, PlanCompiler, models, state machine
│   │   ├── models.py          # InterpretationDraft, ConfirmedIntent, CompiledPlan, RunPhase
│   │   ├── interpreter.py     # IntentInterpreter (único LLM)
│   │   ├── plan_compiler.py   # PlanCompiler (determinista)
│   │   └── llm_client.py      # LLM client
│   ├── catalog/               # capability_catalog.json loader
│   ├── state/                 # RunState persistence (JSON files by run_id)
│   ├── signature/             # Component signature extraction
│   │   └── extractor.py       # Regex scanner para interfaces TSX
│   ├── engine/                # ApplyEngine, StructuralIndex, complete_structure
│   │   ├── apply_engine.py    # Pipeline principal
│   │   ├── structural_index.py # Worktree scanner + existence checks
│   │   └── structural_completion.py  # Lifecycle conciliator
│   ├── graphir/               # Grafo semántico
│   │   ├── models.py          # GraphIR, FileOp, PipelineRoute
│   │   ├── boundary.py        # Pureza semántica
│   │   ├── pipeline.py        # GraphIRPipeline
│   │   ├── path_resolver.py   # File path resolution
│   │   ├── backends/          # ReactBackend + generators
│   │   ├── constraint/        # Identity resolver + renderer constraint-aware
│   │   └── structure/         # Resolver + canonicalizer
│   ├── config/                # Settings + feature flags
│   └── executor/              # Worktree management, file I/O
├── main.py                    # FastAPI entry point
│
orchestrator/                   # LangGraph orquestador (interpret→confirm→apply)
│
ui/                             # Frontend web (single HTML + Vite)
│
tests/
├── unit/                      # Tests unitarios
├── container/                 # Renderer E2E con mock
├── intent/                    # IntentInterpreter, PlanCompiler
├── integration/               # Multi-paso + edge cases
├── regression/                # Content regression
├── e2e/                       # Pipeline completo
├── conftest.py
└── helpers.py

```

---

## Testing

| Suite | Tests | Qué cubre |
|-------|-------|-----------|
| `tests/unit/` | Unitarios | Generadores, content regression, StructuralIndex, PlanCompiler |
| `tests/container/` | 32 | Renderer E2E con mock de archivos (DELETE, CREATE, MODIFY) |
| `tests/intent/` | Intent | Interpreter, PlanCompiler, state machine |
| `tests/integration/` | Integración | Multi-paso, edge cases |
| `tests/regression/` | Regresión | Content regression, no `_props`, no `__COMPOSITION__` |
| `tests/e2e/` | E2E | Pipeline completo (remove/add KPI, update dashboard, etc.) |

Ejecución:

```bash
REPO_ROOT=/path/to/repo pytest tests/ -q
```

---

## Configuración

| Variable | Requerida | Default | Propósito |
|----------|-----------|---------|-----------|
| `REPO_ROOT` | Sí | — | Ruta al repositorio origen |
| `RUNS_DIR` | No | `/opt/agent-repos/worktrees` | Directorio de worktrees |
| `ARTIFACTS_DIR` | No | `/opt/agent-repos/artifacts` | Directorio de artefactos |
| `LLM_BASE_URL` | No | `http://localhost:7000` | URL del LLM |
| `LLM_MODEL` | No | `Qwen/Qwen2.5-Coder-3B-Instruct` | Modelo LLM |

---

## Levantar el Sistema

### 1. vLLM (solo si no está corriendo)

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

### 4. Detener sistema

```bash
cd /opt/agent-system
docker compose down
```

Los datos persisten (worktrees, artifacts, snapshots).

---

## API Surface

| Puerto | Servicio |
|--------|----------|
| 8000 | Backend API |
| 9000 | Orchestrator API |
| 7000 | vLLM inference |
| 5173 | UI frontend |

### Via UI

1. Abrir `http://localhost:5173`
2. Escribir tarea en lenguaje natural
3. Presionar "Run"
4. Ver SSE streaming en vivo

### Via CLI

**Nuevo flujo (interpret → confirm → apply):**

```bash
# 1. Interpretar intención
INTERP=$(curl -s -X POST http://localhost:8000/agent/interpret \
  -H "Content-Type: application/json" \
  -d '{"run_id":"demo","message":"remove KPI row from dashboard"}')
echo "$INTERP" | python3 -m json.tool

# 2. Confirmar plan
CONFIRM=$(curl -s -X POST http://localhost:8000/agent/confirm \
  -H "Content-Type: application/json" \
  -d '{"run_id":"demo"}')
echo "$CONFIRM" | python3 -m json.tool

# 3. Aplicar
APPLY=$(curl -s -X POST http://localhost:8000/agent/apply \
  -H "Content-Type: application/json" \
  -d '{"run_id":"demo","confirmed_intent":{...}}')
echo "$APPLY" | python3 -m json.tool

# 4. Consultar run
curl -s http://localhost:8000/runs/demo | python3 -m json.tool
```

**Via Orchestrator (LangGraph):**

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

---

## MCP Server

El sistema incluye un servidor MCP (Model Context Protocol) en `backend/mcp-server/server.py`.

### Herramientas

| Tool | Params | Descripción |
|------|--------|-------------|
| `agent_plan` | `prompt: str` | Generar plan de ejecución |
| `agent_apply` | `run_id: str, plan: dict, dry_run: bool` | Ejecutar plan en sandbox |
| `get_run` | `run_id: str` | Obtener metadata del run |
| `agent_review` | `run_id: str` | Inspeccionar plan, diff, ejecución |
| `agent_run` | `prompt: str, dry_run: bool` | One-shot plan + apply |
| `agent_approve` | `run_id: str` | Aprobar y mergear cambios |
| `agent_reject` | `run_id: str` | Rechazar y limpiar |

### Conexión

```json
{
  "mcpServers": {
    "agent-runtime": {
      "command": "python",
      "args": ["/opt/agent-system/backend/mcp-server/server.py"],
      "env": { "PYTHONPATH": "/opt/agent-system/backend" }
    }
  }
}
```

### Backup Docker compose

```bash
sudo bash /opt/agent-system/scripts/backup_system.sh
```

---

## Configuración del Repositorio

El backend clona/usa un repo Git en `REPO_ROOT`. Cada run:

1. Crea un branch `agent-{run_id[:8]}` desde `master`
2. Crea un worktree en `{RUNS_DIR}/{run_id}/workspace/`
3. Aplica operaciones (create, modify, delete)
4. Hace `git add -A` y `git commit -m "agent:{run_id}"`
5. Genera diff

Para limpiar worktrees antiguos:
```bash
curl -X POST http://localhost:8000/maintenance/cleanup
```

---

## Limitaciones del Modelo 3B

El sistema usa `Qwen/Qwen2.5-Coder-3B-Instruct`, un modelo pequeño para ejecución local en GPU de consumo.

- **Razonamiento superficial:** planes simples, pocos pasos
- **Contexto limitado:** 4096 tokens máximo
- **Refactors grandes no confiables:** tareas multi-archivo tienden a ser inconsistentes
- **Alucinación de paths:** puede inventar rutas que no existen
- **Sin conocimiento del repositorio:** no entiende la estructura actual sin contexto explícito

La estrategia del sistema para mitigar estas limitaciones no es pedirle más al LLM, sino rodearlo con capas deterministas: registry validation, GraphIR pipeline, ReactBackend, FileOpApplier. El LLM nunca decide directamente qué archivos crear ni qué contenido escribir — solo selecciona capability y rellena parámetros.

# UI prompts
#	Prompt	Patrón que ejercita
1	"add a filter panel to the dashboard"	CREATE componente nuevo (no existe en repo), contrato standalone, composition sync con Page
2	"remove the line chart"	DELETE multi-instance (LineChart es una de las 2 instancias de presentation.timeseries), composition sync → Page MODIFY
3	"update the KPI metrics to show revenue and growth"	MODIFY KpiRow con params, SSOT gate (metrics → KpiRow.data via binding + slice kpiData)
4	"add a search bar to the page"	CREATE SearchBar (nuevo, contrato interaction.search), composition sync
5	"remove the KPI row and add a filter panel"	Compuesto: DELETE KpiRow + CREATE FilterPanel en un solo mensaje, composition sync doble
6	"add a bar chart"	CREATE sobre componente existente → instance_only (BarChart.tsx ya existe, se salta generación pero se añade a Page)
7	"remove the timeseries chart"	DELETE multi-instance específico (Timeseries.tsx), composition sync + anchor preservation (Page no queda huérfana)
8	"add an export button"	CREATE componente nuevo (data.export), standalone, sin Page — filler/scaffold
9	"change the dashboard layout title to Quarterly Overview"	MODIFY Page con params (title), contrato dashboard.sales_overview
10	"what can I add to this dashboard?"	Sólo interpretación (sin acciones), ejercita IntentInterpreter puro

Cobertura de patrones:
- CREATE nuevo (1, 4, 8), CREATE existente → instance_only (6)
- DELETE multi-instance (2, 7), DELETE con composición (5)
- MODIFY con params (3, 9)
- Compuesto CREATE+DELETE (5)
- Instance-only existente (6)
- Interpret-only sin apply (10)
- Todos pasan por el pipeline completo: interpret → confirm → apply → verify (tsc --noEmit)
