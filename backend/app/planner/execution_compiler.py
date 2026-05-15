def compile_plan(plan):
    operations = []

    for action in plan["actions"]:
        operations.append({
            "action": action["type"],
            "path": action["file_path"],
            "diff": action.get("content", "")
        })

    return operations