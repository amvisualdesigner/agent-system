import ast
from pathlib import Path
from collections import defaultdict

ROOT = "app"  # ajusta a tu repo

# ----------------------------
# 1. scan repo
# ----------------------------
def collect_py_files(root):
    for path in Path(root).rglob("*.py"):
        yield path


# ----------------------------
# 2. extract imports (AST)
# ----------------------------
def extract_imports(file_path):
    try:
        tree = ast.parse(open(file_path).read())
    except Exception:
        return []

    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                imports.append(n.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
    return imports


# ----------------------------
# 3. detect key functions
# ----------------------------
KEYWORDS = [
    "build_component_tree",
    "resolve_slots",
    "emit_tree",
    "resolve_skill",
    "apply_engine",
]


def extract_functions(file_path):
    content = open(file_path).read()
    found = []
    for k in KEYWORDS:
        if f"def {k}" in content:
            found.append(k)
    return found


# ----------------------------
# 4. build system graph
# ----------------------------
def build_graph():
    graph = defaultdict(set)

    for file in collect_py_files(ROOT):
        imports = extract_imports(file)
        funcs = extract_functions(file)

        node = str(file)

        for imp in imports:
            graph[node].add(imp)

        for f in funcs:
            graph["__KEY_FUNCTIONS__"].add(f)

    return graph


# ----------------------------
# 5. render SYSTEM PACK
# ----------------------------
def render(graph):
    out = []

    out.append("SYSTEM PACK")
    out.append("====================\n")

    out.append("PIPELINE (inferred):")
    out.append("""
1. resolve_skill → SkillIR → AST
2. build_component_tree → tree construction
3. resolve_slots → validation + binding
4. emit_tree → FileOps
5. apply_engine → execution
""")

    out.append("\nKEY FUNCTIONS:")
    for f in sorted(graph["__KEY_FUNCTIONS__"]):
        out.append(f"- {f}")

    out.append("\nIMPORT GRAPH (simplified):")
    for k, v in list(graph.items())[:30]:
        if k != "__KEY_FUNCTIONS__":
            out.append(f"{k}")
            for dep in list(v)[:5]:
                out.append(f"  -> {dep}")

    return "\n".join(out)


if __name__ == "__main__":
    g = build_graph()
    pack = render(g)

    with open("system_pack.txt", "w") as f:
        f.write(pack)

    print("SYSTEM PACK generated")