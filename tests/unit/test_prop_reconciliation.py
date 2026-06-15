"""Unit tests for _reconcile_destructure, especially return_map."""
import pytest

from app.graphir.backends.react_backend import _reconcile_destructure


# Existing coverage: signature w/ prop_names renames body + destructure
# New coverage: return_map


class TestReconcileDestructure:
    """_reconcile_destructure returns correctly reconciled output."""

    def test_basic_rename(self):
        sig = {"prop_names": ["data"]}
        hardcoded = "{ metrics }"
        body = ["const x = metrics.map(...)", "return <div>{metrics}</div>"]
        new_dest, new_body = _reconcile_destructure(sig, hardcoded, body)
        assert "data" in new_dest
        assert "metrics" not in new_dest
        assert all("data" in line for line in new_body)

    def test_no_prop_names(self):
        sig = {"prop_names": []}
        hardcoded = "{ data }"
        body = ["const x = data"]
        new_dest, new_body = _reconcile_destructure(sig, hardcoded, body)
        assert new_dest == hardcoded
        assert new_body == body

    def test_no_signature(self):
        new_dest, new_body = _reconcile_destructure(None, "{ data }", ["data"])
        assert new_dest == "{ data }"
        assert new_body == ["data"]

    def test_return_map_provides_mapping(self):
        sig = {"prop_names": ["data"]}
        hardcoded = "{ metrics }"
        body = ["const x = metrics"]
        new_dest, new_body, rename_map = _reconcile_destructure(
            sig, hardcoded, body, return_map=True,
        )
        assert rename_map == {"metrics": "data"}

    def test_return_map_empty_when_no_mismatch(self):
        sig = {"prop_names": ["data"]}
        hardcoded = "{ data }"
        body = ["const x = data"]
        new_dest, new_body, rename_map = _reconcile_destructure(
            sig, hardcoded, body, return_map=True,
        )
        assert rename_map == {}

    def test_return_map_partial_mapping(self):
        sig = {"prop_names": ["data", "title"]}
        hardcoded = "{ metrics, title }"
        body = ["const x = metrics"]
        new_dest, new_body, rename_map = _reconcile_destructure(
            sig, hardcoded, body, return_map=True,
        )
        assert rename_map == {"metrics": "data"}
        assert "data" in new_dest

    def test_return_map_no_prop_names(self):
        sig = {"prop_names": []}
        new_dest, new_body, rename_map = _reconcile_destructure(
            sig, "{ data }", ["data"], return_map=True,
        )
        assert rename_map == {}

    def test_return_map_no_signature(self):
        new_dest, new_body, rename_map = _reconcile_destructure(
            None, "{ data }", ["data"], return_map=True,
        )
        assert rename_map == {}
