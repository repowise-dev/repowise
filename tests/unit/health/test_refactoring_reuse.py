"""Extract Helper names an existing function when one clone site already is it.

A clone group whose one occurrence is a whole function ``F`` is planned as
"call ``F``" at the other sites (or "delete this copy" when a site is ``F``
again), never as a second copy. Anything that could make the call wrong keeps
the new-helper plan: these tests pin each refusal.
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


def _fn(name: str = "normalize", params: str = "text, sep", body: list[str] | None = None) -> list[str]:
    return [f"def {name}({params}):", '    """Normalise a separated string."""', *(body or _BODY)]


def _host(
    body: list[str] | None = None, tail: list[str] | None = None, params: str = "text, sep"
) -> list[str]:
    return [f"def run({params}):", '    print("start")', *(body or _BODY), *(tail or [])]


class _Repo:
    """Files, their symbols and import edges, as the graph and reader see them."""

    def __init__(self) -> None:
        self.graph = nx.DiGraph()
        self.files: dict[str, list[str]] = {}

    def file(self, path: str, lines: list[str]) -> _Repo:
        self.files[path] = lines
        self.graph.add_node(path, node_type="file")
        return self

    def symbol(self, path: str, name: str, start: int, end: int, signature: str, **attrs) -> _Repo:
        parent = attrs.get("parent_name")
        sid = f"{path}::{parent}::{name}" if parent else f"{path}::{name}"
        module = path.removesuffix(".py").replace("/", ".")
        self.graph.add_node(
            sid,
            node_type="symbol",
            kind=attrs.get("kind", "function"),
            name=name,
            file_path=path,
            start_line=start,
            end_line=end,
            signature=signature,
            visibility=attrs.get("visibility", "private" if name.startswith("_") else "public"),
            parent_name=parent,
            qualified_name=f"{module}.{name}",
            is_async=False,
        )
        self.graph.add_edge(path, sid, edge_type="defines")
        return self

    def imports(self, importer: str, imported: str) -> _Repo:
        self.graph.add_edge(importer, imported, edge_type="imports")
        return self

    def reuse(self, occurrences, language: str = "python"):
        return find_reuse(self.graph, language, occurrences, self.files.get)


_OCCURRENCES = [("pkg/b.py", 6, 11), ("pkg/util.py", 4, 11)]


def _pair(
    body: list[str] | None = None,
    util_head: list[str] | None = None,
    host_head: list[str] | None = None,
    name: str = "normalize",
    params: str = "text, sep",
    host_params: str = "text, sep",
) -> _Repo:
    """``pkg/util.py`` defining F on lines 4-11; ``pkg/b.py`` whose ``run``
    (lines 4-11) repeats F's body on lines 6-11. ``b`` imports ``util``."""
    util = [*(util_head or ["import json", "", ""]), *_fn(name, params, body)]
    host = [*(host_head or ["from pkg.util import normalize", "", ""]), *_host(body, params=host_params)]
    return (
        _Repo()
        .file("pkg/util.py", util)
        .file("pkg/b.py", host)
        .symbol("pkg/util.py", name, 4, len(util), f"def {name}({params})")
        .symbol("pkg/b.py", "run", 4, len(host), f"def run({host_params})")
        .imports("pkg/b.py", "pkg/util.py")
    )


def test_a_block_that_is_a_whole_function_becomes_a_call_to_it() -> None:
    reuse = _pair().reuse(_OCCURRENCES).plan
    assert reuse["existing_symbol"] == "pkg/util.py::normalize"
    (site,) = reuse["sites"]
    assert site == {
        "file": "pkg/b.py",
        "span": {"start": 6, "end": 11},
        "action": "replace_with_call",
        "new_text": "return normalize(text, sep)",
        "replaces": None,
    }
    plan = {"occurrences": [], "reuse": reuse}
    recipe = build_recipe({"refactoring_type": "extract_helper", "plan": plan})
    (step,) = recipe["steps"]
    assert step["action"] == "replace_with_call"
    assert step["call_site"]["new_text"] == "return normalize(text, sep)"
    assert step["reuse"]["existing_symbol"] == "pkg/util.py::normalize"
    assert recipe["summary"].startswith("Call the existing `normalize`")
    # A list row stays the size it was: the texts are for plan detail.
    assert "reuse" not in list_plan(plan)


