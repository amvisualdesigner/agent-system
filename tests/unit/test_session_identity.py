"""S1-B lock — backend security contract for session_id.

Mirrors backend/app/utils/run_id.py and the orchestrator's local run_id
contract (docs/architecture/SESSION_INFRASTRUCTURE_DEBT.md): LOCAL, duplicated
implementations by design, but the SAME validation invariants and error
message style as validate_run_id.
"""

import pytest

from app.utils.session_id import validate_session_id

VALID_SESSION_ID = "3f6a0f9c-1b8e-4d2a-9c5e-6a7b8c9d0e1f"


class TestValidateSessionId:
    def test_valid_uuid(self):
        assert validate_session_id(VALID_SESSION_ID) == VALID_SESSION_ID

    def test_invalid_not_a_uuid(self):
        with pytest.raises(ValueError, match="Invalid session_id"):
            validate_session_id("not-a-uuid")

    def test_invalid_wrong_format(self):
        with pytest.raises(ValueError, match="Invalid session_id"):
            validate_session_id("3f6a0f9c1b8e4d2a9c5e6a7b8c9d0e1f")

    def test_invalid_uppercase(self):
        with pytest.raises(ValueError, match="Invalid session_id"):
            validate_session_id(VALID_SESSION_ID.upper())

    def test_invalid_non_string(self):
        for bad in (None, 123, b"3f6a0f9c-1b8e-4d2a-9c5e-6a7b8c9d0e1f"):
            with pytest.raises(ValueError, match="Invalid session_id"):
                validate_session_id(bad)

    def test_invalid_empty(self):
        with pytest.raises(ValueError, match="Invalid session_id"):
            validate_session_id("")

    def test_contract_consistent_with_run_id(self):
        """Same validation shape as run_id: valid UUID v4 passes, malformed rejected."""
        from app.utils.run_id import validate_run_id

        # A valid session_id is also a valid run_id (same UUID space).
        assert validate_run_id(VALID_SESSION_ID) == VALID_SESSION_ID
        assert validate_session_id(VALID_SESSION_ID) == VALID_SESSION_ID

    def test_error_message_style_matches_run_id(self):
        from app.utils.run_id import validate_run_id

        run_err = ""
        session_err = ""
        try:
            validate_run_id("bogus")
        except ValueError as e:
            run_err = str(e)
        try:
            validate_session_id("bogus")
        except ValueError as e:
            session_err = str(e)

        assert "Invalid run_id:" in run_err
        assert "Invalid session_id:" in session_err
        assert "UUID v4" in run_err
        assert "UUID v4" in session_err