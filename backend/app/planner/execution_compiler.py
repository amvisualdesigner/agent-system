def compile_plan(plan):
    operations = []

    for action in plan["actions"]:
        op = {
            "action": action["type"],
            "target": action.get("target", "file"),
            "path": action.get("file_path", ""),
            "name": action.get("name", ""),
            "params": action.get("params", {}),
            "diff": action.get("content", ""),
            "intent": action.get("intent", ""),
        }
        operations.append(op)

    return operations
