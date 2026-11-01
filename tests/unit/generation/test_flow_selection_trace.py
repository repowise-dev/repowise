"""Flow pages: declared entries are picked, the trace skips plumbing, the material covers every step."""

from __future__ import annotations

import networkx as nx

from repowise.core.generation.flow_entries import CLI, TOOL, DocSection, Entry, doc_mentions
from repowise.core.generation.flow_pages import select_flow_plans
from repowise.core.generation.flow_trace import call_context
from repowise.core.generation.page_generator.flow import flow_context, ground_flow_citations
from repowise.core.generation.page_generator.flow_material import (
    build_material,
    options_summary,
)


def _add(g: nx.DiGraph, node: str, start: int, end: int, **attrs) -> None:
    path, name = node.split("::", 1)
    g.add_node(path, node_type="file", is_test=False)
    g.add_node(node, node_type="symbol", kind="function", name=name, file_path=path,
               start_line=start, end_line=end, visibility="public", **attrs)


def _call(g: nx.DiGraph, caller: str, callee: str, line: int) -> None:
    g.add_edge(caller, callee, edge_type="calls", confidence=1.0, call_lines=[line])


def _wide(g: nx.DiGraph, root: str, prefix: str, n: int = 14) -> None:
    """Give *root* a reach across several files so it qualifies as an operation."""
    for i in range(n):
        node = f"{prefix}/m{i % 4}.py::work{i}"
        _add(g, node, 1 + i * 3, 2 + i * 3)
        _call(g, root, node, 50 + i)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def test_a_declared_command_is_picked_even_when_production_code_calls_it():
    g = nx.DiGraph()
    _add(g, "cli/cmds.py::init_command", 10, 40, decorators=['@cli.command("init")'])
    _add(g, "cli/cmds.py::update_command", 50, 60)
    _wide(g, "cli/cmds.py::init_command", "engine")
    # A second command reuses init: the old rule ("nothing calls it") dropped init.
    _call(g, "cli/cmds.py::update_command", "cli/cmds.py::init_command", 55)
    plans = select_flow_plans(g, program="tool")
    init = next(p for p in plans if p.entry_id == "cli/cmds.py::init_command")
    assert init.kind == CLI
    assert init.public == "tool init"
    assert init.title == "How tool init works"


def test_documented_entries_outrank_bigger_undocumented_ones():
    g = nx.DiGraph()
    _add(g, "srv/a.py::get_big", 1, 9, decorators=["@mcp.tool()"])
    _add(g, "srv/b.py::get_answer", 1, 9, decorators=["@mcp.tool()"])
    _wide(g, "srv/a.py::get_big", "big", n=16)
    _wide(g, "srv/b.py::get_answer", "ans", n=13)
    docs = [DocSection("README.md", "Tools", 1, 9, "", ("get_answer(question)",))] * 5
    plans = select_flow_plans(g, docs=docs)
    assert [p.entry_id for p in plans][:2] == ["srv/b.py::get_answer", "srv/a.py::get_big"]
    assert plans[0].title == "How the get_answer tool answers a call"


def test_doc_mentions_count_code_as_typed_not_prose():
    entry = Entry("cli.py::run_command", CLI, "tool run")
    sections = [
        DocSection("README.md", "Run", 1, 5, "", ("tool run --port 8000",)),
        DocSection("docs/x.md", "Other", 1, 5, "", ("tool runner",)),
    ]
    assert doc_mentions(entry, sections) == 1
    assert doc_mentions(Entry("t.py::get_x", TOOL, "get_x"), [DocSection("a.md", "", 1, 2, "", ("x = get_x(1)",))]) == 1


# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------


_SOURCE = {
    "app/run.py": [
        "def run():",  # 1
        "    with progress_bar():",  # 2
        "        pass",  # 3
        "    data = load()",  # 4
        "    print_summary(data)",  # 5
        "    if data.empty:",  # 6
        "        return bail()",  # 7
        "    try:",  # 8
        "        build(data)",  # 9
        "    except Exception:",  # 10
        "        recover()",  # 11
        "    for item in data:",  # 12
        "        check(item)",  # 13
    ],
}


def _traced_graph() -> nx.DiGraph:
    g = nx.DiGraph()
    _add(g, "app/run.py::run", 1, 13, decorators=['@cli.command("run")'])
    lines = {"progress_bar": 2, "load": 4, "print_summary": 5, "bail": 7, "build": 9,
             "recover": 11, "check": 13}
    for name, line in lines.items():
        node = f"app/{name}.py::{name}"
        _add(g, node, 1, 20)
        _call(g, "app/run.py::run", node, line)
    # Work under each callee so every one of them is worth a step.
    for name in lines:
        for i in range(3):
            leaf = f"lib/{name}/{i}.py::helper{i}"
            _add(g, leaf, 1, 2)
            _call(g, f"app/{name}.py::{name}", leaf, 3 + i)
    return g


def _read(path: str) -> list[str]:
    return _SOURCE.get(path, [])


def test_the_trace_skips_presentation_and_context_managers_and_classes_each_call():
    (plan,) = select_flow_plans(_traced_graph(), read=_read)
    by_name = {s.name: s for s in plan.steps}
    assert "print_summary" not in by_name and "progress_bar" not in by_name
    assert set(plan.skipped) >= {"print_summary", "progress_bar"}
    assert by_name["load"].context == "always"
    assert by_name["bail"].context == "early_exit"
    assert by_name["build"].context == "always"  # inside try, not a branch
    assert by_name["recover"].context == "handler"
    assert by_name["check"].context == "loop"
    # An error handler is one step: never expanded into its own callees.
    recover = plan.steps.index(by_name["recover"])
    assert not any(s.parent == recover for s in plan.steps)
    assert len(plan.steps) <= 20


