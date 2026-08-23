"""No second answer to "what is this type's bare name".

An architecture check rather than a behaviour one: it fails when a new
qualifier-splitting expression appears in the modules that resolve type names,
anywhere outside :mod:`repowise.core.ingestion.type_names`. The
characterisation tables catch a *wrong* answer from an extractor that already
exists; only this catches a second implementation appearing.

These are expressions rather than named functions, so the match is on shape: a
call to ``split`` / ``rsplit`` / ``partition`` / ``rpartition`` whose first
argument is a separator a qualifier is spelled with, or a bracket that opens a
type-argument group. That shape is also how a file extension or a URL is taken
apart, which is why the scan is confined to the modules that resolve types —
over the whole tree it would match hundreds of legitimate uses and mean nothing.

Ceiling: string shape only. A copy that reaches for ``re``, splits on a
separator held in a variable, or navigates the syntax tree instead of the text
stays invisible — the per-language head extractors in ``parser_helpers.py`` are
node walks and are deliberately out of reach. It catches the shape that
actually recurred here.

``_KNOWN`` holds the sites that stay, each with the reason it is exempt. It
records a count rather than a bare path so a new copy cannot hide in a file
that is already exempt, and a second test holds the counts exact so a converted
site cannot leave a stale exemption behind.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import repowise.core.ingestion.type_names as type_names

# Splitting on one of these is how a qualifier is taken off a type name.
_SEPARATORS = frozenset({".", "::", "\\"})

# ...and these open a type-argument group or a constructor call, so cutting at
# one is how the other half of the question was answered.
_OPENERS = frozenset({"<", "[", "("})

_SPLIT_METHODS = frozenset({"split", "rsplit", "partition", "rpartition"})

_PACKAGES = pathlib.Path(__file__).resolve().parents[2] / "packages"
_INGESTION = _PACKAGES / "core" / "src" / "repowise" / "core" / "ingestion"

# Where the question gets asked. Everything here reduces a type name written in
# source; nothing else in the tree is scanned.
_SCOPE = (
    _INGESTION / "extractors" / "heritage",
    _INGESTION / "dynamic_hints",
    _INGESTION / "framework_edges",
    _INGESTION / "languages",
    _INGESTION / "parser_helpers.py",
    _INGESTION / "type_ref_resolution.py",
    _INGESTION / "call_resolver.py",
    _INGESTION / "heritage_resolver.py",
    # Holds the shared symbol-ID splitter both resolvers used to keep a copy
    # of; in scope so moving it did not move it out of reach.
    _INGESTION / "models.py",
)

_PREFIX = "packages/core/src/repowise/core/ingestion/"

# Sites that split on a separator but are not answering this question, or that
# are and cannot be converted yet, with the reason each is exempt and how many
# matches it is allowed. Shrink, never grow.
_KNOWN: dict[str, int] = {
    # Splits our own `path::Class::method` symbol IDs, which we mint. The
    # separator is ours rather than the language's, so the shared helper would
    # be answering about a type where these ask about an ID. `models.py` holds
    # the one both resolvers used to duplicate. Six of `call_resolver.py`'s
    # eight are symbol IDs; the other two are both an import's module path,
    # which is a module name and not a type: one takes its tail and one takes
    # its head, to ask whether the package it names is one of ours.
    _PREFIX + "call_resolver.py": 8,
    _PREFIX + "models.py": 1,
    # Reads the head to decide whether taking a bare name is safe at all: a
    # qualifier that is a type rather than a package must not be discarded.
    # It guards the shared helper and then calls it, so it asks the opposite
    # question.
    _PREFIX + "languages/receiver_types.py": 1,
    # Normalises the method name out of `#selector(Type.method)`. The last
    # segment here is a member, not a type.
    _PREFIX + "dynamic_hints/swift.py": 1,
    # Splits an import statement's package path, not a type reference.
    _PREFIX + "languages/jvm_same_package.py": 1,
    # Takes the head of a URLconf's `views.detail` to reach the Python module
    # declaring the view. The trailing segment is the view function and the
    # head is a module path, so neither end is a type.
    _PREFIX + "framework_edges/django.py": 1,
    # Split a route handler's path to reach a function name, and keep the
    # prefix to resolve the package it came from. Neither wants a type.
    _PREFIX + "framework_edges/rust.py": 1,
    _PREFIX + "framework_edges/go.py": 1,
    # Takes the qualifier HEAD of `OrderHandlers.GetOrder` to reach the
    # declaring type. It does want a type, but the shared helper returns the
    # trailing segment, which is the member — the opposite end, the same reason
    # `receiver_types.py` is exempt.
    _PREFIX + "framework_edges/aspnet.py": 1,
    # Real copies, each held by a consumer no gate here measures: converting
    # them moves framework edges, C++ symbol parent names and Go
    # `method_implements` edges respectively. Removable once those are covered.
    _PREFIX + "framework_edges/jakarta.py": 2,
    _PREFIX + "framework_edges/micronaut.py": 2,
    _PREFIX + "framework_edges/spring.py": 4,
    _PREFIX + "framework_edges/laravel.py": 1,
    _PREFIX + "parser_helpers.py": 1,
    _PREFIX + "languages/go_interface_satisfaction.py": 1,
}

# Builtin-type lists are language identity data and belong on the spec beside
# `builtin_calls` and `builtin_parents`, where every language can be compared
# against every other. Matches on the name, so a set spelled differently is out
# of reach.
_SPECS = _INGESTION / "languages" / "specs"


def _splits_on_a_qualifier(node: ast.AST) -> str | None:
    """Describe *node* if it is a call that cuts a name at a qualifier."""
    if not isinstance(node, ast.Call):
        return None
    if not isinstance(node.func, ast.Attribute) or node.func.attr not in _SPLIT_METHODS:
        return None
    if not node.args:
        return None
    first = node.args[0]
    if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
        return None
    if first.value not in _SEPARATORS | _OPENERS:
        return None
    return f"{node.func.attr}({first.value!r})"


def _python_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for entry in _SCOPE:
        files.extend(sorted(entry.rglob("*.py")) if entry.is_dir() else [entry])
    return files


def _offenders() -> dict[str, list[str]]:
    """Map of repo-relative path -> the splitting expressions it holds."""
    found: dict[str, list[str]] = {}
    for path in _python_files():
        rel = path.relative_to(_PACKAGES.parent).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        hits = [
            f"line {node.lineno}: {described}"
            for node in ast.walk(tree)
            if (described := _splits_on_a_qualifier(node))
        ]
        if hits:
            found[rel] = hits
    return found


def test_no_new_bare_type_name_expressions() -> None:
    offenders = {p: hits for p, hits in _offenders().items() if len(hits) > _KNOWN.get(p, 0)}
    assert not offenders, (
        "New qualifier-splitting expression(s) outside"
        " repowise.core.ingestion.type_names:\n"
        + "\n".join(f"  {p}\n    " + "\n    ".join(hits) for p, hits in sorted(offenders.items()))
        + "\n\nCall bare_type_name / strip_type_arguments / strip_call_arguments from"
        " repowise.core.ingestion.type_names instead. If a site genuinely asks a"
        " different question, say why in the code and add it to _KNOWN in this file."
    )


def test_known_copies_still_exist() -> None:
    """Every allowlist count must be exact, so the list cannot rot."""
    found = _offenders()
    stale = {
        path: (expected, len(found.get(path, [])))
        for path, expected in _KNOWN.items()
        if len(found.get(path, [])) != expected
    }
    assert not stale, (
        "These no longer hold the number of matches _KNOWN allows"
        " (path: allowed -> actual):\n"
        + "\n".join(f"  {p}: {exp} -> {act}" for p, (exp, act) in sorted(stale.items()))
    )


def test_the_shape_is_what_gets_caught() -> None:
    """A copy is caught; taking a path or an extension apart is not."""

    def found(source: str) -> list[str]:
        return [
            described
            for node in ast.walk(ast.parse(source))
            if (described := _splits_on_a_qualifier(node))
        ]

    assert found('bare = raw.rsplit("::", 1)[-1]') == ["rsplit('::')"]
    assert found('head = raw.split("<")[0]') == ["split('<')"]
    assert found('ext = name.rsplit("/", 1)[-1]') == []
    assert found("part = raw.split(sep)[0]") == []


def test_builtin_type_lists_live_on_the_language_specs() -> None:
    strays: dict[str, list[str]] = {}
    for path in sorted(_PACKAGES.rglob("*.py")):
        if _SPECS in path.parents:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # not ours to police
            continue
        names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                names.extend(
                    t.id
                    for t in node.targets
                    if isinstance(t, ast.Name) and t.id.endswith("BUILTIN_TYPES")
                )
            elif (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id.endswith("BUILTIN_TYPES")
            ):
                names.append(node.target.id)
        if names:
            strays[path.relative_to(_PACKAGES.parent).as_posix()] = sorted(names)
    assert not strays, (
        "Builtin-type list(s) outside ingestion/languages/specs/:\n"
        + "\n".join(f"  {p}: {', '.join(n)}" for p, n in sorted(strays.items()))
        + "\n\nPut them on that language's LanguageSpec.builtin_types and read them"
        " back with get_builtin_types()."
    )


@pytest.mark.parametrize(
    "name", ["bare_type_name", "strip_type_arguments", "strip_call_arguments"]
)
def test_shared_module_exports_the_replacements(name: str) -> None:
    assert callable(getattr(type_names, name))
