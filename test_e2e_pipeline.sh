#!/usr/bin/env bash
# Quick E2E pipeline test: interpret → confirm → apply
# Usage: bash test_e2e_pipeline.sh [message]
# Default: "remove the KPI row"

set -euo pipefail
MSG="${1:-remove the KPI row}"
BASE="http://localhost:8000"
RUN_ID=$(python3 -c "import uuid; print(uuid.uuid4())")

echo "≡ E2E PIPELINE  (run: ${RUN_ID})"
echo "  Message:  ${MSG}"
echo

# 1. Interpret
echo "── 1. INTERPRET ──"
RESP=$(curl -s -X POST "${BASE}/agent/interpret" \
  -H "Content-Type: application/json" \
  -d "{\"run_id\":\"${RUN_ID}\",\"message\":\"${MSG}\",\"conversation\":[]}")
echo "${RESP}" | python3 -m json.tool 2>/dev/null | head -20
INTENT_ID=$(echo "${RESP}" | python3 -c "import sys,json; print(json.load(sys.stdin)['interpretation_id'])")
CONTRACT_ID=$(echo "${RESP}" | python3 -c "import sys,json; print(json.load(sys.stdin)['contract_id'])")
ACTIONS=$(echo "${RESP}" | python3 -c "
import sys, json
d = json.load(sys.stdin)
acts = [{'verb':a['verb'],'target_capability':a['target_capability']} for a in d['proposed_actions']]
print(json.dumps(acts))
")
echo

# 2. Confirm
echo "── 2. CONFIRM ──"
CONFIRM_PAYLOAD="{\"run_id\":\"${RUN_ID}\",\"interpretation_id\":\"${INTENT_ID}\",\"contract_id\":\"${CONTRACT_ID}\",\"actions\":${ACTIONS}}"
RESP2=$(curl -s -X POST "${BASE}/agent/confirm" \
  -H "Content-Type: application/json" \
  -d "${CONFIRM_PAYLOAD}")
echo "${RESP2}" | python3 -c "
import sys, json
d = json.load(sys.stdin)
pv = d.get('plan_preview', {})
print('  Summary: ' + pv.get('summary_human', '?'))
print('  Routes:  ' + json.dumps(pv.get('routes', {})))
print('  Files:   ' + json.dumps(pv.get('estimated_files', [])))
"
echo

# 3. Apply
echo "── 3. APPLY ──"
RESP3=$(curl -s -X POST "${BASE}/agent/apply" \
  -H "Content-Type: application/json" \
  -d "{\"run_id\":\"${RUN_ID}\"}")
echo "${RESP3}" | python3 -c "
import sys, json
d = json.load(sys.stdin)
exec_r = d.get('execution', {})
print('  Status: ' + exec_r.get('status', '?'))
ops = exec_r.get('operations', [])
print('  Operations:')
for op in ops:
    print('    [' + op.get('pipeline_route', '?').ljust(15) + '] ' + op.get('action', '?') + '  ' + op.get('path', ''))
"
echo
echo "✓ Done"
