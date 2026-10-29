"""Python closures keep their own callers without becoming module exports."""

from datetime import datetime
from pathlib import Path

import pytest

from repowise.core.analysis.public_api import public_symbols
from repowise.core.ingestion.call_resolver import CallResolver
from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import parse_file


def _parse(tmp_path: Path, source: str, name: str = "sample.py", language: str = "python"):
    path = tmp_path / name
    path.write_text(source)
    file = FileInfo(
        path=name, abs_path=str(path), language=language, size_bytes=path.stat().st_size,
        git_hash="", last_modified=datetime.now(), is_test=False, is_config=False,
        is_api_contract=False, is_entry_point=False,
    )
    return parse_file(file, source.encode())


def _edges(tmp_path: Path, *files):
    parsed = {file.file_info.path: file for file in files}
    resolver = CallResolver(parsed, {}, repo_path=str(tmp_path))
    return {
        (edge.caller_id, edge.callee_id)
        for path, file in parsed.items()
        for edge in resolver.resolve_file(path, file.calls)
    }


def test_nested_function_owns_its_calls(tmp_path):
    parsed = _parse(tmp_path, '''def echo(value):
    return value

def outer():
    def inner():
        echo("hi")
    inner()
''')
    inner = next(s for s in parsed.symbols if s.name == "inner")
    assert (inner.id, inner.parent_name, inner.kind, inner.visibility) == (
        "sample.py::outer::inner", "outer", "function", "private",
    )
    assert inner.qualified_name == "sample.outer.inner"
    assert [(c.target_name, c.caller_symbol_id) for c in parsed.calls] == [
        ("echo", inner.id), ("inner", "sample.py::outer"),
    ]
    assert _edges(tmp_path, parsed) == {
        (inner.id, "sample.py::echo"), ("sample.py::outer", inner.id),
    }


def test_same_named_closures_are_resolved_in_their_own_scope(tmp_path):
    parsed = _parse(tmp_path, '''def first():
    def helper():
        return 1
    return helper()

def second():
    def helper():
        return 2
    return helper()
''')
    assert _edges(tmp_path, parsed) == {
        ("sample.py::first", "sample.py::first::helper"),
        ("sample.py::second", "sample.py::second::helper"),
    }


def test_nested_functions_keep_full_ancestry_and_recursive_self_edges(tmp_path):
    parsed = _parse(tmp_path, '''def outer():
    def helper():
        return helper()
    async def middle():
        async def inner():
            return helper()
        return await inner()
    return middle()
''')
    inner = next(s for s in parsed.symbols if s.name == "inner")
    assert inner.id == "sample.py::outer::middle::inner"
    assert inner.is_async
    assert _edges(tmp_path, parsed) == {
        ("sample.py::outer::helper", "sample.py::outer::helper"),
        (inner.id, "sample.py::outer::helper"),
        ("sample.py::outer::middle", inner.id),
        ("sample.py::outer", "sample.py::outer::middle"),
    }


@pytest.mark.parametrize("body", [
    "from external import helper\n        return helper(n)",
    "helper = unknown\n        return helper(n)",
    "return factory().helper(n)",
])
def test_a_nested_namesake_without_proven_recursion_draws_no_edge(tmp_path, body):
    parsed = _parse(tmp_path, f'''def helper(n):
    return n
def outer():
    def helper(n):
        {body}
    return helper
''')
    assert _edges(tmp_path, parsed) == set()


def test_a_nested_functions_own_name_parameter_is_not_recursion(tmp_path):
    parsed = _parse(tmp_path, '''def outer():
    def helper(helper):
        return helper()
    return helper
''')
    assert _edges(tmp_path, parsed) == set()


def test_a_childs_parameter_does_not_hide_its_parents_recursion(tmp_path):
    parsed = _parse(tmp_path, '''def outer():
    def helper(n):
        def child(helper):
            return helper()
        return helper(n - 1) if n else 0
    return helper
''')
    assert _edges(tmp_path, parsed) == {
        ("sample.py::outer::helper", "sample.py::outer::helper"),
    }


