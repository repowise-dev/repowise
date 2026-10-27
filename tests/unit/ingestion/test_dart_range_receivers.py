"""Dart ``for (final p in xs)`` variables typed from the container they walk.

``for (final p in repos)`` spells no type for ``p``: it is the element of the
container. The scan reads the clauses and the container declarations; the
resolver joins them. Every negative removes one proof, and must leave the
variable untyped rather than guessed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.ingestion.call_resolver import CallResolver
from repowise.core.ingestion.languages.receiver_types import (
    DART_CALLBACK_METHODS,
    DartCallback,
    RangeClause,
    callbacks_binding,
    range_element_types,
    scan_dart_callbacks,
    scan_declarations,
    scan_ranges,
    types_in_span,
)
from tests.unit.ingestion.test_chained_receiver_calls import _parse_all


def _clauses(text: str) -> list[RangeClause]:
    return list(scan_ranges(text, "dart").clauses)


def _containers(text: str) -> dict[str, str | None]:
    return types_in_span(scan_ranges(text, "dart").containers, 1, 10_000)


class TestElementTypes:
    @pytest.mark.parametrize(
        ("spelling", "expected"),
        [
            ("List<Repo>", (None, "Repo")),
            ("Iterable<Repo>", (None, "Repo")),
            ("Set<Repo>", (None, "Repo")),
            ("Queue<Repo>", (None, "Repo")),
            ("Stream<Repo>", (None, "Repo")),
            ("List<Repo?>", (None, "Repo")),
            ("List<models.Repo>", (None, "Repo")),
        ],
    )
    def test_a_known_shape(self, spelling: str, expected: tuple[str | None, str | None]) -> None:
        assert range_element_types(spelling, "dart") == expected

    @pytest.mark.parametrize(
        "spelling",
        [
            "Map<String, Repo>",
            "List<List<Repo>>",
            "Future<List<Repo>>",
            "Future<Repo>",
            "Repo",
            "",
        ],
    )
    def test_an_unknown_shape_is_refused(self, spelling: str) -> None:
        assert range_element_types(spelling, "dart") is None


class TestClauses:
    def test_final_over_a_name(self) -> None:
        (clause,) = _clauses("for (final p in repos) {")
        assert (clause.key, clause.value, clause.head, clause.member, clause.call) == (
            "",
            "p",
            "repos",
            "",
            False,
        )

    def test_var_over_a_field(self) -> None:
        (clause,) = _clauses("for (var p in r.items) {")
        assert (clause.value, clause.head, clause.member, clause.call) == ("p", "r", "items", False)

    def test_a_zero_argument_method(self) -> None:
        (clause,) = _clauses("for (final p in r.all()) {")
        assert (clause.member, clause.call) == ("all", True)

    def test_await_for(self) -> None:
        (clause,) = _clauses("await for (final p in events) {")
        assert (clause.value, clause.head) == ("p", "events")

    @pytest.mark.parametrize(
        "line",
        [
            "for (Repo p in repos) {",  # a written type is _DART_TYPED's
            "for (final Repo p in repos) {",
            "for (final p in r.a.b) {",
            "for (final p in f().items) {",
            "for (final p in r.all(1)) {",
            "for (var i = 0; i < n; i++) {",
            "// for (final p in repos) {",
        ],
    )
    def test_anything_else_is_not_read(self, line: str) -> None:
        assert _clauses(line) == []


class TestContainers:
    def test_parameter_local_and_field(self) -> None:
        text = "void f(Iterable<Repo> rs, Set<Repo>? ss) {\n    Queue<Repo> q = Queue();\n}\n"
        assert _containers(text) == {"rs": "Iterable<Repo>", "ss": "Set<Repo>", "q": "Queue<Repo>"}

    def test_a_field_keeps_the_closer_that_makes_it_one(self) -> None:
        (field,) = scan_ranges("class A {\n  final List<Repo> repos;\n}\n", "dart").containers
        assert (field.name, field.type_name, field.closer) == ("repos", "List<Repo>", ";")

    def test_a_parameter_inside_parentheses_owns_no_field_closer(self) -> None:
        text = "class A {\n  A({List<Repo> xs = const []});\n  void f(List<Repo> ys) {}\n}\n"
        assert {d.closer for d in scan_ranges(text, "dart").containers} == {""}

    def test_a_map_is_no_container(self) -> None:
        assert _containers("void f(Map<String, Repo> m) {}") == {}


class TestTypedLoopVariable:
    def test_a_written_type_is_declared(self) -> None:
        text = "void f() {\n  for (Repo p in repos) {}\n  for (final Repo q in repos) {}\n}\n"
        names = {d.name: d.type_name for d in scan_declarations(text, "dart")}
        assert names == {"p": "Repo", "q": "Repo"}


_REPO = (
    "class Item {\n  void save() {}\n}\n"
    "class Repo {\n"
    "  Repo();\n"
    "  final List<Item> items = [];\n"
    "  final Map<String, Item> byName = {};\n"
    "  List<Item> all() => items;\n"
    "  Item get current => Item();\n"
    "  void close() {}\n"
    "}\n"
)

_PREFIX = 4  # lines of ``app.dart`` before the body


def _callees(tmp_path: Path, body: str, params: str = "Repo r, List<Repo> repos") -> set[str]:
    app = f"import 'repo.dart';\nclass App {{\n  void run({params}) {{\n{body}\n  }}\n}}\n"
    return _callees_of(tmp_path, app)


def _callees_of(tmp_path: Path, app: str, repo: str = _REPO) -> set[str]:
    """The edges out of ``App.run`` for a whole ``app.dart``."""
    parsed = _parse_all(tmp_path, {"repo.dart": ("dart", repo), "app.dart": ("dart", app)})
    for imp in parsed["app.dart"].imports:
        imp.resolved_file = "repo.dart"
    resolver = CallResolver(
        parsed, {"app.dart": {"repo.dart"}, "repo.dart": set()}, repo_path=str(tmp_path)
    )
    return {
        rc.callee_id
        for rc in resolver.resolve_file("app.dart", parsed["app.dart"].calls)
        if rc.caller_id == "app.dart::App::run"
    }


CLOSE = "repo.dart::Repo::close"
SAVE = "repo.dart::Item::save"


class TestResolution:
    def test_final_over_a_list_parameter(self, tmp_path: Path) -> None:
        assert CLOSE in _callees(tmp_path, "for (final p in repos) {\n  p.close();\n}")

    def test_var_over_a_list_parameter(self, tmp_path: Path) -> None:
        assert CLOSE in _callees(tmp_path, "for (var p in repos) {\n  p.close();\n}")

    def test_over_an_iterable_local(self, tmp_path: Path) -> None:
        body = "Iterable<Repo> rs = repos;\nfor (final p in rs) {\n  p.close();\n}"
        assert CLOSE in _callees(tmp_path, body)

    def test_over_a_field_of_a_typed_receiver(self, tmp_path: Path) -> None:
        assert SAVE in _callees(tmp_path, "for (final i in r.items) {\n  i.save();\n}")

    def test_over_a_method_result(self, tmp_path: Path) -> None:
        assert SAVE in _callees(tmp_path, "for (final i in r.all()) {\n  i.save();\n}")

    def test_await_for_over_a_stream(self, tmp_path: Path) -> None:
        body = "await for (final p in events) {\n  p.close();\n}"
        assert CLOSE in _callees(tmp_path, body, "Stream<Repo> events")

    def test_a_written_type_still_binds(self, tmp_path: Path) -> None:
        assert CLOSE in _callees(tmp_path, "for (Repo p in repos) {\n  p.close();\n}")
        assert CLOSE in _callees(tmp_path, "for (final Repo p in repos) {\n  p.close();\n}")

    def test_a_written_type_wins_over_the_container(self, tmp_path: Path) -> None:
        body = "for (Item p in repos) {\n  p.save();\n}"
        assert _callees(tmp_path, body) == {SAVE}

    def test_a_map_binds_nothing(self, tmp_path: Path) -> None:
        body = "for (final p in m) {\n  p.close();\n}"
        assert CLOSE not in _callees(tmp_path, body, "Map<String, Repo> m")

    def test_a_map_field_binds_nothing(self, tmp_path: Path) -> None:
        assert SAVE not in _callees(tmp_path, "for (final i in r.byName) {\n  i.save();\n}")

    def test_a_type_parameter_binds_nothing(self, tmp_path: Path) -> None:
        body = "for (final p in xs) {\n  p.close();\n}"
        assert CLOSE not in _callees(tmp_path, body, "List<T> xs")

    def test_a_nested_generic_binds_nothing(self, tmp_path: Path) -> None:
        body = "for (final p in xs) {\n  p.close();\n}"
        assert CLOSE not in _callees(tmp_path, body, "List<List<Repo>> xs")

    def test_an_unawaited_future_binds_nothing(self, tmp_path: Path) -> None:
        body = "for (final p in xs) {\n  p.close();\n}"
        assert CLOSE not in _callees(tmp_path, body, "Future<List<Repo>> xs")

    def test_an_unknown_container_binds_nothing(self, tmp_path: Path) -> None:
        assert CLOSE not in _callees(tmp_path, "for (final p in mystery) {\n  p.close();\n}")

    def test_a_loop_variable_reusing_a_declared_name_is_skipped(self, tmp_path: Path) -> None:
        """The parameter ``r`` stays a ``Repo`` for the call after the loop."""
        body = "for (final r in other) {\n  r.save();\n}\nr.close();"
        callees = _callees(tmp_path, body, "Repo r, List<Item> other")
        assert CLOSE in callees
        assert SAVE not in callees


class TestCallbackScan:
    """``items.forEach((p) => ..)``: the literal, its one parameter, and its span."""

    @staticmethod
    def _callbacks(text: str) -> list[DartCallback]:
        return list(scan_dart_callbacks(text))

    @pytest.mark.parametrize("method", DART_CALLBACK_METHODS)
    def test_every_one_element_method_is_read(self, method: str) -> None:
        (callback,) = self._callbacks(f"repos.{method}((p) => p.close());")
        assert (callback.name, callback.head, callback.member, callback.call) == (
            "p",
            "repos",
            "",
            False,
        )
        assert callback.method == method

    def test_a_block_body(self) -> None:
        (callback,) = self._callbacks("repos.where((p) {\n  return p.ok();\n});")
        assert (callback.start, callback.end) == (1, 3)

    def test_an_async_literal(self) -> None:
        (callback,) = self._callbacks("repos.forEach((p) async => await p.close());")
        assert callback.name == "p"

    def test_a_field_and_a_method_result(self) -> None:
        field, call = self._callbacks("r.items.map((i) => i);\nr.all().map((i) => i);")
        assert (field.head, field.member, field.call) == ("r", "items", False)
        assert (call.head, call.member, call.call) == ("r", "all", True)

    def test_this_and_a_null_aware_receiver(self) -> None:
        this, aware = self._callbacks("this.repos.map((p) => p);\nrepos?.forEach((p) => p);")
        assert (this.head, this.member) == ("this", "repos")
        assert (aware.head, aware.member) == ("repos", "")

    def test_an_expression_body_ends_at_its_argument(self) -> None:
        (callback,) = self._callbacks("repos.firstWhere((p) => p.ok(),\n    orElse: () => other);")
        assert (callback.start, callback.end) == (1, 1)

    @pytest.mark.parametrize(
        "line",
        [
            "repos.forEach((Repo p) => p.close());",  # a written type is _DART_TYPED's
            "repos.forEach((final Repo p) => p.close());",
            "repos.fold(0, (acc, p) => acc);",
            "repos.reduce((a, b) => a);",
            "repos.sort((a, b) => 0);",
            "repos.forEach((a, b) => a);",
            "repos.forEach(print);",
            "repos.forEach(() => 1);",
            "r.a.b.forEach((p) => p);",
            "f().forEach((p) => p);",
            "Repo.forEach((p) => p);",
            "repos.listen((p) => p);",
            "// repos.forEach((p) => p.close());",
        ],
    )
    def test_anything_else_is_not_read(self, line: str) -> None:
        assert self._callbacks(line) == []

    def test_an_unbalanced_literal_is_dropped(self) -> None:
        assert self._callbacks("repos.forEach((p) => p.close(") == []

    def test_a_bracket_in_a_string_does_not_end_the_literal(self) -> None:
        (callback,) = self._callbacks("repos.where((p) => p.name == ')' &&\n    p.ok());")
        assert (callback.start, callback.end) == (1, 2)

    def test_a_nested_literal_hides_the_outer_one(self) -> None:
        text = "a.forEach((p) => b.forEach((p) => p.x()));"
        callbacks = self._callbacks(text)
        (inner,) = callbacks_binding(callbacks, "p", 1)
        assert inner.head == "b"

    def test_literals_side_by_side_are_both_kept(self) -> None:
        text = "a.forEach((p) => p.x()); b.forEach((p) => p.y());"
        assert len(callbacks_binding(self._callbacks(text), "p", 1)) == 2

    def test_a_line_outside_the_literal_is_unbound(self) -> None:
        text = "repos.forEach((p) {\n  p.close();\n});\np.save();"
        assert callbacks_binding(self._callbacks(text), "p", 4) == ()


class TestCallbackResolution:
    def test_for_each_over_a_list_parameter(self, tmp_path: Path) -> None:
        assert CLOSE in _callees(tmp_path, "repos.forEach((p) => p.close());")

    def test_the_issue_repro(self, tmp_path: Path) -> None:
        body = "for (final p in repos) {\n  p.close();\n}\nrepos.forEach((p) => p.close());\nr.close();"
        assert _callees(tmp_path, body) == {CLOSE}

    @pytest.mark.parametrize(
        "method", ["forEach", "map", "where", "any", "every", "firstWhere", "expand"]
    )
    def test_each_method_binds(self, tmp_path: Path, method: str) -> None:
        assert CLOSE in _callees(tmp_path, f"repos.{method}((p) => p.close());")

    def test_a_block_body(self, tmp_path: Path) -> None:
        body = "repos.where((p) {\n  p.close();\n  return true;\n});"
        assert CLOSE in _callees(tmp_path, body)

    def test_over_an_iterable_local(self, tmp_path: Path) -> None:
        body = "Iterable<Repo> rs = repos;\nrs.forEach((p) => p.close());"
        assert CLOSE in _callees(tmp_path, body)

    def test_over_a_field_of_a_typed_receiver(self, tmp_path: Path) -> None:
        assert SAVE in _callees(tmp_path, "r.items.forEach((i) => i.save());")

    def test_over_a_method_result(self, tmp_path: Path) -> None:
        assert SAVE in _callees(tmp_path, "r.all().forEach((i) => i.save());")

    def test_over_this_field(self, tmp_path: Path) -> None:
        app = (
            "import 'repo.dart';\nclass App {\n  final List<Repo> repos = [];\n"
            "  void run() {\n    this.repos.map((p) => p.close());\n  }\n}\n"
        )
        assert CLOSE in _callees_of(tmp_path, app)

    def test_over_a_bare_field(self, tmp_path: Path) -> None:
        app = (
            "import 'repo.dart';\nclass App {\n  final List<Repo> repos = [];\n"
            "  void run() {\n    repos.forEach((p) => p.close());\n  }\n}\n"
        )
        assert CLOSE in _callees_of(tmp_path, app)

    def test_a_local_hides_a_field_of_the_same_name(self, tmp_path: Path) -> None:
        app = (
            "import 'repo.dart';\nclass App {\n  final List<Repo> repos = [];\n"
            "  void run(List<Item> repos) {\n    repos.forEach((p) => p.save());\n  }\n}\n"
        )
        callees = _callees_of(tmp_path, app)
        assert SAVE in callees
        assert CLOSE not in callees

    def test_an_outer_closure_parameter_types_an_inner_receiver(self, tmp_path: Path) -> None:
        body = "repos.forEach((r2) => r2.items.forEach((i) => i.save()));"
        assert SAVE in _callees(tmp_path, body)

    def test_a_written_type_still_binds(self, tmp_path: Path) -> None:
        assert CLOSE in _callees(tmp_path, "repos.forEach((Repo p) => p.close());")

    def test_a_written_type_wins_over_the_container(self, tmp_path: Path) -> None:
        assert _callees(tmp_path, "repos.forEach((Item p) => p.save());") == {SAVE}

    @pytest.mark.parametrize(
        "body",
        [
            "repos.fold(0, (acc, p) => p.close());",
            "repos.reduce((a, b) => a.close());",
            "repos.forEach((a, b) => a.close());",
        ],
    )
    def test_a_callback_of_another_shape_binds_nothing(self, tmp_path: Path, body: str) -> None:
        assert CLOSE not in _callees(tmp_path, body)

    def test_an_unknown_receiver_binds_nothing(self, tmp_path: Path) -> None:
        assert CLOSE not in _callees(tmp_path, "mystery.forEach((p) => p.close());")

    def test_a_map_binds_nothing(self, tmp_path: Path) -> None:
        body = "m.forEach((p) => p.close());"
        assert CLOSE not in _callees(tmp_path, body, "Map<String, Repo> m")

    def test_a_nested_generic_binds_nothing(self, tmp_path: Path) -> None:
        body = "xs.forEach((p) => p.close());"
        assert CLOSE not in _callees(tmp_path, body, "List<List<Repo>> xs")

    def test_a_repo_classs_own_map_binds_nothing(self, tmp_path: Path) -> None:
        """``r.map`` is not ``Iterable.map``: ``r`` is a ``Repo``, not a container."""
        repo = _REPO.replace(
            "  void close() {}", "  void map(void Function(Repo) f) {}\n  void close() {}"
        )
        app = (
            "import 'repo.dart';\nclass App {\n  void run(Repo r) {\n"
            "    r.map((x) => x.close());\n  }\n}\n"
        )
        assert CLOSE not in _callees_of(tmp_path, app, repo)

    def test_the_parameter_lives_only_inside_its_literal(self, tmp_path: Path) -> None:
        """A later local named ``p`` keeps its own reading, not the closure's."""
        body = "repos.forEach((p) {\n  p.close();\n});\nItem p = Item();\np.save();"
        callees = _callees(tmp_path, body)
        assert CLOSE in callees
        assert SAVE in callees

    def test_sibling_closures_each_keep_their_own(self, tmp_path: Path) -> None:
        body = "repos.forEach((p) {\n  p.close();\n});\nr.items.forEach((p) {\n  p.save();\n});"
        assert {CLOSE, SAVE} <= _callees(tmp_path, body)

    def test_a_closure_parameter_hides_a_local_of_its_name(self, tmp_path: Path) -> None:
        """``p`` is the closure's, even where the body also declares an ``Item p``."""
        body = "Item p = Item();\nmystery.forEach((p) {\n  p.save();\n});"
        assert SAVE not in _callees(tmp_path, body)

    def test_the_loop_variable_after_it_is_unaffected(self, tmp_path: Path) -> None:
        body = "repos.forEach((p) => p.close());\nfor (final p in repos) {\n  p.close();\n}"
        assert CLOSE in _callees(tmp_path, body)


