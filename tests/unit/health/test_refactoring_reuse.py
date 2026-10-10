"""Extract Helper names an existing function when one clone site already is it.

A clone group whose one occurrence is a whole function ``F`` is planned as
"call ``F``" at the other sites (or "delete this copy" when a site is ``F``
again), never as a second copy. Anything that would make the call wrong keeps
the new-helper plan.
"""

from __future__ import annotations

import networkx as nx

from repowise.core.analysis.health.duplication import ClonePair
from repowise.core.analysis.health.refactoring import RefactoringContext, detect_refactorings
from repowise.core.analysis.health.refactoring.recipe import build_recipe
from repowise.core.analysis.health.refactoring.render import list_plan
from repowise.core.analysis.health.refactoring.reuse import find_reuse
from repowise.core.analysis.signature_effect import parameter_names

_BODY = [
    "    parts = text.split(sep)",
    "    cleaned = [p.strip() for p in parts]",
    "    joined = sep.join(cleaned)",
    "    if not joined:",
    '        return ""',
    "    return joined.lower()",
]


def _util(name: str = "normalize", params: str = "text, sep") -> list[str]:
    return [
        "import json",
        "",
        "",
        f"def {name}({params}):",
        '    """Normalise a separated string."""',
        *_BODY,
    ]


def _host(tail: list[str] | None = None, body: list[str] | None = None) -> list[str]:
    return [
        "import os",
        "",
        "",
        "def run(text, sep):",
        '    print("start")',
        *(body or _BODY),
        *(tail or []),
    ]


def _graph(files: dict[str, list[tuple[str, int, int, str]]]) -> nx.DiGraph:
    graph = nx.DiGraph()
    for path, symbols in files.items():
        graph.add_node(path, node_type="file")
        for name, start, end, signature in symbols:
            sid = f"{path}::{name}"
            graph.add_node(
                sid,
                node_type="symbol",
                kind="function",
                name=name,
                file_path=path,
                start_line=start,
                end_line=end,
                signature=signature,
                visibility="private" if name.startswith("_") else "public",
                parent_name=None,
                is_async=False,
            )
            graph.add_edge(path, sid, edge_type="defines")
    return graph


def _plans(sources: dict[str, list[str]], graph: nx.DiGraph, pair: ClonePair) -> list:
    anchor = min(sources)
    ctx = RefactoringContext(
        file_path=anchor,
        language="python",
        nloc=200,
        clones=[pair],
        graph=graph,
        source_lines=sources[anchor],
        read_lines=sources.get,
    )
    return [s for s in detect_refactorings(ctx) if s.refactoring_type == "extract_helper"]


def _pair(a: tuple[str, int, int], b: tuple[str, int, int]) -> ClonePair:
    return ClonePair(a[0], b[0], a[1], a[2], b[1], b[2], token_count=60)


def _setup(util: list[str], host: list[str], name: str = "normalize", params: str = "text, sep"):
    sources = {"pkg/b.py": host, "pkg/util.py": util}
    graph = _graph(
        {
            "pkg/util.py": [(name, 4, len(util), f"def {name}({params})")],
            "pkg/b.py": [("run", 4, len(host), "def run(text, sep)")],
        }
    )
    pair = _pair(("pkg/b.py", 6, 11), ("pkg/util.py", 4, 11))
    return sources, graph, pair


def test_a_block_that_is_a_whole_function_becomes_a_call_to_it() -> None:
    (plan,) = _plans(*_setup(_util(), _host()))
    reuse = plan.plan["reuse"]
    assert reuse["existing_symbol"] == "pkg/util.py::normalize"
    assert reuse["sites"] == [
        {
            "file": "pkg/b.py",
            "span": {"start": 6, "end": 11},
            "action": "replace_with_call",
            "new_text": "return normalize(text, sep)",
            "replaces": None,
        }
    ]
    recipe = build_recipe({"refactoring_type": "extract_helper", "plan": plan.plan})
    (step,) = recipe["steps"]
    assert step["action"] == "replace_with_call"
    assert step["call_site"]["new_text"] == "return normalize(text, sep)"
    assert step["reuse"]["existing_symbol"] == "pkg/util.py::normalize"
    assert recipe["summary"].startswith("Call the existing `normalize`")
    # A list row stays the size it was: the texts are for plan detail.
    assert "reuse" not in list_plan(plan.plan)


