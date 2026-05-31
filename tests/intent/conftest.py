"""Shared fixtures for intent tests."""

from __future__ import annotations

import pytest

from app.catalog.loader import clear_cache


@pytest.fixture(autouse=True)
def reset_catalog_cache():
    """Reset catalog cache before each test to avoid cross-test pollution."""
    clear_cache()
    yield
    clear_cache()
