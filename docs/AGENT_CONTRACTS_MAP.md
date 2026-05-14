# 🧠 Agent System — Contracts Map (v2)

This document defines **all contracts required for the full agent pipeline** after migration to vLLM + schema v2.

---

# 🧩 1. Core Contracts (REQUIRED)

## 1.1 agent.md (LLM Behavior Contract)
Defines how the model behaves.

**Responsibility:**
- Converts user task → structured plan intent
- Must obey schema v2 strictly
- No hallucinated fields
- No explanations outside JSON

**Output:**
```json
{
  "actions": [
    {
      "type": "create|modify|delete",
      "file_path": "string",
      "description": "string"
    }
  ]
}
```

---

## 1.2 schema_v2.md (Data Contract)
Defines strict transport schema between LLM → backend.

**Schema:**
- actions: array
- type: action type
- file_path: target file
- description: intent only (NOT code)

**Rules:**
- No `steps`
- No `proposed_content`
- No nested execution logic

---

## 1.3 execution_contract.md (Runtime Contract) ✔ (exists)
Defines deterministic execution in Git worktrees.

**Responsibilities:**
- Create isolated worktree per run_id
- Apply file operations
- Generate diff
- Produce artifacts:
  - plan.json
  - execution.json
  - diff.patch

---

## 1.4 compiler_contract.md (❗ CRITICAL MISSING PIECE)
Defines how descriptions become real code.

**Responsibility:**
- Convert `description → concrete code changes`
- Must NOT call LLM
- Must be deterministic or rule-based

**Rules:**
- If ambiguity → fail or request refinement
- Must preserve project style
- Must validate syntax before writing files

**Output example:**
```json
{
  "file_path": "src/util.ts",
  "content": "export function add(a: number, b: number): number { return a + b; }"
}
```

---

# 🔁 2. Pipeline Contracts

## 2.1 planner_contract.md
Defines LLM → plan generation constraints.

**Responsibilities:**
- Interpret user request
- Produce valid schema v2
- No code generation
- No execution logic

---

## 2.2 validation_contract.md
Defines strict validation rules for plans.

**Rules:**
- actions must be non-empty array
- each action must include required keys
- reject unknown types
- reject malformed JSON

**Failure modes:**
- invalid_json_from_llm
- missing_fields
- schema_violation

---

## 2.3 tool_contract.md
Defines tools exposed to agent runtime.

**Tools:**
- agent_plan
- agent_apply
- agent_review
- agent_run

**Rules:**
- Tools must be stateless
- No side effects outside execution_contract

---

# 🌐 3. API Contracts

## 3.1 api_contract.md
Defines FastAPI endpoints.

**Endpoints:**
- POST /agent/plan
- POST /agent/apply
- POST /maintenance/cleanup

**Rules:**
- plan returns schema v2
- apply consumes execution_contract
- cleanup is idempotent

---

# 🧠 4. System Behavior Contract

## 4.1 orchestration_rules.md
Defines global system rules.

**Rules:**
- LLM never writes code directly
- Compiler is single source of truth for code generation
- Execution is isolated per run_id
- No cross-run contamination

---

# 🔥 FINAL ARCHITECTURE

```text
User
  ↓
agent.md (LLM reasoning)
  ↓
schema_v2.md (structured plan)
  ↓
validation_contract.md
  ↓
compiler_contract.md (code synthesis)
  ↓
execution_contract.md (git + worktree)
  ↓
API layer (FastAPI)
```

---

# ⚠️ CRITICAL INSIGHT

The system quality depends MOST on:

1. compiler_contract.md  ← biggest impact on code quality
2. schema_v2.md         ← prevents ambiguity
3. validation_contract ← prevents garbage input

---

# 🚀 Status

✔ execution_contract.md exists
✔ vLLM migration defined
✔ schema v2 defined
❗ compiler_contract.md missing (next priority)

---

# NEXT STEP RECOMMENDED

Implement:
👉 compiler_contract.md first

This is the component that upgrades output quality from:

> “LLM ideas” → “production-grade code”