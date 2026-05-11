from app.utils.workspace import list_workspace_files

def build_context(base_dir: str, run_id:str):
    workspace = f"{base_dir}/workspace"
    artifacts = f"{base_dir}/artifacts"

    return {
        "run_id": run_id,
        "workspace": workspace,
        "artifacts": artifacts,
        "workspace_files": list_workspace_files(workspace)
    }