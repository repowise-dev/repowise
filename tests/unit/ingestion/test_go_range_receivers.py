"""Go ``range`` variables typed from the container they walk.

``for _, sub := range c.commands`` spells no type for ``sub``: it is whatever
the container yields in that position. The scan reads the clauses and the
container declarations; the resolver joins them. Every negative removes one
proof, and must leave the variable untyped rather than guessed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.ingestion.languages.receiver_types import (
    RangeClause,
    range_element_types,
    scan_ranges,
    types_in_span,
)
from tests.unit.ingestion.test_chained_receiver_calls import _edges


def _clauses(text: str) -> list[RangeClause]:
    return list(scan_ranges(text, "go").clauses)


def _containers(text: str) -> dict[str, str | None]:
    return types_in_span(scan_ranges(text, "go").containers, 1, 10_000)


class TestElementTypes:
    """The container table: which position gets which type."""

    @pytest.mark.parametrize(
        ("spelling", "expected"),
        [
            ("[]*Command", (None, "Command")),
            ("[]Rule", (None, "Rule")),
            ("[4]Rule", (None, "Rule")),
            ("...*Command", (None, "Command")),
            ("[]report.Finding", (None, "Finding")),
            ("map[string]*Group", (None, "Group")),
            ("map[Key]Value", ("Key", "Value")),
            ("chan *Job", ("Job", None)),
            ("<-chan Job", ("Job", None)),
            ("chan<- Job", ("Job", None)),
            ("[]string", (None, None)),
        ],
    )
    def test_a_known_shape(self, spelling: str, expected: tuple[str | None, str | None]) -> None:
        assert range_element_types(spelling, "go") == expected

    @pytest.mark.parametrize(
        "spelling",
        ["Commands", "*Command", "[][]Rule", "map[string][]Rule", "[]func()", "string", ""],
    )
    def test_an_unknown_shape_is_refused(self, spelling: str) -> None:
        """A named container, a nested one or a non-container yields nothing."""
        assert range_element_types(spelling, "go") is None

    def test_an_externally_qualified_element_is_refused(self) -> None:
        """``os.FileInfo`` bares to a name a repo type may share."""
        assert range_element_types("[]os.FileInfo", "go", frozenset({"os"})) == (None, None)
        assert range_element_types("[]report.Finding", "go", frozenset({"os"})) == (
            None,
            "Finding",
        )

    def test_other_languages_have_no_table(self) -> None:
        assert range_element_types("[]Rule", "java") is None


class TestClauses:
    def test_both_variables_and_a_field(self) -> None:
        (clause,) = _clauses("for i, sub := range c.commands {")
        assert (clause.key, clause.value, clause.head, clause.member, clause.call) == (
            "i", "sub", "c", "commands", False
        )

    def test_a_single_variable_over_a_name(self) -> None:
        (clause,) = _clauses("for job := range jobs {")
        assert (clause.key, clause.value, clause.head, clause.member) == ("job", "", "jobs", "")

    def test_a_zero_argument_method(self) -> None:
        (clause,) = _clauses("for _, cmd := range c.Commands() {")
        assert (clause.member, clause.call) == ("Commands", True)

    @pytest.mark.parametrize(
        "line",
        [
            "for _, x := range c.a.b {",
            "for _, x := range f().items {",
            "for _, x := range c.Items(1) {",
            "for _, x := range xs[1:] {",
            "for _, x = range xs {",
            "// for _, x := range xs {",
        ],
    )
    def test_a_longer_expression_or_an_assignment_is_not_read(self, line: str) -> None:
        assert _clauses(line) == []


class TestContainers:
    def test_a_parameter_and_a_variadic(self) -> None:
        text = "func f(findings []report.Finding, cmds ...*Command) {"
        assert _containers(text) == {"findings": "[]report.Finding", "cmds": "...*Command"}

    def test_a_var_a_literal_and_a_make(self) -> None:
        text = "var jobs chan *Job\nrs := []Rule{}\nmatches := make([]*Command, 0)\n"
        assert _containers(text) == {"jobs": "chan *Job", "rs": "[]Rule", "matches": "[]*Command"}

    def test_a_struct_field_belongs_to_no_body(self) -> None:
        """A field line is a ``member``: class scope only, never a local."""
        scan = scan_ranges("type C struct {\n\tcommands []*Command `json:\"c\"`\n}\n", "go")
        (field,) = scan.containers
        assert (field.name, field.type_name, field.member) == ("commands", "[]*Command", True)


_COMMAND = (
    "go",
    "package main\n\n"
    "type Group struct{}\n\n"
    "func (g *Group) Title() string { return \"\" }\n\n"
    "type Command struct {\n"
    "\tcommands []*Command\n"
    "\tgroups   map[string]*Group\n"
    "\tnamed    Commands\n"
    "}\n\n"
    "type Commands []*Command\n\n"
    "func (c *Command) Commands() []*Command { return c.commands }\n"
    "func (c *Command) Named() Commands { return c.named }\n"
    "func (c *Command) HasAlias(s string) bool { return false }\n\n",
)


# The rest of a one-loop body: ``sub.HasAlias("x")``, then the loop and function close.
_HAS_ALIAS_BODY_END = '\t\tsub.HasAlias("x")\n\t}\n}\n'


def _range_edges(tmp_path: Path, body: str) -> set[tuple[str, str]]:
    lang, head = _COMMAND
    edges = _edges(tmp_path, {"a.go": (lang, head + body)})
    return {(caller, callee) for caller, callee, _, _ in edges}


def _callees(tmp_path: Path, body: str) -> set[str]:
    return {callee for _, callee in _range_edges(tmp_path, body)}


class TestResolution:
    def test_a_range_over_a_field(self, tmp_path: Path) -> None:
        body = "func (c *Command) find() {\n\tfor _, sub := range c.commands {\n\t\tsub.HasAlias(\"x\")\n\t}\n}\n"
        assert ("a.go::Command::find", "a.go::Command::HasAlias") in _range_edges(tmp_path, body)

    def test_a_range_over_a_method_result(self, tmp_path: Path) -> None:
        body = "func (c *Command) find() {\n\tfor _, sub := range c.Commands() {\n\t\tsub.HasAlias(\"x\")\n\t}\n}\n"
        assert ("a.go::Command::find", "a.go::Command::HasAlias") in _range_edges(tmp_path, body)

    def test_a_map_value_takes_the_second_variable(self, tmp_path: Path) -> None:
        body = "func (c *Command) find() {\n\tfor k, g := range c.groups {\n\t\tg.Title()\n\t\tk.Title()\n\t}\n}\n"
        assert _range_edges(tmp_path, body) == {("a.go::Command::find", "a.go::Group::Title")}

    def test_a_parameter_slice(self, tmp_path: Path) -> None:
        body = "func filter(cmds []*Command) {\n\tfor i, x := range cmds {\n\t\tx.HasAlias(\"x\")\n\t}\n}\n"
        assert ("a.go::filter", "a.go::Command::HasAlias") in _range_edges(tmp_path, body)

    def test_a_nested_range_over_an_outer_variable(self, tmp_path: Path) -> None:
        body = (
            "func (c *Command) walk() {\n\tfor _, sub := range c.commands {\n"
            "\t\tfor _, leaf := range sub.commands {\n\t\t\tleaf.HasAlias(\"x\")\n\t\t}\n\t}\n}\n"
        )
        assert ("a.go::Command::walk", "a.go::Command::HasAlias") in _range_edges(tmp_path, body)

    def test_the_index_is_not_the_element(self, tmp_path: Path) -> None:
        body = "func filter(cmds []*Command) {\n\tfor i := range cmds {\n\t\ti.HasAlias(\"x\")\n\t}\n}\n"
        assert not _callees(tmp_path, body) & {"a.go::Command::HasAlias"}

    @pytest.mark.parametrize(
        "loop",
        [
            "for _, sub := range c.named {",
            "for _, sub := range c.Named() {",
            "for _, sub := range named {",
        ],
    )
    def test_a_named_container_type_walks_its_declared_container(
        self, tmp_path: Path, loop: str
    ) -> None:
        """``type Commands []*Command`` makes a range over a ``Commands`` yield a ``Command``."""
        body = f"func (c *Command) find(named Commands) {{\n\t{loop}\n" + _HAS_ALIAS_BODY_END
        assert ("a.go::Command::find", "a.go::Command::HasAlias") in _range_edges(tmp_path, body)

    def test_a_named_type_declared_in_another_file_of_the_package(self, tmp_path: Path) -> None:
        """gin's shape: ``engine.trees`` is a ``methodTrees`` declared beside ``node``, and
        each tree's ``root`` field is read to the method."""
        files = {
            "tree.go": (
                "go",
                "package gin\n\ntype node struct{}\n\n"
                "func (n *node) getValue(path string) int { return 0 }\n\n"
                "type methodTree struct {\n\tmethod string\n\troot   *node\n}\n\n"
                "type methodTrees []methodTree\n",
            ),
            "gin.go": (
                "go",
                "package gin\n\ntype Engine struct {\n\ttrees methodTrees\n}\n\n"
                "func (engine *Engine) handle(p string) {\n"
                "\tfor _, tree := range engine.trees {\n"
                "\t\tif v := tree.root.getValue(p); v > 0 {\n\t\t\treturn\n\t\t}\n\t}\n}\n",
            ),
        }
        edges = {(caller, callee) for caller, callee, _, _ in _edges(tmp_path, files)}
        assert ("gin.go::Engine::handle", "tree.go::node::getValue") in edges

    @pytest.mark.parametrize(
        "declaration",
        [
            # Another named type, not a container spelled on the line.
            "type Named Commands",
            # A generic type: its element is a type parameter.
            "type Named[T any] []T",
            # A struct of the name walks nothing.
            "type Named struct{}",
        ],
    )
    def test_a_named_type_that_spells_no_container_is_refused(
        self, tmp_path: Path, declaration: str
    ) -> None:
        body = (
            f"{declaration}\n\n"
            "func find(xs Named) {\n\tfor _, sub := range xs {\n\t\tsub.HasAlias(\"x\")\n\t}\n}\n"
        )
        assert not _callees(tmp_path, body) & {"a.go::Command::HasAlias"}

    def test_a_named_type_of_another_package_is_refused(self, tmp_path: Path) -> None:
        """An unqualified type name is its own package's: a sibling directory's does not answer."""
        lang, head = _COMMAND
        files = {
            "a.go": (
                lang,
                head + "func find(xs Listed) {\n\tfor _, sub := range xs {\n" + _HAS_ALIAS_BODY_END,
            ),
            "other/listed.go": ("go", "package other\n\ntype Listed []*Command\n"),
        }
        callees = {callee for _, callee, _, _ in _edges(tmp_path, files)}
        assert "a.go::Command::HasAlias" not in callees

    def test_a_loop_variable_does_not_retype_the_receiver_body_wide(self, tmp_path: Path) -> None:
        """A body is one scope, so the loop's ``c`` must not cost the method's."""
        body = (
            "func (c *Command) run(xs Commands) {\n\tfor _, c := range xs {\n\t\t_ = c\n\t}\n"
            "\tc.HasAlias(\"x\")\n}\n"
        )
        assert ("a.go::Command::run", "a.go::Command::HasAlias") in _range_edges(tmp_path, body)

    def test_two_loops_typing_one_name_differently_refuse_it(self, tmp_path: Path) -> None:
        body = (
            "func (c *Command) find() {\n\tfor _, x := range c.commands {\n\t}\n"
            "\tfor _, x := range c.groups {\n\t\tx.HasAlias(\"x\")\n\t}\n}\n"
        )
        assert not _callees(tmp_path, body) & {"a.go::Command::HasAlias"}

    def test_an_external_element_does_not_bind_a_repo_type_of_its_name(
        self, tmp_path: Path
    ) -> None:
        files = {
            "a.go": (
                "go",
                "package main\n\nimport (\n\t\"os\"\n\t\"path/filepath\"\n)\n\n"
                "type FileInfo struct{}\n\nfunc (f FileInfo) IsDir() bool { return false }\n\n"
                "func list(infos []os.FileInfo, more []filepath.FileInfo) {\n"
                "\tfor _, i := range infos {\n\t\ti.IsDir()\n\t}\n"
                "\tfor _, m := range more {\n\t\tm.IsDir()\n\t}\n}\n",
            )
        }
        callees = {callee for _, callee, _, _ in _edges(tmp_path, files)}
        assert "a.go::FileInfo::IsDir" not in callees

    def test_two_hops_are_refused(self, tmp_path: Path) -> None:
        body = (
            "func origin() *Command { return nil }\n"
            "func find() {\n\tfor _, sub := range origin().commands {\n\t\tsub.HasAlias(\"x\")\n\t}\n}\n"
        )
        assert not _callees(tmp_path, body) & {"a.go::Command::HasAlias"}
