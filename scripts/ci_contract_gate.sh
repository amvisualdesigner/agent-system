#!/bin/bash
# CI gate: run all contract checks before merging
set -euo pipefail

echo "=== Contract Registry Validation ==="
python3 scripts/contract_validator.py

echo ""
echo "=== Contract Lint ==="
python3 scripts/contract_lint.py

echo ""
echo "=== Duplicate Detection ==="
python3 scripts/contract_duplicate_detector.py

echo ""
echo "ALL CHECKS PASSED"
