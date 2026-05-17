import os

from app.renderer.base import FileOp

ALLOWED_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".py", ".md", ".json", ".yaml", ".yml", ".html", ".css"}
BLOCKED_PATTERNS = [".git", "node_modules", "dist", "build", ".env"]
MAX_FILE_SIZE = 200_000


def validate_fileops(fileops: list[FileOp]) -> tuple[bool, str]:
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
