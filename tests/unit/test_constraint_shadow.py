"""Shadow validation — unit tests for legacy ↔ line-range comparison.

Tests the shadow_compare function that detects behavioral divergence
between the legacy renderer and the constraint pipeline with line-range.
"""

from app.graphir.models import FileOp
from app.graphir.constraint.validation import shadow_compare


def _op(action="create", path="src/KpiRow.tsx", content="export const KpiRow = () => null;\n"):
    return FileOp(action=action, path=path, content=content)


class TestShadowCompare:
    """shadow_compare logs differences without side effects."""

    def test_identical_fileops_no_warnings(self, caplog):
        legacy = [_op(), _op("modify", "src/F.tsx", "data")]
        shadow = [_op(), _op("modify", "src/F.tsx", "data")]
        shadow_compare(legacy, shadow, "run-1")
        assert "SHADOW" not in caplog.text

    def test_different_actions_logged(self, caplog):
        legacy = [_op("create")]
        shadow = [_op("modify")]
        shadow_compare(legacy, shadow, "run-1")
        assert "SHADOW" in caplog.text
        assert "action: create vs modify" in caplog.text

    def test_different_paths_logged(self, caplog):
        legacy = [_op(path="src/A.tsx")]
        shadow = [_op(path="src/B.tsx")]
        shadow_compare(legacy, shadow, "run-1")
        assert "SHADOW" in caplog.text
        assert "path:" in caplog.text

    def test_different_content_logged(self, caplog):
        legacy = [_op(content="old content")]
        shadow = [_op(content="new content")]
        shadow_compare(legacy, shadow, "run-1")
        assert "SHADOW" in caplog.text
        assert "content:" in caplog.text

    def test_mismatched_count_logged(self, caplog):
        legacy = [_op(), _op()]
        shadow = [_op()]
        shadow_compare(legacy, shadow, "run-1")
        assert "SHADOW" in caplog.text
        assert "count mismatch" in caplog.text

    def test_multiple_differences(self, caplog):
        legacy = [_op("create", "src/A.tsx", "a")]
        shadow = [_op("modify", "src/B.tsx", "b")]
        shadow_compare(legacy, shadow, "run-1")
        assert "SHADOW" in caplog.text
        assert "action:" in caplog.text
        assert "path:" in caplog.text
        assert "content:" in caplog.text

    def test_empty_lists_no_issues(self, caplog):
        shadow_compare([], [], "run-1")
        assert "SHADOW" not in caplog.text

    def test_shadow_longer_than_legacy(self, caplog):
        legacy = [_op()]
        shadow = [_op(), _op()]
        shadow_compare(legacy, shadow, "run-1")
        assert "count mismatch" in caplog.text