def test_a_nested_recursive_name_with_two_definitions_is_ambiguous(tmp_path):
    parsed = _parse(tmp_path, '''def outer():
    def helper(n):
        return helper(n - 1) if n else 0
    def helper(n):
        return n
    return helper
''')
    assert _edges(tmp_path, parsed) == set()


def test_closure_inside_method_is_not_itself_a_method(tmp_path):
    parsed = _parse(tmp_path, '''class Worker:
    def save(self):
        return 1
    def run(self):
        def callback():
            return self.save()
        return callback()
''')
    symbols = {s.name: s for s in parsed.symbols}
    assert symbols["run"].kind == "method"
    assert symbols["callback"].kind == "function"
    assert symbols["callback"].id == "sample.py::Worker::run::callback"
    assert _edges(tmp_path, parsed) == {
        ("sample.py::Worker::run", "sample.py::Worker::run::callback"),
        ("sample.py::Worker::run::callback", "sample.py::Worker::save"),
    }


def test_closure_does_not_hide_a_module_function_or_leak_to_another_file(tmp_path):
    parsed = _parse(tmp_path, '''def helper():
    return 1
def owner():
    def helper():
        return 2
    def private_helper():
        return 3
    return helper()
def unrelated():
    return helper() + private_helper()
''')
    other = _parse(tmp_path, "def run():\n    return private_helper()\n", "other.py")
    assert _edges(tmp_path, parsed, other) == {
        ("sample.py::owner", "sample.py::owner::helper"),
        ("sample.py::unrelated", "sample.py::helper"),
    }


@pytest.mark.parametrize("receiver", ["self.save()", "self.client.save()"])
def test_a_closures_self_parameter_does_not_capture_the_method_instance(tmp_path, receiver):
    parsed = _parse(tmp_path, f'''class Worker:
    def save(self):
        return 1
    def run(self):
        def inner(self):
            return {receiver}
        return inner(unknown)
''')
    assert _edges(tmp_path, parsed) == {
        ("sample.py::Worker::run", "sample.py::Worker::run::inner"),
    }


def test_a_typed_self_parameter_uses_its_declared_type(tmp_path):
    parsed = _parse(tmp_path, '''class Other:
    def save(self):
        return 1
class Worker:
    def save(self):
        return 2
    def run(self):
        def inner(self: Other):
            return self.save()
        return inner(unknown)
''')
    assert _edges(tmp_path, parsed) == {
        ("sample.py::Worker::run", "sample.py::Worker::run::inner"),
        ("sample.py::Worker::run::inner", "sample.py::Other::save"),
    }


def test_parameters_and_reassignment_still_shadow_closures(tmp_path):
    parsed = _parse(tmp_path, '''def outer():
    def helper():
        return 1
    def inner(helper):
        return helper()
    helper = something_else
    return helper()
''')
    assert _edges(tmp_path, parsed) == set()


def test_global_declaration_bypasses_enclosing_closure(tmp_path):
    parsed = _parse(tmp_path, '''def helper():
    return 1
def outer():
    def helper():
        return 2
    def inner():
        global helper
        return helper()
    return helper() + inner()
''')
    assert _edges(tmp_path, parsed) == {
        ("sample.py::outer::inner", "sample.py::helper"),
        ("sample.py::outer", "sample.py::outer::helper"),
        ("sample.py::outer", "sample.py::outer::inner"),
    }


def test_a_child_parameter_does_not_shadow_its_parents_function(tmp_path):
    parsed = _parse(tmp_path, '''def outer():
    def helper():
        return 1
    def inner(helper):
        return helper()
    return helper()
''')
    assert _edges(tmp_path, parsed) == {
        ("sample.py::outer", "sample.py::outer::helper"),
    }


