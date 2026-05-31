"""Test catalog loader — runtime loading of capability_catalog.json."""

from __future__ import annotations

import json
import tempfile
import os

from app.catalog.loader import load_catalog, get_contract_catalog, list_contract_ids, clear_cache


def test_load_catalog_default():
    clear_cache()
    catalog = load_catalog()
    assert catalog["version"] == 1
    assert "dashboard.sales_overview" in catalog["contracts"]
    assert "analytics.table" in catalog["contracts"]


def test_get_contract_catalog():
    clear_cache()
    entry = get_contract_catalog("dashboard.sales_overview")
    assert entry is not None
    assert entry["label"] == "Sales dashboard"
    capabilities = entry["capabilities"]
    assert len(capabilities) >= 3
    cap_ids = {c["id"] for c in capabilities}
    assert "presentation.kpi_row" in cap_ids
    assert "presentation.timeseries" in cap_ids
    assert "layout.page" in cap_ids


def test_get_contract_catalog_not_found():
    clear_cache()
    assert get_contract_catalog("nonexistent") is None


def test_list_contract_ids():
    clear_cache()
    ids = list_contract_ids()
    assert "dashboard.sales_overview" in ids
    assert len(ids) >= 10


def test_load_catalog_custom_path():
    clear_cache()
    data = {
        "version": 1,
        "contracts": {
            "test.contract": {
                "label": "Test",
                "capabilities": [],
                "utterance_examples": [],
            }
        },
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        tmp_path = f.name

    try:
        catalog = load_catalog(tmp_path)
        assert "test.contract" in catalog["contracts"]
    finally:
        os.unlink(tmp_path)
        clear_cache()


def test_load_catalog_missing_file():
    clear_cache()
    catalog = load_catalog("/nonexistent/path/catalog.json")
    assert catalog["version"] == 0
    assert catalog["contracts"] == {}
    clear_cache()
