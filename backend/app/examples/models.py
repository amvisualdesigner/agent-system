from dataclasses import dataclass, field


@dataclass
class ExampleContext:
    """Structural patterns extracted from canonical example components.

    NOT full source code — just imports, component names, layouts,
    and composition edges. Used by the renderer to align generated
    code with canonical architectural conventions.

    This is a runtime-consumable view of the offline-built catalog.
    """

    imports: list[str] = field(default_factory=list)
    components: list[str] = field(default_factory=list)
    layouts: list[str] = field(default_factory=list)
    composition: list[tuple[str, str]] = field(default_factory=list)

    @classmethod
    def empty(cls) -> "ExampleContext":
        return cls()

    def format_imports_block(self) -> str:
        return "\n".join(self.imports)

    def main_layout(self) -> str:
        return self.layouts[0] if self.layouts else ""