def test_the_detector_attaches_reuse_only_with_a_reader() -> None:
    repo = _pair()
    pair = ClonePair("pkg/b.py", "pkg/util.py", 6, 11, 4, 11, token_count=60)
    ctx = RefactoringContext(
        file_path="pkg/b.py",
        language="python",
        nloc=200,
        clones=[pair],
        graph=repo.graph,
        source_lines=repo.files["pkg/b.py"],
        read_lines=repo.files.get,
    )
    (plan,) = [s for s in detect_refactorings(ctx) if s.refactoring_type == "extract_helper"]
    assert plan.plan["reuse"]["existing_symbol"] == "pkg/util.py::normalize"
    ctx.read_lines = None
    (plain,) = [s for s in detect_refactorings(ctx) if s.refactoring_type == "extract_helper"]
    assert "reuse" not in plain.plan


def test_a_private_function_in_another_module_is_not_importable() -> None:
    repo = _pair(name="_normalize", host_head=["import os", "", ""])
    assert repo.reuse(_OCCURRENCES).refused == "not_importable"


def test_a_parameter_the_lines_never_use_is_refused() -> None:
    assert _pair(params="text, sep, strict").reuse(_OCCURRENCES).refused == "param_mismatch"


def test_a_keyword_only_parameter_is_refused() -> None:
    assert _pair(params="text, *, sep").reuse(_OCCURRENCES).refused == "param_mismatch"
    assert parameter_names("def f(a, *rest)", "python", "function") is None
    assert parameter_names("function f({ a, b }: P)", "typescript", "function") is None


def test_a_value_the_host_reads_afterwards_is_refused() -> None:
    body = _BODY[:3]
    util = ["import json", "", "", *_fn(body=body)]
    host = ["import os", "", "", *_host(body=body, tail=["    print(joined)"])]
    repo = (
        _Repo()
        .file("pkg/util.py", util)
        .file("pkg/b.py", host)
        .symbol("pkg/util.py", "normalize", 4, len(util), "def normalize(text, sep)")
        .symbol("pkg/b.py", "run", 4, len(host), "def run(text, sep)")
        .imports("pkg/b.py", "pkg/util.py")
    )
    assert repo.reuse([("pkg/b.py", 6, 8), ("pkg/util.py", 4, 8)]).refused == "outputs_used_after"


# -- names the body reads -------------------------------------------------------


def test_a_module_global_bound_differently_at_the_site_is_refused() -> None:
    body = ["    parts = text.split(SEP)", *_BODY[1:]]
    repo = _pair(body, util_head=["SEP = ','", "", ""], host_head=["SEP = ';'", "", ""])
    assert repo.reuse(_OCCURRENCES).refused == "free_names"


def test_an_imported_helper_must_come_from_the_same_module() -> None:
    body = ["    parts = clean(text).split(sep)", *_BODY[1:]]

    def build(source: str) -> _Repo:
        util_head = ["from pkg.text import clean", "", ""]
        host_head = [f"from {source} import clean", "from pkg.util import normalize", ""]
        return _pair(body, util_head=util_head, host_head=host_head)

    assert build("pkg.other").reuse(_OCCURRENCES).refused == "free_names"
    assert build("pkg.text").reuse(_OCCURRENCES).plan is not None


def test_a_host_variable_shadowing_a_module_name_is_refused() -> None:
    body = ["    parts = text.split(json.dumps(sep))", *_BODY[1:]]
    repo = _pair(body, host_head=["import json", "", ""], host_params="text, sep, json")
    assert repo.reuse(_OCCURRENCES).refused == "free_names"


def test_a_site_binding_the_name_elsewhere_is_refused() -> None:
    repo = _pair(host_head=["from pkg.other import normalize", "", ""])
    assert repo.reuse(_OCCURRENCES).refused == "name_taken"


def test_scope_statements_and_walrus_targets_are_read() -> None:
    body = ["    global CACHE", *_BODY[:5]]
    repo = _pair(body)
    assert repo.reuse(_OCCURRENCES).refused == "scope_statement"
    walrus = ["    if (n := len(text)) > 3:", "        text = text[:n]", *_BODY[:4]]
    util = ["import json", "", "", *_fn(body=walrus)]
    host = ["import os", "", "", *_host(body=walrus, tail=["    print(n)"])]
    repo = (
        _Repo()
        .file("pkg/util.py", util)
        .file("pkg/b.py", host)
        .symbol("pkg/util.py", "normalize", 4, len(util), "def normalize(text, sep)")
        .symbol("pkg/b.py", "run", 4, len(host), "def run(text, sep)")
        .imports("pkg/b.py", "pkg/util.py")
    )
    assert repo.reuse([("pkg/b.py", 6, 11), ("pkg/util.py", 4, 11)]).refused == "outputs_used_after"


