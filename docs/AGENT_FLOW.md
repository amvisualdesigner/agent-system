# 🔄 Agent System v2 — Execution Flow (Schema Migration)

This document describes the **end-to-end execution flow** of the Agent System after migrating to the new LLM schema:

```json
{
  "actions": [
    {
      "type": "create|update|delete",
      "file_path": "...",
      "description": "..."
    }
  ]
}
```

---

# 🧠 1. User Input

The system starts with a natural language request:

```
Add a comment to main.py that prints hello world
```

This input is sent to the LLM planner.

---

# 🤖 2. LLM Planning (vLLM)

The model returns a **structured intent-only plan**:

```json
{
  "actions": [
    {
      "type": "update",
      "file_path": "main.py",
      "description": "Add a comment that prints hello world at the top of the file"
    }
  ]
}
```

### Key principle

- ❌ No code generation here
- ❌ No file content
- ✔ Only structured intent

---

# 🧾 3. plan_validator.py (Validation Layer)

Responsibilities:

- Validate JSON structure
- Ensure required fields exist:
  - type
  - file_path
  - description
- Reject malformed or incomplete actions

### Output

Normalized internal representation:

```python
NormalizedAction(
    type="update",
    file_path="main.py",
    description="Add comment..."
)
```

---

# ⚙️ 4. execution_compiler.py (Compilation Layer)

This is where **intent becomes real code changes**.

### Input

```json
{
  "type": "update",
  "file_path": "main.py",
  "description": "Add comment that prints hello world"
}
```

### Output

```json
{
  "operations": [
    {
      "action": "patch",
      "path": "main.py",
      "proposed_content": "# prints hello world\nprint('hello world')"
    }
  ]
}
```

### Key idea

- LLM describes intent
- Compiler generates actual file content

---

# 🧱 5. Git Worktree Execution

For each `run_id`:

- Create isolated worktree
- Apply file operations
- Stage changes with git

```
/tmp/agent-runs/<run_id>/workspace/
```

---

# 📊 6. Diff Generation

After execution:

```bash
git diff
```

Example output:

```diff
+ # prints hello world
+ print('hello world')
```

---

# 📦 7. Artifact System

Each run generates:

```
/tmp/agent-runs/<run_id>/
  ├── workspace/
  ├── artifacts/
  │   ├── plan.json
  │   ├── execution.json
  │   ├── diff.patch
  │   └── summary.json
```

---

# 🔄 Full System Flow

```
User
  ↓
vLLM (planner)
  ↓
actions (intent-only)
  ↓
plan_validator
  ↓
execution_compiler
  ↓
git worktree executor
  ↓
diff + artifacts
```

---

# 🧠 Architectural Principles

## 1. Separation of concerns

| Layer | Responsibility |
|------|----------------|
| LLM | Intent generation |
| Validator | Schema correctness |
| Compiler | Code generation |
| Executor | Git + filesystem |
| Diff engine | Observability |

---

## 2. LLM is NOT a code generator

The LLM only produces structured intent.

All deterministic logic lives in backend layers.

---

## 3. Determinism > Creativity

- temperature = 0.0
- strict schema enforcement
- validation before execution

---

# 🚀 Why this design is important

This architecture enables:

- predictable execution
- safe code generation
- easier debugging
- tool-calling readiness
- future multi-agent orchestration

---

# 📈 Future extensions

- `/health/llm` monitoring endpoint
- multi-step reasoning (analyze → plan → execute)
- policy engine before execution
- review scoring system
- tool-calling native agents