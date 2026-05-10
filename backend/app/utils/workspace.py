import os

def list_workspace_files(workspace_path: str) -> list[str]:
    files = []

    for root, _, filenames in os.walk(workspace_path):
        for f in filenames:
            full_path = os.path.join(root, f)
            rel_path = os.path.relpath(full_path, workspace_path)
            files.append(rel_path)

    # Limite
    if len(files) > 200:
        files = files[:200]

    return files