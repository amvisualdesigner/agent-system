import requests
import json
import os

from app.contracts.operations import Action

URL = "http://0.0.0.0:8000/agent/run"

tests = [
    {
        "name": "Archivo normal",
        "task": "Create src/test_artifact.txt with HELLO_ARTIFACT"
    },
    {
        "name": "Path bloqueado",
        "task": "Create .git/evil.txt with BAD_CONTENT"
    },
    {
        "name": "Archivo muy grande",
        "task": "Create src/big_file.txt with " + "A" * 300_000
    },
    {
        "name": "Operación no permitida",
        "task": "Rename src/test_artifact.txt to src/renamed.txt"
    }
]

for test in tests:
    print(f"\n=== Test: {test['name']} ===")
    payload = {
        "task": test["task"],
        "scope": [],
        "constraints": [],
        "repo_context": []
    }

    response = requests.post(URL, json=payload)
    result = response.json()

    run_id = result.get("run_id")
    workspace = result.get("workspace")
    artifacts = result.get("artifacts")
    git_diff = result.get("git_diff")
    llm_ops = result.get("llm_result", {}).get("operations", [])

    print(f"Run ID: {run_id}")
    print(f"Workspace: {workspace}")
    print(f"Artifacts: {artifacts}")

    # listar archivos creados
    if os.path.exists(workspace):
        print("Archivos en workspace:")
        for root, _, files in os.walk(workspace):
            for f in files:
                rel_path = os.path.relpath(os.path.join(root, f), workspace)
                print(rel_path)

    # mostrar contenido de archivos que creó el LLM
    for op in llm_ops:
        if Action(op["action"]) in {Action.create, Action.modify}:
            path = os.path.join(workspace, op["path"])
            if os.path.exists(path):
                with open(path, "r") as f:
                    content = f.read()
                print(f"Contenido de {op['path']}:\n{content}")

    print("Git diff staged:")
    print(git_diff)

    print("Operaciones LLM:")
    print(json.dumps(llm_ops, indent=2))