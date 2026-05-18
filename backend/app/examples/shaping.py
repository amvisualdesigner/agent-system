"""Apply structural bias from ExampleContext into the AST.

Pure function. Deterministic. No side effects.
The shaped AST carries structural hints (composition, layout)
that the renderer maps into the component tree, NOT raw template strings.

ExampleContext is a semantic prior, not a text macro system.
"""


def apply_example_context(ast: dict, ctx) -> dict:
    if not ctx:
        return dict(ast)

    shaped = dict(ast)  # copy — never mutate the original

    shaped["__layout__"] = getattr(ctx, "layouts", [None])[0]

    composition = getattr(ctx, "composition", [])
    if composition:
        shaped["__composition__"] = composition

    return shaped
