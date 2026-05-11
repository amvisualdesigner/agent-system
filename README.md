# 🤖 Agent System

Sistema de ejecución de agentes que convierte planes LLM en operaciones reales sobre un repositorio Git mediante worktrees aislados.

---

## 📌 Arquitectura

El sistema está compuesto por:

- **Backend API (FastAPI)** → expone endpoints de planificación y ejecución
- **Git Repository** → estado persistente de los agentes
- **Worktrees efímeros** → ejecución aislada por `run_id`
- **Filesystem `/tmp/agent-runs`** → artefactos temporales por ejecución

---

## ⚙️ Flujo general

1. `POST /agent/plan`
   - El LLM genera un plan estructurado

2. `POST /agent/apply`
   - El plan se compila en operaciones
   - Se ejecutan en un worktree aislado
   - Se genera diff + artifacts

3. `POST /maintenance/cleanup`
   - Limpia:
     - worktrees huérfanos
     - branches `agent-*`
     - `/tmp/agent-runs`

---

## 🚀 Cómo ejecutar el backend

```bash
cd /opt/agent-system/backend
source venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

## 📡 API Usage
### 1. 📥 Generar un plan
```Request
curl -X POST http://localhost:8000/agent/plan \
  -H "Content-Type: application/json" \
  -d '{
    "input": "Create a util function and a hello module that uses it"
  }'
```
```Response
{
  "run_id": "0d74fb1a-01e4-4d0e-af51-8535e4263c4c",
  "status": "ok",
  "plan": {
    "steps": [
      {
        "path": "src/util.ts",
        "action": "create",
        "proposed_content": "export function add(a, b) { return a + b; }"
      }
    ]
  }
}
```
### 2. ⚙️ Ejecutar plan (apply)
```Request
curl -X POST http://localhost:8000/agent/apply \
  -H "Content-Type: application/json" \
  -d '{
    "run_id": "0d74fb1a-01e4-4d0e-af51-8535e4263c4c",
    "plan": {
      "steps": [
        {
          "path": "src/util.ts",
          "action": "create",
          "proposed_content": "export function add(a, b) { return a + b; }"
        },
        {
          "path": "src/hello.ts",
          "action": "create",
          "proposed_content": "import { add } from \"./util\";\nconsole.log(add(2,3));"
        }
      ]
    }
  }'
```
```Response
{
  "status": "ok",
  "run_id": "0d74fb1a-01e4-4d0e-af51-8535e4263c4c",
  "operations": [],
  "execution": []
}
```
### 3. 🧹 Cleanup del sistema

```Request
curl -X POST http://localhost:8000/maintenance/cleanup
```
```Response
{
  "status": "ok",
  "message": "cleanup completed"
}
```

## 📁 Artefactos generados
Cada ejecución crea:

/tmp/agent-runs/<run_id>/
  ├── workspace/         # worktree aislado
  ├── artifacts/
  │     ├── plan.json
  │     ├── execution.json
  │     ├── summary.json
  │     └── diff.patch

## 🧠 Conceptos clave
run_id → identifica cada ejecución
worktree → aislamiento por ejecución Git
proposed_content → contenido final a escribir en archivo
diff → generado desde git staging
agent- branches* → ramas temporales del sistema

## ⚠️ Notas importantes
El sistema requiere permisos consistentes en el repositorio Git
No ejecutar operaciones Git como usuarios distintos sobre el mismo repo
/tmp/agent-runs es efímero y puede limpiarse en cualquier momento

## 🧹 Mantenimiento
Si algo queda colgado:
rm -rf /tmp/agent-runs/*
o
curl -X POST http://localhost:8000/maintenance/cleanup

## 🚀 Estado del sistema
✔ Worktrees aislados por run
✔ Plan → Apply pipeline operativo
✔ Cleanup centralizado
✔ Diff generado por Git