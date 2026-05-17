from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict


@dataclass
class FileOp:
    action: str    # "create" | "modify"
    path: str
    content: str

    def to_dict(self) -> dict:
        return asdict(self)


class Renderer(ABC):
    @abstractmethod
    def render(self, ast: dict, renderer_config: dict) -> list[FileOp]:
        ...
