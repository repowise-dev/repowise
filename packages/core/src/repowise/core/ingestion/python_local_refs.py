"""Same-file symbol-reference extraction for Python.

The dead-code analyzer's unused-export pass treats a public symbol as live
when something imports it (cross-file ``imports`` edge naming it) or a
``calls`` / ``type_use`` / heritage edge lands on it. Neither signal fires
for a top-level function or class that is only *referenced within its own
module* in a non-call position:

* passed as a first-class callable argument to a higher-order helper
  (``_score_dimension(results, maintainability_weight, ...)`` — the
  function is never invoked by name, so no ``calls`` edge exists);
* used as a default value, named in a decorator expression, or stored as a
  dict/list value;
* used purely as a **type annotation** — the canonical case being a
  Pydantic ``BaseModel`` used only as a FastAPI request-body parameter
  type, which FastAPI instantiates at runtime (no constructor call lands in
  user code).

Python can only reach a name from another module through an ``import``, so
a *cross-file* reference almost always carries the name on an ``imports``
edge and is handled — the one residual cross-file gap is a namespace import
(``import mymod``) used only as a qualified annotation (``x: mymod.Thing``),
where the edge names ``mymod`` rather than ``Thing``; that is out of scope
here. The dominant blind spot, and the one this module closes, is
*same-file* usage. This module computes, per Python file, the set of
top-level symbol names that are referenced elsewhere in the same file,
which the analyzer stamps on the file node (``local_refs``) and consults to
rescue them.

A symbol's *own body* does not count as a use — otherwise a function that
is dead from the rest of the codebase would rescue itself through its own
recursive call. Mutual recursion between two otherwise-dead top-level
symbols is a known residual gap (each rescues the other); resolving it
would need strongly-connected-component analysis and is deliberately not
done — under-reporting a dead recursive cluster is the safe error for a
dead-code tool, where the failure to avoid is a confident *false positive*.

We use the stdlib :mod:`ast` rather than enumerating tree-sitter capture
patterns: the host language is Python, ``ast`` already backs the
Django/pytest dynamic hints, and it distinguishes ``Load`` from ``Store``
context and walks annotation subtrees — including quoted forward-reference
annotations — reliably.
"""

from __future__ import annotations

import ast

__all__ = ["extract_python_local_refs"]


def extract_python_local_refs(
    source: str,
    defined_names: frozenset[str] | set[str],
) -> frozenset[str]:
    """Return the subset of *defined_names* referenced elsewhere in *source*.

    A name counts as referenced when it appears in any ``Load`` position
    (call argument, default value, decorator expression, collection value,
    assignment RHS, type annotation, …) anywhere in the module — including
    inside a quoted/postponed forward-reference annotation such as
    ``Optional["Foo"]``.

    *defined_names* should be the file's top-level symbol names (methods
    excluded — they are reached through their class, never imported by
    name). Restricting to defined names keeps the result a small rescue set
    and bounds the walk's bookkeeping.

    Returns an empty set when there are no candidate names or the source is
    not parseable as Python 3 (Python 2 source, syntax tree-sitter
    tolerated but ``ast`` rejects, etc.) — a missed rescue simply falls
    back to the analyzer's prior behaviour, never a false negative on a
    genuinely-dead symbol elsewhere.
    """
    if not defined_names:
        return frozenset()
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return frozenset()

    referenced: set[str] = set()
    # Walk each top-level statement separately so a definition can be told
    # apart from its own body: the name a top-level ``def``/``class`` binds
    # (``owner``) is excluded while walking that statement's subtree, so a
    # recursive self-call is not mistaken for an external use.
    for stmt in tree.body:
        owner = _bound_name(stmt)
        _collect_refs(stmt, defined_names, owner, referenced)

    return frozenset(referenced)


def _bound_name(stmt: ast.stmt) -> str | None:
    """Return the symbol name a top-level def/class binds, else ``None``."""
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return stmt.name
    return None


def _collect_refs(
    node: ast.AST,
    defined_names: frozenset[str] | set[str],
    owner: str | None,
    out: set[str],
) -> None:
    """Record defined-name references inside *node*, excluding ``owner`` itself."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            # ``Store``/``Del`` Names are (re)definitions, not uses; only a
            # ``Load`` of a defined name (other than the enclosing symbol's
            # own name) is evidence the symbol is consumed elsewhere.
            if (
                isinstance(sub.ctx, ast.Load)
                and sub.id != owner
                and sub.id in defined_names
            ):
                out.add(sub.id)
            continue

        # Quoted / postponed forward-reference annotations are string
        # constants in the AST; their inner names are invisible to the
        # ``Name`` walk above. Re-parse each annotation's string literals
        # and collect any defined name they reference.
        annotation = _annotation_of(sub)
        if annotation is not None:
            _collect_forward_refs(annotation, defined_names, owner, out)


def _annotation_of(node: ast.AST) -> ast.expr | None:
    """Return the annotation expression carried by *node*, if any."""
    if isinstance(node, (ast.AnnAssign, ast.arg)):
        return node.annotation
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return node.returns
    return None


def _collect_forward_refs(
    annotation: ast.expr,
    defined_names: frozenset[str] | set[str],
    owner: str | None,
    out: set[str],
) -> None:
    """Parse quoted forward refs inside *annotation* and record defined names.

    Handles a quoted forward ref anywhere in the annotation —
    ``"Foo"``, ``Optional["Foo"]``, ``dict[str, "Bar"]`` — by re-parsing
    each string-constant leaf as a type expression. ``owner`` is excluded so
    a self-referential annotation (``class Node: next: "Node"``) does not
    rescue an otherwise-dead class.
    """
    for sub in ast.walk(annotation):
        if not (isinstance(sub, ast.Constant) and isinstance(sub.value, str)):
            continue
        try:
            inner = ast.parse(sub.value, mode="eval")
        except (SyntaxError, ValueError):
            continue
        for ref in ast.walk(inner):
            if isinstance(ref, ast.Name) and ref.id != owner and ref.id in defined_names:
                out.add(ref.id)
