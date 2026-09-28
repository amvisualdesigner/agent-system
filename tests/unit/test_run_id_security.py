"""S1-A.7 lock — backend security contract for run_id + path containment.

These tests mirror tests/orchestrator/test_run_id_security.py: backend and
orchestrator keep LOCAL implementations (no shared runtime dependency, by
design) but must enforce the SAME security invariants documented in
docs/architecture/SESSION_INFRASTRUCTURE_DEBT.md.
"""

import os

import pytest

from app.utils.run_id import validate_run_id
from app.utils.path_guard import guard_within

VALID_UUID = "9f0c7f1b-8b6c-4b4e-9e1a-4b6c4f6c7d5e"


class TestValidateRunId:
    def test_valid_uuid(self):
        assert validate_run_id(VALID_UUID) == VALID_UUID

    def test_invalid_uuid_not_a_uuid(self):
        with pytest.raises(ValueError, match="Invalid run_id"):
            validate_run_id("not-a-uuid")

    def test_invalid_uuid_wrong_format(self):
        with pytest.raises(ValueError, match="Invalid run_id"):
            validate_run_id("9f0c7f1b8b6c4b4e9e1a4b6c4f6c7d5e")

    def test_invalid_uuid_uppercase(self):
        with pytest.raises(ValueError, match="Invalid run_id"):
            validate_run_id(VALID_UUID.upper())

    def test_invalid_non_string(self):
        for bad in (None, 123, b"9f0c7f1b-8b6c-4b4e-9e1a-4b6c4f6c7d5e"):
            with pytest.raises(ValueError, match="Invalid run_id"):
                validate_run_id(bad)

    def test_invalid_empty(self):
        with pytest.raises(ValueError, match="Invalid run_id"):
            validate_run_id("")


class TestGuardWithin:
    def test_normal_path_inside_root(self, tmp_path):
        root = str(tmp_path)
        inside = os.path.join(root, "run-123")
        assert guard_within(inside, root) == os.path.realpath(inside)

    def test_path_equals_root(self, tmp_path):
        root = str(tmp_path)
        assert guard_within(root, root) == os.path.realpath(root)

    def test_absolute_external_path(self, tmp_path):
        root = str(tmp_path)
        with pytest.raises(PermissionError, match="Path escape blocked"):
            guard_within("/tmp/external", root)

    def test_parent_relative_traversal(self, tmp_path):
        root = str(tmp_path)
        with pytest.raises(PermissionError, match="Path escape blocked"):
            guard_within(os.path.join(root, "sub", "..", "..", "etc"), root)

    def test_equivalent_traversal_outside_root(self, tmp_path):
        root = str(tmp_path)
        with pytest.raises(PermissionError, match="Path escape blocked"):
            guard_within(os.path.join(root, "..", "outside"), root)

    def test_path_outside_root(self, tmp_path):
        root = str(tmp_path)
        sibling_root = tmp_path.parent / "sibling"
        sibling = os.path.join(str(sibling_root), "run-1")
        with pytest.raises(PermissionError, match="Path escape blocked"):
            guard_within(sibling, root)