from app.config.settings import settings
import os

def list_workspace_files(workspace_path: str) -> list[str]:
    files = []

    for root, _, filenames in os.walk(workspace_path):
        for f in filenames:
            full_path = os.path.join(root, f)
            rel_path = os.path.relpath(full_path, workspace_path)
            files.append(rel_path)

    # 🔵 límite aquí
    if len(files) > settings.MAX_WORKSPACE_FILES:
        files = files[:settings.MAX_WORKSPACE_FILES]

    return files