def test_a_private_function_in_another_module_keeps_the_new_helper_plan() -> None:
    sources, graph, pair = _setup(_util("_normalize"), _host(), name="_normalize")
    (plan,) = _plans(sources, graph, pair)
    assert "reuse" not in plan.plan
    occurrences = [("pkg/b.py", 6, 11), ("pkg/util.py", 4, 11)]
    assert find_reuse(graph, "python", occurrences, sources.get).refused == "not_importable"


def test_a_parameter_the_lines_never_use_is_refused() -> None:
    params = "text, sep, strict"
    sources, graph, pair = _setup(_util(params=params), _host(), params=params)
    occurrences = [("pkg/b.py", 6, 11), ("pkg/util.py", 4, 11)]
    verdict = find_reuse(graph, "python", occurrences, sources.get)
    assert verdict.plan is None and verdict.refused == "param_mismatch"
    assert "reuse" not in _plans(sources, graph, pair)[0].plan


def test_a_value_the_host_reads_afterwards_is_refused() -> None:
    body = [line for line in _BODY if "return" not in line and "if not" not in line]
    util = [*_util()[:5], *body]
    host = _host(tail=["    print(joined)", "    return cleaned"], body=body)
    sources = {"pkg/b.py": host, "pkg/util.py": util}
    graph = _graph(
        {
            "pkg/util.py": [("normalize", 4, len(util), "def normalize(text, sep)")],
            "pkg/b.py": [("run", 4, len(host), "def run(text, sep)")],
        }
    )
    occurrences = [("pkg/b.py", 6, 8), ("pkg/util.py", 4, 8)]
    assert find_reuse(graph, "python", occurrences, sources.get).refused == "outputs_used_after"


def test_a_copy_under_the_same_header_is_deleted_in_favour_of_the_original() -> None:
    copy = ["import os", "", "", *_util()[3:]]
    sources = {"pkg/a.py": copy, "pkg/util.py": _util()}
    graph = _graph(
        {
            "pkg/util.py": [("normalize", 4, 11, "def normalize(text, sep)")],
            "pkg/a.py": [("normalize", 4, 11, "def normalize(text, sep)")],
        }
    )
    occurrences = [("pkg/a.py", 4, 11), ("pkg/util.py", 4, 11)]
    reuse = find_reuse(graph, "python", occurrences, sources.get).plan
    # Both are the same function; the earlier path is kept, the other deleted.
    assert reuse["existing_symbol"] == "pkg/a.py::normalize"
    (site,) = reuse["sites"]
    assert site["action"] == "delete" and site["span"] == {"start": 4, "end": 11}
    assert site["replaces"] == "pkg/util.py::normalize"


def test_parameter_names_refuse_what_a_positional_call_cannot_fill() -> None:
    assert parameter_names("def f(self, a, b=1)", "python", "method") == ("self", ["a", "b"])
    assert parameter_names("def f(a, *rest)", "python", "function") is None
    assert parameter_names("function f({ a, b }: P)", "typescript", "function") is None


def test_a_private_typescript_copy_of_an_exported_function_is_deleted() -> None:
    body = ["  const primary = variant === 1;", "  if (!primary) {", "    return 0;", "  }", "  return 2;", "}"]
    atoms = ["export function pick(variant: number): number {", *body]
    panel = ["import x from './x';", "", "function pick(variant: number): number {", *body]
    sources = {"ui/atoms.ts": atoms, "ui/panel.ts": panel}
    graph = _graph(
        {
            "ui/atoms.ts": [("pick", 1, 7, "function pick(variant: number): number")],
            "ui/panel.ts": [("pick", 3, 9, "function pick(variant: number): number")],
        }
    )
    graph.nodes["ui/panel.ts::pick"]["visibility"] = "private"
    occurrences = [("ui/atoms.ts", 1, 7), ("ui/panel.ts", 3, 9)]
    reuse = find_reuse(graph, "typescript", occurrences, sources.get).plan
    assert reuse["existing_symbol"] == "ui/atoms.ts::pick"
    assert reuse["sites"][0]["action"] == "delete"