class TestChainedCallbackHead:
    """``p.items.save()`` inside ``(p) => ...`` heads the chain with the closure's ``p``."""

    _CLASSES = (
        "class Item {\n  void save() {}\n}\n"
        "class Box {\n  void save() {}\n}\n"
        "class Holder {\n  final Item items = Item();\n}\n"
        "class Shelf {\n  final Box items = Box();\n}\n"
    )

    def test_an_outer_local_of_the_name_does_not_head_the_chain(self, tmp_path: Path) -> None:
        app = (
            "import 'repo.dart';\nclass App {\n  void run(Shelf p, List<Holder> hs) {\n"
            "    hs.forEach((p) => p.items.save());\n  }\n}\n"
        )
        callees = _callees_of(tmp_path, app, self._CLASSES)
        assert "repo.dart::Box::save" not in callees
        assert "repo.dart::Item::save" in callees

    def test_an_outer_field_of_the_name_does_not_head_the_chain(self, tmp_path: Path) -> None:
        app = (
            "import 'repo.dart';\nclass App {\n  final Shelf p = Shelf();\n"
            "  void run(List<Holder> hs) {\n    hs.forEach((p) => p.items.save());\n  }\n}\n"
        )
        callees = _callees_of(tmp_path, app, self._CLASSES)
        assert "repo.dart::Box::save" not in callees
        assert "repo.dart::Item::save" in callees
