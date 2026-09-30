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

    def test_a_named_container_type_is_refused(self, tmp_path: Path) -> None:
        """``Commands`` is a slice, but only a type checker would say so."""
        body = (
            "func (c *Command) find() {\n\tfor _, sub := range c.named {\n\t\tsub.HasAlias(\"x\")\n\t}\n"
            "\tfor _, sub := range c.Named() {\n\t\tsub.HasAlias(\"x\")\n\t}\n}\n"
        )
        assert not _callees(tmp_path, body) & {"a.go::Command::HasAlias"}

    def test_an_unknown_container_shadows_an_outer_declaration(self, tmp_path: Path) -> None:
        """``sub`` inside the loop is not the ``*Command`` parameter it shadows."""
        body = (
            "func run(sub *Command, xs Commands) {\n\tfor _, sub := range xs {\n"
            "\t\tsub.HasAlias(\"x\")\n\t}\n}\n"
        )
        assert not _callees(tmp_path, body) & {"a.go::Command::HasAlias"}

    def test_two_hops_are_refused(self, tmp_path: Path) -> None:
        body = (
            "func origin() *Command { return nil }\n"
            "func find() {\n\tfor _, sub := range origin().commands {\n\t\tsub.HasAlias(\"x\")\n\t}\n}\n"
        )
        assert not _callees(tmp_path, body) & {"a.go::Command::HasAlias"}
