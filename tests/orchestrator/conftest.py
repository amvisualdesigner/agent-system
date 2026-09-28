"""S1-A.7 — orchestrator test setup.

Adds the orchestrator directory to sys.path so its LOCAL run_id module
(validate_run_id + guard_within) is importable without a shared package.
"""

import os
import sys

ORCHESTRATOR_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "orchestrator")
)
# Append (not prepend) so backend/main.py and other top-level modules are never
# shadowed by the orchestrator namespace during the same session.
if ORCHESTRATOR_DIR not in sys.path:
    sys.path.append(ORCHESTRATOR_DIR)