def test_call_context_reads_the_enclosing_block():
    lines = _SOURCE["app/run.py"]
    assert call_context(lines, 2, 1) == "with"
    assert call_context(lines, 7, 1) == "early_exit"
    assert call_context(lines, 11, 1) == "handler"
    assert call_context(lines, 13, 1) == "loop"
    assert call_context(lines, None, 1) == "always"
    assert call_context([], 4, 1) == "always"


# ---------------------------------------------------------------------------
# Material
# ---------------------------------------------------------------------------


def test_every_step_gets_an_excerpt_with_its_call_sites():
    g = _traced_graph()
    (plan,) = select_flow_plans(g, read=_read)
    material = build_material(plan, g, _read, ())
    root = material.excerpts[0]
    assert "    9         build(data)" in root  # the window around a traced call
    assert len(material.excerpts) == len(plan.steps)
    ctx = flow_context(plan, g, {}, {}, material=material)
    assert {s["name"] for s in ctx["exits"]} == {"bail", "recover"}
    assert ctx["public"] == "run" and ctx["kind"] == CLI


def test_a_small_code_cap_drops_the_deepest_steps_code_first():
    g = _traced_graph()
    (plan,) = select_flow_plans(g, read=_read)
    full = build_material(plan, g, _read, ()).excerpts
    tight = build_material(plan, g, _read, (), code_cap=len(full[0]) // 4).excerpts
    assert tight[0] and len(tight) == len(full)
    assert not tight[-1]


def test_options_are_summarised_one_line_each():
    out = options_summary([
        '@click.command("run")',
        '@click.option("--port", "-p", default=5000, help="The port to bind to. Defaults to 5000.")',
        '@click.option("--reload/--no-reload", help=("Enable or disable"\n " the reloader."))',
        "@pass_script_info",
    ])
    assert out.splitlines() == [
        "- @click.command(run)",
        "- option --port / -p: The port to bind to.",
        "- option --reload/--no-reload: Enable or disable the reloader.",
        "- @pass_script_info",
    ]


def test_a_narrow_citation_inside_a_step_is_kept_and_a_wrong_one_is_grounded():
    spans = {"app/run.py": [(1, 40, "run")]}
    text = "A `app/run.py:4-9` and B `app/run.py:30-90` and C `app/run.py:200-210`."
    out = ground_flow_citations(text, spans)
    assert "`app/run.py:4-9`" in out
    assert "`app/run.py:1-40`" in out
    assert "`app/run.py`" in out


def test_a_package_reexport_in_parentheses_is_public_api():
    from repowise.core.generation.flow_entries import API, declared_entries

    g = nx.DiGraph()
    _add(g, "pkg/app.py::serve", 1, 5)
    g.add_node("pkg/__init__.py", node_type="file")
    source = {"pkg/__init__.py": ["from .app import (", "    serve,  # the server", "    _private,", ")"]}
    entries = declared_entries(g, lambda p: source.get(p, []))
    assert [(e.kind, e.public) for e in entries] == [(API, "serve")]


def test_a_call_in_a_braced_catch_block_is_a_handler():
    lines = ["function run() {", "  try {", "    work();", "  } catch (e) {", "    recover();", "  }", "}"]
    assert call_context(lines, 3, 1) == "always"
    assert call_context(lines, 5, 1) == "handler"


def test_a_one_line_guard_return_is_an_early_exit():
    lines = ["def run(data):", "    if not data: return bail()", "    work(data)"]
    assert call_context(lines, 2, 1) == "early_exit"
    assert call_context(lines, 3, 1) == "always"


def test_an_indented_method_cites_from_its_decorators():
    from repowise.core.generation.page_generator.flow_material import decorator_start

    lines = ["class Tools:", "", "    @mcp.tool()", "    @traced", "    def get_x(self):", "        pass"]
    assert decorator_start(lines, 5) == 3


def test_a_command_is_named_after_the_script_that_reaches_it():
    g = nx.DiGraph()
    _add(g, "src/acme/cli/cmds.py::init_command", 10, 40, decorators=['@cli.command("init")'])
    _wide(g, "src/acme/cli/cmds.py::init_command", "engine")
    scripts = {"acme-admin": "acme/admin", "acme-cli": "acme/cli"}
    (plan,) = select_flow_plans(g, program="acme-admin", scripts=scripts)
    assert plan.public == "acme-cli init"


def test_the_reuse_subject_ignores_other_walkthroughs_but_follows_the_docs():
    from repowise.core.generation.page_generator.flow import _subject

    g = _traced_graph()
    (plan,) = select_flow_plans(g, read=_read)
    plain = build_material(plan, g, _read, ())
    doc = DocSection("README.md", "Run", 1, 3, "Use `run` to start.", ("run",))
    with_doc = build_material(plan, g, _read, (doc,))
    assert _subject(plan, {}, plain) == _subject(plan, {}, build_material(plan, g, _read, ()))
    assert _subject(plan, {}, plain) != _subject(plan, {}, with_doc)