@pytest.mark.parametrize("parameter, assignment", [("helper", ""), ("", "    helper = unknown\n")])
def test_an_inner_definition_shadows_an_outer_parameter_or_assignment(tmp_path, parameter, assignment):
    parsed = _parse(tmp_path, f'''def outer({parameter}):
{assignment}    def inner():
        def helper():
            return 1
        return helper()
    return inner()
''')
    assert _edges(tmp_path, parsed) == {
        ("sample.py::outer", "sample.py::outer::inner"),
        ("sample.py::outer::inner", "sample.py::outer::inner::helper"),
    }


@pytest.mark.parametrize("body", ["return use(value)", "\n        return use(value)"])
def test_a_nested_default_is_called_by_the_defining_scope(tmp_path, body):
    parsed = _parse(tmp_path, f'''def factory():
    return 1
def use(value):
    return value
def outer():
    def inner(value=factory()): {body}
    return inner()
''')
    assert _edges(tmp_path, parsed) == {
        ("sample.py::outer", "sample.py::factory"),
        ("sample.py::outer", "sample.py::outer::inner"),
        ("sample.py::outer::inner", "sample.py::use"),
    }


def test_graph_keeps_returned_callback_and_its_captured_receiver(tmp_path):
    parsed = _parse(tmp_path, '''class Store:
    def save(self):
        return 1
def outer(store: Store):
    def middle():
        def callback():
            return store.save()
        return callback
    return middle()
''')
    builder = GraphBuilder(tmp_path)
    builder.add_file(parsed)
    graph = builder.build()
    callback = "sample.py::outer::middle::callback"
    assert graph.has_edge(callback, "sample.py::Store::save")
    assert graph.edges["sample.py::outer::middle", callback]["edge_type"] == "references"
    assert graph.nodes[callback]["qualified_name"] == "sample.outer.middle.callback"


def test_decorated_closure_is_emitted_once_with_its_metadata(tmp_path):
    parsed = _parse(tmp_path, '''def outer():
    @cache
    async def inner(value):
        """A cached closure."""
        return await load(value)
    return inner
''')
    inner, = [s for s in parsed.symbols if s.name == "inner"]
    assert inner.decorators == ["@cache"]
    assert inner.docstring == "A cached closure."
    assert inner.is_async
    assert inner.signature == "async def inner(value)"


def test_local_class_and_javascript_callback_symbols_stay_unchanged(tmp_path):
    python = _parse(tmp_path, "def factory():\n    class Local:\n        def method(self): pass\n")
    assert [s.name for s in python.symbols] == ["factory"]
    javascript = _parse(tmp_path, "function outer() { function inner() { target(); } inner(); }",
                        "sample.js", "javascript")
    assert [s.name for s in javascript.symbols] == ["outer"]


def test_lexical_parents_do_not_publish_closures_or_make_them_class_members(tmp_path):
    parsed = _parse(tmp_path, '''def factory():
    def helper():
        return 1
    return helper

class Worker:
    def run(self):
        def callback():
            return self.save()
        return callback
    def save(self):
        return 1
''', "__init__.py")
    path = parsed.file_info.path
    published = public_symbols({path: parsed})
    assert set(published) == {
        "__init__.py::factory", "__init__.py::Worker",
        "__init__.py::Worker::run", "__init__.py::Worker::save",
    }
    assert "helper" not in parsed.exports
    assert "callback" not in parsed.exports

    builder = GraphBuilder(tmp_path)
    builder.add_file(parsed)
    graph = builder.build()
    callback = "__init__.py::Worker::run::callback"
    assert graph.nodes[callback]["parent_name"] == "Worker::run"
    assert graph.nodes[callback]["kind"] == "function"
    assert graph.nodes[callback]["visibility"] == "private"
    assert {
        (source, target)
        for source, target, data in graph.edges(data=True)
        if data.get("edge_type") == "has_method"
    } == {
        ("__init__.py::Worker", "__init__.py::Worker::run"),
        ("__init__.py::Worker", "__init__.py::Worker::save"),
    }
    assert graph.edges[callback, "__init__.py::Worker::save"]["edge_type"] == "calls"
