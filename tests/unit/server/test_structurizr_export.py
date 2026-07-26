"""Tests for the Structurizr DSL emitter.

The emitter takes dataclasses and returns text, so everything here runs
without a database. The properties that matter to a user who commits the
output are determinism and identifier stability — those get their own tests
rather than being implied by a golden file.
"""

from __future__ import annotations

import re

import pytest

from repowise.server.services.c4_builder.models import (
    C4Model,
    Component,
    Container,
    ExternalSystemView,
    Person,
    Relation,
    System,
)
from repowise.server.services.c4_builder.structurizr import to_dsl
from repowise.server.services.c4_builder.structurizr.identifiers import (
    identifiers_for,
    sanitize,
)


def _model(**overrides) -> C4Model:
    core = Container(
        id="pkg:packages/core",
        name="core",
        path="packages/core",
        language="python",
        file_count=120,
        symbol_count=800,
        hotspot_count=3,
        dead_count=1,
    )
    web = Container(
        id="pkg:packages/web",
        name="web",
        path="packages/web",
        language="typescript",
        file_count=40,
        symbol_count=150,
    )
    defaults = dict(
        system=System(id="sys:repo-1", name="repowise", description="Docs engine"),
        people=[
            Person(
                id="person:cli",
                name="CLI user",
                description="Runs commands from a terminal",
                kind="cli",
            )
        ],
        containers=[core, web],
        components_by_container={
            core.id: [
                Component(
                    id="cmp:packages/core/ingestion",
                    name="ingestion",
                    path="packages/core/ingestion",
                    container_id=core.id,
                    file_count=30,
                    symbol_count=200,
                ),
                Component(
                    id="cmp:packages/core#root",
                    name="(root)",
                    path="packages/core",
                    container_id=core.id,
                    file_count=2,
                    symbol_count=4,
                ),
            ],
            web.id: [],
        },
        external_systems=[
            ExternalSystemView(
                id="ext:fastapi",
                name="fastapi",
                display_name="FastAPI",
                category="framework",
                ecosystem="pypi",
                version="0.110",
            )
        ],
        container_relations=[
            Relation(
                source_id=web.id,
                target_id=core.id,
                label="imports",
                edge_count=60,
                edge_types=("imports",),
                coupling="tight",
            ),
            Relation(
                source_id=core.id,
                target_id="ext:fastapi",
                label="imports",
                edge_count=4,
                edge_types=("imports",),
                coupling="loose",
            ),
        ],
        component_relations=[
            Relation(
                source_id="cmp:packages/core#root",
                target_id="cmp:packages/core/ingestion",
                label="calls",
                edge_count=12,
                edge_types=("calls",),
                coupling="moderate",
            )
        ],
    )
    defaults.update(overrides)
    return C4Model(**defaults)


# ---------------------------------------------------------------------------
# Identifiers
# ---------------------------------------------------------------------------


def test_sanitize_keeps_only_legal_characters() -> None:
    assert sanitize("pkg:packages/core") == "pkg_packages_core"
    assert sanitize("cmp:packages/core#root") == "cmp_packages_core_root"
    assert sanitize("ext:@scope/pkg") == "ext_scope_pkg"


def test_an_identifier_never_starts_with_a_digit() -> None:
    identifier = sanitize("2fa/handler.py")
    assert not identifier[0].isdigit()


def test_colliding_ids_both_get_a_suffix_derived_from_the_id() -> None:
    """Not from iteration order — that is what makes re-exports churn."""
    mapping = identifiers_for(["a/b", "a-b", "a.b"])
    assert len(set(mapping.values())) == 3
    # The same set in any order produces the same answer.
    assert mapping == identifiers_for(["a.b", "a-b", "a/b"])
    # And nobody keeps the bare slug, so which one "won" cannot change.
    assert "a_b" not in mapping.values()


def test_adding_an_element_never_renames_an_existing_one() -> None:
    before = identifiers_for(["pkg:packages/core", "pkg:packages/web"])
    after = identifiers_for(["pkg:packages/core", "pkg:packages/web", "pkg:packages/cli"])
    for raw, identifier in before.items():
        assert after[raw] == identifier


def test_identifiers_are_unique_across_a_large_scope() -> None:
    raw = [f"cmp:packages/core/{n}" for n in ("a b", "a-b", "a_b", "a/b", "a.b")]
    mapping = identifiers_for(raw)
    assert len(set(mapping.values())) == len(raw)


# ---------------------------------------------------------------------------
# Emission
# ---------------------------------------------------------------------------


def test_fragment_is_a_bare_model_block() -> None:
    dsl = to_dsl(_model())
    assert dsl.startswith("#")
    assert "\nmodel {\n" in dsl
    # No workspace or views block of its own — only the illustration in the
    # header comment, which is not code.
    code = [line for line in dsl.splitlines() if not line.strip().startswith("#")]
    assert not any("workspace " in line for line in code)
    assert not any("views {" in line for line in code)


def test_fragment_explains_how_to_use_itself() -> None:
    """Someone who never saw the terminal output still knows what to do."""
    dsl = to_dsl(_model())
    assert "!include repowise-model.dsl" in dsl
    assert "do not hand-edit" in dsl
    # The include only parses inside the workspace block, so the snippet has
    # to show that rather than the bare line.
    assert 'workspace "your name" {' in dsl