# -- reach ---------------------------------------------------------------------


def test_a_sibling_whose_file_imports_the_site_is_an_import_cycle() -> None:
    repo = _pair(host_head=["import os", "", ""])
    repo.graph.remove_edge("pkg/b.py", "pkg/util.py")
    assert repo.reuse(_OCCURRENCES).plan is not None  # a sibling, no cycle
    repo.imports("pkg/util.py", "pkg/b.py")
    assert repo.reuse(_OCCURRENCES).refused == "import_cycle"


def test_another_directory_needs_an_existing_import() -> None:
    repo = _pair(host_head=["import os", "", ""])
    repo.file("app/b.py", repo.files["pkg/b.py"]).symbol("app/b.py", "run", 4, 11, "def run(text, sep)")
    occurrences = [("app/b.py", 6, 11), ("pkg/util.py", 4, 11)]
    assert repo.reuse(occurrences).refused == "not_importable"
    repo.imports("app/b.py", "pkg/util.py")
    assert repo.reuse(occurrences).plan is not None


# -- decorators and method forms -------------------------------------------------


def test_a_decorated_function_is_refused() -> None:
    repo = _pair(util_head=["import functools", "", "@functools.cache"])
    assert repo.reuse(_OCCURRENCES).refused == "decorated"


def _method(lines: list[str]) -> list[str]:
    return ["    " + t for t in lines]


def test_a_static_method_is_refused() -> None:
    lines = ["class C:", "    @staticmethod", *_method(_fn()), "", *_method(_host(params="self, text, sep"))]
    repo = (
        _Repo()
        .file("pkg/c.py", lines)
        .symbol("pkg/c.py", "C", 1, len(lines), "class C", kind="class")
        .symbol("pkg/c.py", "normalize", 3, 10, "def normalize(text, sep)", kind="method", parent_name="C")
        .symbol("pkg/c.py", "run", 12, 19, "def run(self, text, sep)", kind="method", parent_name="C")
    )
    assert repo.reuse([("pkg/c.py", 3, 10), ("pkg/c.py", 14, 19)]).refused == "decorated"


def test_a_method_is_called_through_self_from_its_own_class() -> None:
    fn = _method(_fn(params="self, text, sep"))
    lines = ["class C:", *fn, "", *_method(_host(params="self, text, sep"))]
    repo = (
        _Repo()
        .file("pkg/c.py", lines)
        .symbol("pkg/c.py", "C", 1, len(lines), "class C", kind="class")
        .symbol("pkg/c.py", "normalize", 2, 9, "def normalize(self, text, sep)", kind="method", parent_name="C")
        .symbol("pkg/c.py", "run", 11, 18, "def run(self, text, sep)", kind="method", parent_name="C")
    )
    (site,) = repo.reuse([("pkg/c.py", 2, 9), ("pkg/c.py", 13, 18)]).plan["sites"]
    assert site["new_text"] == "return self.normalize(text, sep)"


def test_a_typescript_static_method_is_refused() -> None:
    body = ["    const n = a + 1;", "    const m = n * 2;", "    const k = m - 3;", "    return k;", "  }"]
    lines = ["class K {", "  static f(a: number): number {", *body, "  g(a: number): number {", *body, "}"]
    repo = (
        _Repo()
        .file("ui/k.ts", lines)
        .symbol("ui/k.ts", "K", 1, len(lines), "class K", kind="class")
        .symbol("ui/k.ts", "f", 2, 7, "f(a: number) -> number", kind="method", parent_name="K")
        .symbol("ui/k.ts", "g", 8, 13, "g(a: number) -> number", kind="method", parent_name="K")
    )
    verdict = repo.reuse([("ui/k.ts", 2, 7), ("ui/k.ts", 8, 13)], "typescript")
    assert verdict.refused == "static_or_accessor"


# -- whole copies ------------------------------------------------------------------


_TS_BODY = ["  const primary = variant === 1;", "  if (!primary) {", "    return 0;", "  }", "  return 2;", "}"]
_TS_OCCURRENCES = [("ui/atoms.ts", 1, 7), ("ui/panel.ts", 3, 9)]


