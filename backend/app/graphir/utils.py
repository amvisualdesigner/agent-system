"""Shared utilities for GraphIR modules."""
import os

from app.graphir.models import FileOp

ALLOWED_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".py", ".md", ".json", ".yaml", ".yml", ".html", ".css"}
BLOCKED_PATTERNS = [".git", "node_modules", "dist", "build", ".env"]
MAX_FILE_SIZE = 200_000
_INTEGRITY_IGNORE_DIRS = {"node_modules", ".git", "dist", "build", "__pycache__", ".venv"}


def extract_component_name(file_path: str) -> str:
    """Extract component name from a file path.

    'components/KpiRow.tsx' → 'KpiRow'
    'src/pages/dashboard/Page.tsx' → 'Page'
    """
    stem = os.path.splitext(os.path.basename(file_path))[0]
    return stem


def validate_fileops(fileops: list[FileOp]) -> tuple[bool, str]:
    """Validate FileOp list for safety and completeness."""
    for i, op in enumerate(fileops):
        ext = os.path.splitext(op.path)[1]
        if ext not in ALLOWED_EXTENSIONS:
            return False, f"op[{i}]: extension not allowed: {op.path}"

        normalized = os.path.normpath(op.path)
        parts = normalized.split(os.sep)
        if any(p in BLOCKED_PATTERNS for p in parts):
            return False, f"op[{i}]: blocked path: {op.path}"

        if op.action == "create" and not op.content:
            return False, f"op[{i}]: create without content: {op.path}"

        if len(op.content.encode("utf-8")) > MAX_FILE_SIZE:
            return False, f"op[{i}]: file too large: {op.path}"

    return True, "ok"


def check_repo_integrity(workspace_root: str) -> dict:
    """Lightweight post-write checks on the repository.

    Returns a dict with status, files_checked, and a list of issues:
      - JSX imbalance (open vs close tags)
      - Duplicate imports within a file
      - Broken export sources pointing to nonexistent paths
    """
    if not workspace_root or not os.path.isdir(workspace_root):
        return {"status": "skipped", "reason": "no_workspace", "issues": [], "files_checked": 0}

    issues: list[dict] = []
    tsx_files: list[str] = []
    for root, dirs, files in os.walk(workspace_root):
        dirs[:] = [d for d in dirs if d not in _INTEGRITY_IGNORE_DIRS]
        for f in files:
            if f.endswith((".tsx", ".ts")):
                tsx_files.append(os.path.join(root, f))

    for fp in tsx_files:
        _check_jsx_balance(fp, workspace_root, issues)
        _check_duplicate_imports(fp, workspace_root, issues)
        _check_broken_exports(fp, workspace_root, issues)

    return {
        "status": "ok",
        "files_checked": len(tsx_files),
        "issues": issues,
        "issues_count": len(issues),
    }


def _check_jsx_balance(fp: str, workspace_root: str, issues: list[dict]) -> None:
    """Check JSX tag balance (> vs </) in a tsx file."""
    if not fp.endswith(".tsx"):
        return
    try:
        with open(fp) as fh:
            content = fh.read()
    except Exception:
        return
    opens = content.count(">")
    closes = content.count("</")
    if opens > 0 and closes > 0 and closes > opens:
        issues.append({
            "severity": "error",
            "type": "jsx_imbalance",
            "detail": f"{os.path.relpath(fp, workspace_root)}: {opens} open vs {closes} close tags",
            "file": os.path.relpath(fp, workspace_root),
        })


def _check_duplicate_imports(fp: str, workspace_root: str, issues: list[dict]) -> None:
    """Check for duplicate import sources within a file."""
    try:
        with open(fp) as fh:
            lines = fh.readlines()
    except Exception:
        return
    imports_seen: dict[str, int] = {}
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("import ") and " from " in stripped:
            source = stripped.split(" from ")[-1].strip().rstrip(";")
            if source in imports_seen:
                issues.append({
                    "severity": "warning",
                    "type": "duplicate_import",
                    "detail": f"{os.path.relpath(fp, workspace_root)}:{i + 1}: duplicate import of {source}",
                    "file": os.path.relpath(fp, workspace_root),
                })
            imports_seen[source] = i + 1


def _check_broken_exports(fp: str, workspace_root: str, issues: list[dict]) -> None:
    """Check that re-export sources exist on disk."""
    try:
        with open(fp) as fh:
            content = fh.read()
    except Exception:
        return
    rel = os.path.relpath(fp, workspace_root)
    dir_fp = os.path.dirname(fp)
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("export ") and " from " in stripped:
            source = stripped.split(" from ")[-1].strip().rstrip(";").strip("'\"")
            if source.startswith("."):
                resolved = os.path.normpath(os.path.join(dir_fp, source))
                found = False
                for ext in (".tsx", ".ts", ".jsx", ".js", ""):
                    if os.path.exists(resolved + ext) or os.path.isdir(resolved + ext):
                        found = True
                        break
                if not found:
                    issues.append({
                        "severity": "warning",
                        "type": "broken_export_source",
                        "detail": f"{rel}: export from {source} not found (resolved: {resolved})",
                        "file": rel,
                    })
