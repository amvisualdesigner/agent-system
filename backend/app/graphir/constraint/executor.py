"""FileOpExecutor — pure EditOperation → FileOp computation.
FileOpApplier — sole mutation authority, writes FileOps to disk.

Architecture:
  FileOpExecutor (PURE): EditOperation + optional existing_content → FileOp[]
    NO filesystem reads, NO writes. Pure computation only.

  FileOpApplier (IO): FileOp[] → filesystem writes.
    SOLE mutation authority in the system. Atomic writes via tempfile + rename.
"""

from __future__ import annotations

import logging
import os
import tempfile

from app.graphir.models import FileOp
from app.graphir.constraint.diff import EditOperation

logger = logging.getLogger(__name__)


class FileOpExecutor:
    """PURE: converts EditOperations to FileOps. Does NOT read or write.

    Args:
        workspace_root: Absolute path to workspace root (for path joining).
    """

    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root
        self.errors: list[str] = []

    def execute(
        self, edit: EditOperation,
        existing_content: str | None = None,
    ) -> list[FileOp]:
        """Convert an EditOperation to FileOps. Pure — no IO.

        Args:
            edit: EditOperation from StructuralDiffEngine.
            existing_content: Existing file content for surgical edits.
                When None, treats missing files as greenfield (create).

        Returns:
            list[FileOp] with the edit applied.
        """
        if edit.action == "create":
            return [FileOp(action="create", path=edit.source_file, content=edit.content)]

        if edit.action == "delete_file":
            return [FileOp(action="delete", path=edit.source_file, content="")]

        if edit.action == "replace_file":
            return [FileOp(action="modify", path=edit.source_file, content=edit.content)]

        if edit.action == "replace_range":
            return self._compute_surgical(edit, existing_content or "")

        if edit.action == "insert_range":
            return self._compute_insert(edit, existing_content or "")

        logger.warning("Unknown edit action: %s (file=%s)", edit.action, edit.source_file)
        self.errors.append(f"Unknown edit action: {edit.action}")
        return []

    def _compute_surgical(
        self, edit: EditOperation, existing_content: str,
    ) -> list[FileOp]:
        """Compute content for range replacement. Pure — no IO."""
        if not existing_content:
            return [FileOp(action="create", path=edit.source_file, content=edit.content)]

        patched = self._apply_surgical(existing_content, edit)
        return [FileOp(action="modify", path=edit.source_file, content=patched)]

    def _compute_insert(
        self, edit: EditOperation, existing_content: str,
    ) -> list[FileOp]:
        """Compute content for range insertion. Pure — no IO."""
        if not existing_content:
            return [FileOp(action="create", path=edit.source_file, content=edit.content)]

        patched = self._apply_surgical(existing_content, edit)
        return [FileOp(action="modify", path=edit.source_file, content=patched)]

    @staticmethod
    def _apply_surgical(existing: str, edit: EditOperation) -> str:
        """Apply surgical replacement to existing file content.

        replace_range: replaces lines [range_start-1:range_end]
        insert_range: inserts content at line range_start-1

        Preserves trailing newline if present in existing content.
        """
        lines = existing.split("\n")
        trailing_newline = existing.endswith("\n")

        if edit.action == "insert_range":
            before = lines[:edit.range_start - 1]
            after = lines[edit.range_start - 1:]
            result = "\n".join(before + [edit.content] + after)
        else:
            before = lines[:edit.range_start - 1]
            after = lines[edit.range_end:]
            result = "\n".join(before + [edit.content] + after)

        if trailing_newline and not result.endswith("\n"):
            result += "\n"
        return result


class FileOpApplier:
    """SOLE mutation authority. Writes FileOps to disk atomically.

    Args:
        workspace_root: Absolute path to workspace root.
            All FileOp.path values are relative to this.
    """

    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root
        self.errors: list[str] = []

    def apply(self, fileops: list[FileOp]) -> list[dict]:
        """Apply FileOps to disk. Each FileOp is written atomically.

        Returns:
            list[dict] with status per FileOp.
        """
        results: list[dict] = []
        for fop in fileops:
            result = self._apply_one(fop)
            results.append(result)
        return results

    def _apply_one(self, fop: FileOp) -> dict:
        """Apply a single FileOp to disk."""
        path = os.path.normpath(os.path.join(self.workspace_root, fop.path))
        if not path.startswith(os.path.realpath(self.workspace_root) + os.sep):
            return {"status": "rejected", "reason": "path_escape", "path": fop.path}

        if fop.action in ("create", "modify"):
            self._atomic_write(path, fop.content)
            return {
                "status": "created" if fop.action == "create" else "modified",
                "path": path,
            }
        elif fop.action == "delete":
            if os.path.isfile(path):
                os.remove(path)
                return {"status": "deleted", "path": path}
            return {"status": "rejected", "reason": "file_not_found", "path": path}

        return {"status": "rejected", "reason": "unknown_action"}

    @staticmethod
    def _atomic_write(file_path: str, content: str) -> None:
        """Write content atomically using temp file + rename.

        Prevents partial writes that could corrupt the file.
        """
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=os.path.dirname(file_path),
            prefix=".opencode_tmp_",
            suffix=".tsx",
        )
        try:
            with os.fdopen(fd, "w") as f:
                f.write(content)
            os.replace(tmp_path, file_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            raise
