"""DatasourceContract — framework-agnostic model for datasource shape.

SSOT for the datasource layer. Defines WHAT data is available, not HOW it's
fetched. Loaded from workspace/backend/data/datasource.json per repo.

Framework lowering (React types, Vue composables, etc.) happens in the
renderer, never in this model.

Usage:
    contract = DatasourceContract.load(workspace_root)
    contract.validate_slices(slices)  # verify slices match real fields
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class DatasourceType:
    """A named type in the datasource schema (framework-agnostic).

    Examples: KpiItem {label: string, value: number | null}
              Point {label: string, value: number}
    """
    name: str
    props: dict[str, str] = field(default_factory=dict)
    description: str = ""
    index_signature: str | None = None

    def to_typescript(self) -> str:
        """Generate TypeScript interface definition."""
        lines = [f"export interface {self.name} {{"]
        for prop_name, prop_type in self.props.items():
            lines.append(f"  {prop_name}: {prop_type};")
        if self.index_signature:
            lines.append(f"  {self.index_signature};")
        lines.append("}")
        return "\n".join(lines)


@dataclass
class DatasourceField:
    """A top-level field in the datasource return shape.

    The dot-separated name corresponds to the selector used in slices.
    e.g. Field(name="chartData.timeseries") is accessed via _pageData.chartData.timeseries.
    """
    name: str
    type_ref: str
    description: str = ""

    @property
    def path_parts(self) -> list[str]:
        return self.name.split(".")


@dataclass
class DatasourceContract:
    """Framework-agnostic contract for the datasource layer.

    Describes WHAT data is available from the datasource hook.
    Loaded from workspace configuration. This is the SSOT for all
    datasource-related generation.

    Fields:
        types: Named type definitions (e.g. KpiItem, Point).
        fields: Top-level fields in the return shape (e.g. kpiData).
        mock_data: Default mock values for each field.
        hook_name: Name of the datasource hook function.
        hook_path: Module import path for the hook.
    """
    version: int = 1
    types: list[DatasourceType] = field(default_factory=list)
    fields: list[DatasourceField] = field(default_factory=list)
    mock_data: dict[str, Any] = field(default_factory=dict)
    hook_name: str = "useDashboardData"
    hook_path: str = "@/hooks/useDashboardData"

    _field_map: dict[str, DatasourceField] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self):
        self._field_map = {f.name: f for f in self.fields}

    def get_field(self, name: str) -> DatasourceField | None:
        return self._field_map.get(name)

    def validate_slices(self, slices: list[Any]) -> list[str]:
        """Verify every slice selector references an existing field.

        Args:
            slices: List of slice dicts with 'selector' keys (like data_access.json).

        Returns:
            List of warning messages. Empty = all slices valid.
        """
        warnings: list[str] = []
        for s in slices:
            selector = s.get("selector") if isinstance(s, dict) else getattr(s, "selector", None)
            if not selector:
                continue
            if selector not in self._field_map:
                warnings.append(
                    f"Slice selector '{selector}' not found in DatasourceContract fields. "
                    f"Available: {sorted(self._field_map.keys())}"
                )
        return warnings

    def mock_value_for(self, field_name: str) -> Any:
        """Get mock data for a field, traversing dot-separated paths."""
        if not self.mock_data:
            return None
        parts = field_name.split(".")
        current = self.mock_data
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
        return current

    @classmethod
    def load(cls, workspace_root: str) -> DatasourceContract | None:
        """Load contract from workspace/backend/data/datasource.json.

        Returns None if file doesn't exist or is invalid.
        """
        path = os.path.join(workspace_root, "backend", "data", "datasource.json")
        if not os.path.exists(path):
            logger.debug("No datasource contract at %s", path)
            return None
        try:
            with open(path) as f:
                data = json.load(f)
            return cls.from_dict(data)
        except (json.JSONDecodeError, OSError, ValueError) as e:
            logger.warning("Failed to load datasource contract from %s: %s", path, e)
            return None

    @classmethod
    def from_dict(cls, data: dict) -> DatasourceContract:
        """Parse dict (from JSON) into DatasourceContract."""
        types_raw = data.get("types", {})
        types = [
            DatasourceType(
                name=tname,
                props=tdata.get("props", {}),
                description=tdata.get("description", ""),
                index_signature=tdata.get("indexSignature"),
            )
            for tname, tdata in types_raw.items()
        ]

        fields = cls._parse_fields(data.get("returnType", {}), prefix="")

        return cls(
            version=data.get("version", 1),
            types=types,
            fields=fields,
            mock_data=data.get("mockData", {}),
            hook_name=data.get("hook", {}).get("name", "useDashboardData"),
            hook_path=data.get("hook", {}).get("import", "@/hooks/useDashboardData"),
        )

    @classmethod
    def _parse_fields(cls, return_type: dict, prefix: str) -> list[DatasourceField]:
        """Recursively parse returnType into flat field list.

        Nested objects become dot-separated fields:
        {kpiData: "KpiItem[]", chartData: {timeseries: "Point[]"}}
        →
        [Field("kpiData", "KpiItem[]"), Field("chartData.timeseries", "Point[]")]
        """
        fields: list[DatasourceField] = []
        for key, value in return_type.items():
            full_name = f"{prefix}.{key}" if prefix else key
            if isinstance(value, str):
                fields.append(DatasourceField(name=full_name, type_ref=value))
            elif isinstance(value, dict):
                fields.extend(cls._parse_fields(value, prefix=full_name))
        return fields
