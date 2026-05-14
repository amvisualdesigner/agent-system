# ⚙️ Execution Contract — Agent System v2

This document defines the **runtime execution rules** that transform compiled actions into real filesystem and Git operations.

It is the contract between:

- `execution_compiler.py`
- `executor (Git worktree layer)`
- `artifact system`

---

# 🧠 Core Principle

> The executor is deterministic and non-LLM.

It must execute instructions **exactly as specified**, without interpretation or creativity.

---

# 📦 Input Contract

The executor receives compiled operations in this format:

```json
{
  "operations": [
    {
      "action": "create | update | delete | patch | rewrite",
      "path": "relative/file/path",
      "proposed_content": "..."
    }
  ]
}
```

---

# 🎯 Operation Semantics

## 1. create

Creates a new file.

Rules:

- File MUST NOT exist
- If file exists → error or overwrite policy (see below)
- Directory MUST be created if missing

---

## 2. update

Modifies an existing file.

Rules:

- File MUST exist
- Applies full overwrite OR patch depending on compiler output
- Must preserve unrelated content unless explicitly replaced

---

## 3. delete

Removes a file.

Rules:

- File MUST exist
- Must be removed from filesystem AND git index

---

## 4. patch

Applies a diff-based modification.

Rules:

- Must be valid unified diff OR internal patch format
- Must fail safely if patch cannot be applied cleanly

---

## 5. rewrite

Full file replacement.

Rules:

- Overwrites entire file content
- Most deterministic operation
- Preferred over patch when possible

---

# 🧱 Path Resolution Rules

- All paths are relative to repository root
- No absolute paths allowed
- No `/tmp`, `/home`, system paths
- Must resolve inside active worktree only

---

# 🔁 Idempotency Rules

The executor MUST ensure:

- Re-running same operation produces same result
- No duplicate file creation
- Safe re-application of identical updates

---

# ⚠️ Conflict Handling

## File already exists (create)

Policy:

- Default: FAIL
- Optional mode: overwrite (explicit only)

---

## File missing (update/delete)

- Must fail with explicit error

---

## Patch failure

If patch cannot be applied:

- retry with full rewrite fallback (if available)
- otherwise fail execution

---

# 🧪 Git Integration Rules

After each operation batch:

- Stage changes (`git add`)
- Ensure repository consistency
- Generate diff snapshot

---

# 📊 Artifact Generation

Each run MUST produce:

```
plan.json
execution.json
diff.patch
summary.json
```

Rules:

- Must reflect actual filesystem state
- Must NOT reflect planned state
- Must be generated AFTER execution

---

# 🧠 Safety Rules

Executor MUST NOT:

- Execute shell commands from LLM
- Interpret natural language
- Modify files outside repo
- Access network

---

# 🔒 Determinism Rules

- Same input → same output
- No randomness
- No heuristics outside compiler output

---

# 🧱 Error Handling

All errors MUST be explicit:

- `FILE_NOT_FOUND`
- `FILE_ALREADY_EXISTS`
- `PATCH_FAILED`
- `INVALID_OPERATION`
- `GIT_ERROR`

Errors must be logged in `execution.json`

---

# 🔄 Execution Flow

```
compiled operations
      ↓
executor
      ↓
filesystem changes
      ↓
git staging
      ↓
diff generation
      ↓
artifact writing
```

---

# 🧠 Boundary Definition

The executor is:

- ❌ NOT a planner
- ❌ NOT a code generator
- ❌ NOT a validator

It is ONLY:

> a deterministic file + git state machine

---

# 📌 Version

v2 — aligned with:

- vLLM planner
- schema v2 actions
- execution_compiler layer separation

---

# 🚀 Design Goal

Enable:

- fully deterministic execution
- reproducible runs
- safe Git-based modifications
- clean separation of concerns

---

# 🔮 Future Extensions

- transactional execution (rollback support)
- partial failure recovery
- parallel execution of independent actions
- signature-based audit trail