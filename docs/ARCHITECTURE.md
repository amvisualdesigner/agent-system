## High-Level System Overview

The project is an AI-assisted agent backend designed for:
- planning code modifications,
- generating structured actions,
- executing deterministic workflows,
- supporting future tool-calling agents.

The architecture prioritizes:
- modularity,
- observability,
- deterministic behavior,
- operational stability.

---

# Core Layers

## 1. API Layer

Primary responsibilities:
- expose HTTP endpoints,
- validate requests,
- return structured responses,
- isolate transport concerns.

Technology:
- FastAPI

Rules:
- no business logic in endpoints,
- no LLM logic in routes,
- endpoints should orchestrate only.

---

## 2. Planner Layer

Primary responsibilities:
- build prompts,
- call LLMs,
- validate outputs,
- generate execution plans.

Key file:
- `backend/app/planner/plan_generator.py`

Rules:
- planner outputs must be deterministic,
- planner must validate JSON,
- planner must retry malformed responses,
- planner must never trust raw LLM output.

---

## 3. Configuration Layer

Primary responsibilities:
- centralize environment variables,
- provide typed settings,
- avoid scattered configuration.

Key location:
- `backend/app/config/settings.py`

Rules:
- all runtime config must live here,
- no hardcoded infrastructure values.

---

## 4. Infrastructure Layer

Primary responsibilities:
- Docker services,
- GPU runtime,
- model serving,
- health checks,
- operational stability.

Current target stack:
- vLLM
- Qwen2.5-Coder-14B-Instruct-AWQ
- CUDA GPU runtime

---

# LLM Architecture

## Serving Layer

The project uses:
- vLLM OpenAI-compatible API

Reasoning:
- robust tool calling,
- PagedAttention,
- better throughput,
- predictable latency.

---

## Model Constraints

Target hardware:
- RTX 5070 12GB

Constraints:
- VRAM is limited,
- KV cache pressure matters,
- concurrency must remain conservative.

Operational priorities:
1. Stability
2. Predictable latency
3. Reliable JSON
4. Controlled VRAM usage
5. Throughput last

---

# Reliability Philosophy

The system assumes:
- LLMs may fail,
- malformed JSON will happen,
- GPU services may warm slowly,
- requests may timeout,
- retries are normal.

Therefore:
- all critical operations need retries,
- all responses require validation,
- all services need health checks,
- logging must support debugging.

---

# Observability

Required observability:
- structured logging,
- request latency,
- retry counts,
- HTTP status failures,
- model availability.

Future improvements:
- `/health/llm`
- metrics export
- request tracing
- VRAM telemetry

---

# Repository Organization

Recommended structure:

```text
backend/
  app/
    api/
    planner/
    config/
    services/
    utils/

scripts/

docs/
  architecture/
  migrations/
```

---

# Design Constraints

## Keep Layers Separated

Do not mix:
- API transport,
- LLM orchestration,
- infrastructure management,
- utility helpers.

---

## Avoid Hidden Coupling

Avoid:
- importing across unrelated layers,
- hidden global state,
- circular dependencies,
- scattered configuration.

---

## Prefer Explicit Flows

Good systems are:
- traceable,
- debuggable,
- explicit.

Avoid magic behavior.

---

# Future Evolution

Planned future directions:
- tool calling agents,
- executor layer,
- structured memory,
- distributed workers,
- better health monitoring,
- multi-model support,
- request queueing.

The architecture should remain simple enough to evolve incrementally.