def test_there_is_no_timestamp_in_the_output() -> None:
    """A timestamp would make every regeneration a diff."""
    dsl = to_dsl(_model(), standalone=True, include_components=True)
    assert not re.search(r"\d{4}-\d{2}-\d{2}", dsl)
    assert not re.search(r"\d{2}:\d{2}:\d{2}", dsl)


def test_emission_is_deterministic() -> None:
    model = _model()
    assert to_dsl(model) == to_dsl(model)


def test_containers_and_externals_are_emitted() -> None:
    dsl = to_dsl(_model())
    assert 'container "core" "120 files, 800 symbols" "python"' in dsl
    assert 'softwareSystem "FastAPI" "framework · pypi · 0.110"' in dsl
    assert 'tags "External"' in dsl
    assert 'person "CLI user" "Runs commands from a terminal"' in dsl


def test_components_are_off_by_default_and_nest_when_asked_for() -> None:
    without = to_dsl(_model())
    assert "component " not in without

    with_components = to_dsl(_model(), include_components=True)
    assert 'component "ingestion" "30 files, 200 symbols"' in with_components
    assert 'component "(root)" "2 files, 4 symbols"' in with_components


def test_externals_can_be_left_out() -> None:
    dsl = to_dsl(_model(), include_externals=False)
    assert "FastAPI" not in dsl
    # And the relation pointing at it goes too, rather than dangling.
    assert "ext_fastapi" not in dsl


def test_relationships_carry_the_verb_and_the_coupling_tag() -> None:
    dsl = to_dsl(_model())
    assert "-> " in dsl
    assert '"imports"' in dsl
    assert 'tags "Tight"' in dsl
    assert 'tags "Loose"' in dsl


def test_a_relationship_never_references_an_element_we_did_not_emit() -> None:
    """A dangling reference is a parse error, not a missing arrow."""
    dsl = to_dsl(_model(), include_components=False)
    declared = {line.split(" = ", 1)[0].strip() for line in dsl.splitlines() if " = " in line}
    for line in dsl.splitlines():
        stripped = line.strip()
        if "->" not in stripped or stripped.startswith("#"):
            continue
        source, _, rest = stripped.partition(" -> ")
        target = rest.split(" ", 1)[0].split("{", 1)[0].strip()
        assert source.strip() in declared, f"undeclared source in: {stripped}"
        assert target in declared, f"undeclared target in: {stripped}"


def test_standalone_wraps_the_model_and_adds_views() -> None:
    dsl = to_dsl(_model(), standalone=True, include_components=True)
    assert dsl.count("workspace ") == 1
    assert "views {" in dsl
    assert "systemContext " in dsl
    assert "container " in dsl
    assert "styles {" in dsl
    # The fragment's include instruction would be wrong here.
    assert "!include" not in dsl


def test_a_large_component_view_is_capped_and_says_so() -> None:
    core = Container(
        id="pkg:packages/big",
        name="big",
        path="packages/big",
        language="python",
        file_count=500,
        symbol_count=1,
    )
    components = [
        Component(
            id=f"cmp:packages/big/mod{n:03d}",
            name=f"mod{n:03d}",
            path=f"packages/big/mod{n:03d}",
            container_id=core.id,
            file_count=n,
            symbol_count=n,
        )
        for n in range(40)
    ]
    dsl = to_dsl(
        _model(
            containers=[core],
            components_by_container={core.id: components},
            container_relations=[],
            component_relations=[],
            external_systems=[],
        ),
        standalone=True,
        include_components=True,
    )
    assert "largest of 40 components" in dsl


def test_braces_balance() -> None:
    for standalone in (False, True):
        for components in (False, True):
            dsl = to_dsl(_model(), standalone=standalone, include_components=components)
            body = "\n".join(line for line in dsl.splitlines() if not line.strip().startswith("#"))
            assert body.count("{") == body.count("}"), (standalone, components)


def test_no_line_has_trailing_whitespace() -> None:
    dsl = to_dsl(_model(), standalone=True, include_components=True)
    for line in dsl.splitlines():
        assert line == line.rstrip(), repr(line)


@pytest.mark.parametrize("standalone", [False, True])
def test_an_empty_model_still_emits_valid_structure(standalone: bool) -> None:
    empty = C4Model(
        system=System(id="sys:repo-1", name="empty"),
        people=[],
        containers=[],
        components_by_container={},
        external_systems=[],
        container_relations=[],
        component_relations=[],
    )
    dsl = to_dsl(empty, standalone=standalone)
    assert 'softwareSystem "empty"' in dsl
    body = "\n".join(line for line in dsl.splitlines() if not line.strip().startswith("#"))
    assert body.count("{") == body.count("}")


def test_quoting_survives_a_name_with_quotes_and_newlines() -> None:
    model = _model(system=System(id="sys:repo-1", name='we"rd', description="line one\nline two"))
    dsl = to_dsl(model)
    system_line = next(line for line in dsl.splitlines() if "softwareSystem" in line)
    assert "\n" not in system_line
    assert system_line.count('"') % 2 == 0


def test_the_system_identifier_does_not_depend_on_the_local_repo_id() -> None:
    """Two people exporting the same repo must get the same file.

    The system's node id embeds the local repository UUID, which differs on
    every machine — keying the identifier on it would make every reference in
    the file differ between teammates.
    """
    mine = to_dsl(_model(system=System(id="sys:aaaaaaaa", name="repowise")))
    theirs = to_dsl(_model(system=System(id="sys:bbbbbbbb", name="repowise")))
    assert mine == theirs
    assert "aaaaaaaa" not in mine
