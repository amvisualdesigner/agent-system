# 🤖 Agent System

A coding agent system that turns LLM-generated plans into real, isolated Git-based operations using worktrees.

---

## 📌 Overview

The Agent System is a backend-driven framework that:

- Converts natural language tasks into structured execution plans
- Executes changes in isolated Git worktrees
- Produces diffs and artifacts per run
- Supports review, approval, and cleanup workflows

It is designed for deterministic execution, reproducibility, and safe code generation.

---

## ⚙️ Architecture

### Core Components

- **FastAPI Backend** → API layer for planning and execution
- **LLM Engine (vLLM)** → generates structured JSON plans
- **Git Repository** → source of truth for code state
- **Ephemeral Worktrees** → isolated execution per `run_id`
- **Temporary Storage (`/tmp/agent-runs`)** → artifacts per execution

### LLM Layer (Current)

The system now uses **vLLM OpenAI-compatible server** instead of Ollama:

- Model: `Qwen/Qwen2.5-Coder-14B-Instruct-AWQ`
- Endpoint: `http://localhost:8000/v1/chat/completions`
- Features:
  - PagedAttention KV cache
  - Tool-safe structured output
  - High-throughput inference

---

## 🔄 Execution Flow

### 1. Plan Generation
`POST /agent/plan`

- LLM receives task prompt
- Returns structured JSON plan:

```json
{
  "actions": [
    {
      "type": "create|update|delete",
      "file_path": "path/to/file",
      "description": "what to do"
    }
  ]
}
```

---

### 2. Plan Execution
`POST /agent/apply`

- Creates isolated Git worktree per `run_id`
- Applies file operations
- Stages and computes Git diff
- Stores execution artifacts

---

### 3. Cleanup
`POST /maintenance/cleanup`

- Removes:
  - Orphan worktrees
  - Temporary branches (`agent-*`)
  - `/tmp/agent-runs`

---

## 🚀 Running the Backend

```bash
cd /opt/agent-system/backend
source venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

---

## 🧠 LLM Configuration

The system is configured via environment variables:

```env
LLM_BASE_URL=http://localhost:8000
LLM_MODEL=Qwen/Qwen2.5-Coder-14B-Instruct-AWQ
LLM_API_KEY=
```

### vLLM Deployment

```bash
docker compose up -d vllm
```

Key parameters:

- Context: 6144 tokens (initial)
- GPU utilization: 0.75
- Max sequences: 4
- Quantization: AWQ

---

## 📡 API Usage

### 1. Generate Plan

```bash
curl -X POST http://localhost:8000/agent/plan \
  -H "Content-Type: application/json" \
  -d '{
    "task": "Create a utility function and a hello module"
  }'
```

### Response

```json
{
  "run_id": "uuid",
  "status": "ok",
  "plan": {
    "actions": [
      {
        "type": "create",
        "file_path": "src/util.ts",
        "description": "create utility function"
      }
    ]
  }
}
```

---

### 2. Apply Plan

```bash
curl -X POST http://localhost:8000/agent/apply \
  -H "Content-Type: application/json" \
  -d '{
    "run_id": "uuid",
    "plan": {
      "actions": []
    }
  }'
```

---

### 3. Cleanup System

```bash
curl -X POST http://localhost:8000/maintenance/cleanup
```

---

## 📁 Artifact Structure

Each execution creates:

```
/tmp/agent-runs/<run_id>/
  ├── workspace/          # isolated git worktree
  ├── artifacts/
  │    ├── plan.json
  │    ├── execution.json
  │    ├── summary.json
  │    └── diff.patch
```

---

## 🧠 Core Concepts

- **run_id** → unique execution identifier
- **worktree** → isolated Git workspace per run
- **actions** → atomic file operations
- **diff** → Git-generated change set
- **agent branches** → temporary branches (`agent-*`)

---

## 🧹 Maintenance

Manual cleanup (if needed):

```bash
rm -rf /tmp/agent-runs/*
```

Or via API:

```bash
POST /maintenance/cleanup
```

---

## 🤖 MCP Tools

| Tool | Params | Description |
|------|--------|-------------|
| `agent_plan` | prompt | Generate execution plan |
| `agent_apply` | run_id, plan | Execute plan in sandbox |
| `get_run` | run_id | Retrieve run metadata |
| `agent_review` | run_id | Inspect plan, diff, execution |
| `agent_run` | prompt | One-shot plan + apply |
| `agent_approve` | run_id | Approve and merge changes |
| `agent_reject` | run_id | Reject and cleanup |

---

## ⚠️ Important Notes

- Do NOT mix Ollama with vLLM (deprecated)
- AWQ model requires GPU memory tuning (12GB constraint)
- Keep `run_id` isolated per execution
- Worktrees must never be shared between runs
- Cleanup is mandatory to avoid disk accumulation

---

## 📊 System Status Goals

- ✔ Deterministic plan generation
- ✔ Isolated execution per run
- ✔ Stable vLLM inference layer
- ✔ Git-based diff tracking
- ✔ Full cleanup lifecycle

---

## 🚀 Future Improvements

- `/health/llm` endpoint (vLLM monitoring)
- Streaming plan generation
- Multi-model routing
- Tool-calling structured enforcement
- Review scoring system for generated code