def _ts_twins(twin_head: str = "", twin_visibility: str = "private") -> _Repo:
    atoms = ["export function pick(variant: number): number {", *_TS_BODY]
    panel = ["import x from './x';", "", f"{twin_head}function pick(variant: number): number {{", *_TS_BODY]
    return (
        _Repo()
        .file("ui/atoms.ts", atoms)
        .file("ui/panel.ts", panel)
        .symbol("ui/atoms.ts", "pick", 1, 7, "function pick(variant: number): number")
        .symbol("ui/panel.ts", "pick", 3, 9, "function pick(variant: number): number", visibility=twin_visibility)
    )


def test_a_private_typescript_copy_of_an_exported_function_is_deleted() -> None:
    reuse = _ts_twins().reuse(_TS_OCCURRENCES, "typescript").plan
    assert reuse["existing_symbol"] == "ui/atoms.ts::pick"
    (site,) = reuse["sites"]
    assert site["action"] == "delete" and site["replaces"] == "ui/panel.ts::pick"
    recipe = build_recipe({"refactoring_type": "extract_helper", "plan": {"reuse": reuse}})
    (step,) = recipe["steps"]
    assert step["action"] == "delete" and step["span"] == {"start": 3, "end": 9}
    assert "import `pick` from `ui/atoms.ts`" in step["text"]


def test_an_exported_or_default_exported_copy_is_not_deleted() -> None:
    for head in ("export ", "export default "):
        repo = _ts_twins(head, twin_visibility="public")
        assert repo.reuse(_TS_OCCURRENCES, "typescript").refused == "twin_exported"


def test_a_copy_other_files_call_is_not_deleted() -> None:
    repo = _ts_twins()
    repo.file("ui/other.ts", ["x"]).graph.add_edge("ui/other.ts", "ui/panel.ts::pick", edge_type="calls")
    assert repo.reuse(_TS_OCCURRENCES, "typescript").refused == "twin_imported"


def test_a_decorated_copy_is_refused() -> None:
    repo = _ts_twins()
    repo.files["ui/panel.ts"][1] = "@memo"
    assert repo.reuse(_TS_OCCURRENCES, "typescript").refused == "decorated"


def test_a_renamed_copy_calls_the_original_only_under_the_same_header() -> None:
    def build(header: str) -> _Repo:
        util = ["import json", "", "", *_fn()]
        copy = ["from pkg.util import normalize", "", "", header, *_fn()[1:]]
        return (
            _Repo()
            .file("pkg/util.py", util)
            .file("pkg/b.py", copy)
            .symbol("pkg/util.py", "normalize", 4, 11, "def normalize(text, sep)")
            .symbol("pkg/b.py", "_clean", 4, 11, header.rstrip(":"))
            .imports("pkg/b.py", "pkg/util.py")
        )

    occurrences = [("pkg/b.py", 4, 11), ("pkg/util.py", 4, 11)]
    (site,) = build("def _clean(text, sep):").reuse(occurrences).plan["sites"]
    assert site["action"] == "replace_with_call"
    assert site["new_text"] == "return normalize(text, sep)"
    differs = build("def _clean(text, sep) -> str:").reuse(occurrences)
    assert differs.refused == "header_differs"


def test_a_public_python_copy_is_not_deleted() -> None:
    lines = ["import json", "", "", *_fn()]
    repo = (
        _Repo()
        .file("pkg/a.py", lines)
        .file("pkg/util.py", list(lines))
        .symbol("pkg/a.py", "normalize", 4, 11, "def normalize(text, sep)")
        .symbol("pkg/util.py", "normalize", 4, 11, "def normalize(text, sep)")
    )
    assert repo.reuse([("pkg/a.py", 4, 11), ("pkg/util.py", 4, 11)]).refused == "twin_exported"


def test_a_default_parameter_is_passed_by_position() -> None:
    (site,) = _pair(params="text, sep=','").reuse(_OCCURRENCES).plan["sites"]
    assert site["new_text"] == "return normalize(text, sep)"


def test_a_copy_exported_by_a_later_statement_is_not_deleted() -> None:
    repo = _ts_twins()
    repo.files["ui/panel.ts"].append("export { pick };")
    assert repo.reuse(_TS_OCCURRENCES, "typescript").refused == "twin_exported"
