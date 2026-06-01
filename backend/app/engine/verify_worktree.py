"""Post-apply worktree verification — tsc/build check.

Usage:
    from app.engine.verify_worktree import verify_worktree
    result = verify_worktree("/path/to/workspace")

Returns:
    {
        "status": "passed" | "failed" | "skipped",
        "check": "tsc" | "npm_run_build" | "none",
        "errors": [...],
        "output": "..."
    }

Design:
  - Tries tsc --noEmit first (faster, no bundler needed)
  - Falls back to npm run build if tsc not available
  - Gracefully skips if node/npm not installed
  - Timeout protects against hanging builds
  - Never raises — always returns a dict
"""

from __future__ import annotations

import logging
import os
import subprocess
import shutil
import json

logger = logging.getLogger(__name__)

VERIFY_TIMEOUT = 60  # seconds


def _find_frontend_dir(workspace: str) -> str | None:
    """Find the frontend/ or root directory containing package.json."""
    candidates = [
        os.path.join(workspace, "frontend"),
        workspace,
    ]
    for cand in candidates:
        pkg = os.path.join(cand, "package.json")
        if os.path.isfile(pkg):
            return cand
    return None


def _check_node_available() -> bool:
    return shutil.which("node") is not None


def _check_npm_available() -> bool:
    return shutil.which("npm") is not None


def _check_tsc_available(frontend_dir: str) -> bool:
    """Check if tsc is available (installed locally via node_modules)."""
    local_tsc = os.path.join(frontend_dir, "node_modules", ".bin", "tsc")
    return os.path.isfile(local_tsc) or shutil.which("tsc") is not None


def _run_cmd(cmd: list[str], cwd: str, timeout: int = VERIFY_TIMEOUT) -> dict:
    """Run a command and return result dict."""
    try:
        r = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "returncode": r.returncode,
            "stdout": r.stdout[-2000:] if r.stdout else "",
            "stderr": r.stderr[-2000:] if r.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s",
        }
    except FileNotFoundError:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": "Command not found",
        }


def verify_worktree(workspace: str) -> dict:
    """Run post-apply verification on a workspace.

    Returns a dict with status, errors, check type, and output.
    """
    frontend_dir = _find_frontend_dir(workspace)
    if not frontend_dir:
        logger.info("[verify] No package.json found in workspace — skipped")
        return {
            "status": "skipped",
            "check": "none",
            "errors": [],
            "output": "No package.json found",
        }

    if not _check_node_available():
        logger.info("[verify] Node.js not available — skipped")
        return {
            "status": "skipped",
            "check": "none",
            "errors": [],
            "output": "Node.js not available in this environment",
        }

    # Ensure node_modules exists
    node_modules_dir = os.path.join(frontend_dir, "node_modules")
    if not os.path.isdir(node_modules_dir):
        logger.info("[verify] Running npm install in %s", frontend_dir)
        install_result = _run_cmd(
            ["npm", "install", "--no-audit", "--no-fund", "--prefer-offline"],
            frontend_dir,
            timeout=120,
        )
        if install_result["returncode"] != 0:
            logger.warning("[verify] npm install failed")
            return {
                "status": "failed",
                "check": "npm_install",
                "errors": [install_result["stderr"][:500]],
                "output": (install_result["stderr"] or install_result["stdout"])[:2000],
            }
    else:
        logger.info("[verify] node_modules found — skipping npm install")

    # Try tsc --noEmit first (faster, no bundler)
    tsc_available = _check_tsc_available(frontend_dir)
    if tsc_available:
        local_tsc = os.path.join(frontend_dir, "node_modules", ".bin", "tsc")
        tsc_cmd = [local_tsc, "--noEmit"] if os.path.isfile(local_tsc) else ["npx", "tsc", "--noEmit"]
        logger.info("[verify] Running tsc --noEmit in %s", frontend_dir)
        result = _run_cmd(tsc_cmd, frontend_dir)
        if result["returncode"] == 0:
            logger.info("[verify] tsc --noEmit passed")
            return {
                "status": "passed",
                "check": "tsc",
                "errors": [],
                "output": result["stdout"][:1000],
            }
        else:
            # Parse tsc errors
            errors = _parse_tsc_errors(result["stderr"] or result["stdout"])
            logger.warning("[verify] tsc --noEmit failed (%d errors)", len(errors))
            return {
                "status": "failed",
                "check": "tsc",
                "errors": errors,
                "output": (result["stderr"] or result["stdout"])[:2000],
            }

    # Fallback: npm run build
    if _check_npm_available():
        logger.info("[verify] Running npm run build in %s", frontend_dir)
        result = _run_cmd(["npm", "run", "build"], frontend_dir)
        if result["returncode"] == 0:
            logger.info("[verify] npm run build passed")
            return {
                "status": "passed",
                "check": "npm_run_build",
                "errors": [],
                "output": result["stdout"][:1000],
            }
        else:
            logger.warning("[verify] npm run build failed")
            return {
                "status": "failed",
                "check": "npm_run_build",
                "errors": [result["stderr"][:500]] if result["stderr"] else [],
                "output": (result["stderr"] or result["stdout"])[:2000],
            }

    # Neither tsc nor npm available but node is installed — unusual
    logger.info("[verify] tsc and npm not found — skipped")
    return {
        "status": "skipped",
        "check": "none",
        "errors": [],
        "output": "tsc and npm not available",
    }


def _parse_tsc_errors(output: str) -> list[dict]:
    """Parse tsc error output into structured error list."""
    errors = []
    for line in output.split("\n"):
        line = line.strip()
        if not line:
            continue
        # Typical tsc error: "src/file.tsx:12:5 - error TS2304: Cannot find module '...'"
        if "error TS" in line:
            errors.append({"message": line[:300]})
        elif line.startswith("src/") or line.startswith("frontend/"):
            if "error" in line.lower():
                errors.append({"message": line[:300]})
    if not errors and output.strip():
        # If no structured errors found, include the whole output as one error
        errors.append({"message": output.strip()[:500]})
    return errors
