def compile_plan(plan):
    operations = []

    for step in plan["steps"]:
        operations.append({
            "type": step["action"],
            "path": step["path"],
            "diff": step.get("proposed_content", "")
        })

    return operations