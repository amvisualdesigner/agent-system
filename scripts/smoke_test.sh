#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# Smoke tests for all 4 containers (Fase 5.5)
# Usage: bash scripts/smoke_test.sh
# Exit code: 0 = all green, 1 = any failure
# ──────────────────────────────────────────────────────────────
set -euo pipefail

PASS=0
FAIL=0
COMPOSE_PROJECT=${COMPOSE_PROJECT:-agent-system}

green() { printf "  ✅ %s\n" "$1"; }
red()   { printf "  ❌ %s\n" "$1"; fail=1; }

echo "═══ Smoke Tests ═══"
echo ""

# ── 1. Docker ────────────────────────────────────────────────
echo "── Docker ──"
if command -v docker &>/dev/null; then
    green "docker CLI available"
else
    red "docker CLI not found"
fi

# ── 2. Container status ──────────────────────────────────────
echo "── Container Status ──"
for svc in vllm agent-backend agent-orchestrator agent-ui; do
    status=$(docker inspect --format='{{.State.Status}}' "$svc" 2>/dev/null || echo "not_found")
    if [ "$status" = "running" ]; then
        green "$svc is running"
    else
        red "$svc is $status"
    fi
done

# ── 3. vLLM health ───────────────────────────────────────────
echo "── vLLM (port 7000) ──"
if curl -sf http://localhost:7000/health >/dev/null 2>&1; then
    green "/health responds 200"
else
    red "/health not reachable"
fi

# ── 4. Backend health ────────────────────────────────────────
echo "── Backend (port 8000) ──"
if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
    green "/health responds 200"
else
    red "/health not reachable"
fi

# ── 5. Orchestrator health ───────────────────────────────────
echo "── Orchestrator (port 9000) ──"
if curl -sf http://localhost:9000/health >/dev/null 2>&1; then
    green "/health responds 200"
else
    red "/health not reachable"
fi

# ── 6. UI health ─────────────────────────────────────────────
echo "── UI (port 5173) ──"
if curl -sf -o /dev/null http://localhost:5173/; then
    green "UI responds 200"
else
    red "UI not reachable"
fi

# ── Summary ──────────────────────────────────────────────────
echo ""
if [ "$FAIL" -gt 0 ]; then
    echo "❌ $FAIL failures"
    exit 1
else
    echo "✅ All smoke tests passed"
    exit 0
fi
