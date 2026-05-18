"""Apply structural bias from ExampleContext into the AST.

Pure function. Deterministic. No side effects.
The shaped AST carries __layout__ and __example_imports__ hints
that the renderer maps into template placeholders.
"""


def apply_example_context(ast: dict, ctx) -> dict:
    if not ctx:
        return ast

    ast = dict(ast)

    ast["__layout__"] = getattr(ctx, "layouts", [None])[0]
    ast["__example_imports__"] = getattr(ctx, "imports", [])

    return ast
