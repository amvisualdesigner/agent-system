"""FileOpExecutor — IO layer for applying EditOperations.

State Layer: reads filesystem, writes filesystem.
NO decision logic, NO content generation, NO structural diff.

Responsibility: convert EditOperation → FileOp by reading existing
file content and applying surgical replacements.

Atomicity: writes to temp file then renames to prevent partial writes.
"""

from __future__ import annotations

import logging
import os
import tempfile

from app.graphir.models import FileOp
from app.graphir.constraint.diff import EditOperation

logger = logging.getLogger(__name__)


class FileOpExecutor:
    """IO layer: applies EditOperations to produce FileOps.

    Args:
        workspace_root: Absolute path to workspace root.
    """

    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root
        self.errors: list[str] = []

    def execute(self, edit: EditOperation) -> list[FileOp]:
        """Convert an EditOperation to one or more FileOps.

        Reads existing file content for surgical edits.
        Returns empty list on errors (logged, not raised).

        Args:
            edit: EditOperation from StructuralDiffEngine.

        Returns:
            list[FileOp] with the edit applied.
        """
        file_path = os.path.join(self.workspace_root, edit.source_file)

        if edit.action == "create":
            return [FileOp(action="create", path=edit.source_file, content=edit.content)]

        if edit.action == "delete_file":
            return [FileOp(action="delete", path=edit.source_file, content="")]

        if edit.action == "replace_file":
            return [FileOp(action="modify", path=edit.source_file, content=edit.content)]

        if edit.action == "replace_range":
            return self._surgical_replace(edit, file_path)

        if edit.action == "insert_range":
            return self._surgical_insert(edit, file_path)

        logger.warning("Unknown edit action: %s (file=%s)", edit.action, edit.source_file)
        self.errors.append(f"Unknown edit action: {edit.action}")
        return []

    def _surgical_replace(
        self, edit: EditOperation, file_path: str,
    ) -> list[FileOp]:
        """Replace range [range_start, range_end] with edit.content."""
        if not os.path.exists(file_path):
            logger.warning(
                "File not found for replace_range: %s — treating as create",
                file_path,
            )
            return [FileOp(action="create", path=edit.source_file, content=edit.content)]

        try:
            with open(file_path) as f:
                existing = f.read()
        except (IOError, OSError) as e:
            logger.error("Failed to read file %s: %s", file_path, e)
            self.errors.append(f"read_error:{file_path}:{e}")
            return []

        patched = self._apply_surgical(existing, edit)

        self._atomic_write(file_path, patched)

        return [FileOp(action="modify", path=edit.source_file, content=patched)]

    def _surgical_insert(
        self, edit: EditOperation, file_path: str,
    ) -> list[FileOp]:
        """Insert edit.content at line range_start."""
        if not os.path.exists(file_path):
            logger.warning(
                "File not found for insert_range: %s — treating as create",
                file_path,
            )
            return [FileOp(action="create", path=edit.source_file, content=edit.content)]

        try:
            with open(file_path) as f:
                existing = f.read()
        except (IOError, OSError) as e:
            logger.error("Failed to read file %s: %s", file_path, e)
            self.errors.append(f"read_error:{file_path}:{e}")
            return []

        patched = self._apply_surgical(existing, edit)

        self._atomic_write(file_path, patched)

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
