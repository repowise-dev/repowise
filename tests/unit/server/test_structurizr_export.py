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
    BoxSignals,
    C4Model,
    Component,
    Container,
    ExternalSystemView,
    Person,
    Relation,
    System,
    TourStep,
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


# ---------------------------------------------------------------------------
# Health, ownership and layer metadata
# ---------------------------------------------------------------------------


def _signals_model(**overrides) -> C4Model:
    base = _model()
    signals = {
        "pkg:packages/core": BoxSignals(
            hotspot_count=3,
            dead_count=1,
            layers=("Domain", "Ingestion"),
            primary_owner="Ada Lovelace",
            primary_owner_pct=62.5,
            min_bus_factor=1,
        ),
        "pkg:packages/web": BoxSignals(),
        "cmp:packages/core/ingestion": BoxSignals(hotspot_count=2, layers=("Ingestion",)),
    }
    defaults = {"box_signals": signals}
    defaults.update(overrides)
    return C4Model(**{**vars(base), **defaults})


def test_health_rides_along_as_tags() -> None:
    dsl = to_dsl(_signals_model())
    assert '"Hotspot"' in dsl
    assert '"Dead"' in dsl


def test_layer_membership_becomes_a_tag() -> None:
    """The layer grouping is ours; a tag is how it survives into their tool."""
    dsl = to_dsl(_signals_model())
    assert '"Layer: Domain"' in dsl
    assert '"Layer: Ingestion"' in dsl


def test_properties_are_namespaced_so_they_cannot_collide() -> None:
    dsl = to_dsl(_signals_model())
    for key in ("repowise.hotspots", "repowise.owner", "repowise.minBusFactor"):
        assert f'"{key}"' in dsl
    assert '"Ada Lovelace"' in dsl


def test_unknown_ownership_is_omitted_not_zeroed() -> None:
    """An emitted 0 bus factor reads as "nobody owns this", a different claim."""
    dsl = to_dsl(_signals_model())
    web_block = dsl.split('container "web"')[1]
    assert "repowise.owner" not in web_block
    assert "repowise.minBusFactor" not in web_block


def test_counts_are_emitted_even_when_zero() -> None:
    """We counted; zero is an answer, unlike a missing owner."""
    dsl = to_dsl(_signals_model())
    assert '"repowise.hotspots" "0"' in dsl


def test_metadata_can_be_turned_off_for_a_plain_c4_model() -> None:
    dsl = to_dsl(_signals_model(), include_metadata=False)
    assert "repowise." not in dsl
    assert "Layer: " not in dsl
    assert "Hotspot" not in dsl


def test_a_box_with_nothing_to_say_stays_a_one_liner() -> None:
    plain = C4Model(**{**vars(_model()), "box_signals": {}})
    dsl = to_dsl(plain)
    assert 'container "core" "120 files, 800 symbols" "python"\n' in dsl


def test_the_tour_rides_along_as_a_comment() -> None:
    """It cannot be a view, but it is one of the few things we know and C4 does not."""
    model = C4Model(
        **{
            **vars(_model()),
            "tour": [
                TourStep(
                    order=1,
                    title="Start at the CLI",
                    reason="Every run enters here",
                    target_path="packages/cli/main.py",
                ),
                TourStep(
                    order=2, title="Then the parser", description="Where files\nbecome symbols"
                ),
            ],
        }
    )
    dsl = to_dsl(model)
    assert "Suggested reading order:" in dsl
    assert "1. Start at the CLI — packages/cli/main.py" in dsl
    assert "Every run enters here" in dsl
    # A description with a newline must not break out of the comment.
    for line in dsl.splitlines():
        if "become symbols" in line:
            assert line.strip().startswith("#")


def test_layer_views_are_emitted_for_a_standalone_workspace() -> None:
    dsl = to_dsl(_signals_model(), standalone=True)
    assert 'include "element.tag==Layer: Domain"' in dsl
    assert 'element "Hotspot"' in dsl


def test_metadata_does_not_break_brace_balance() -> None:
    for standalone in (False, True):
        for components in (False, True):
            dsl = to_dsl(_signals_model(), standalone=standalone, include_components=components)
            body = "\n".join(line for line in dsl.splitlines() if not line.strip().startswith("#"))
            assert body.count("{") == body.count("}"), (standalone, components)


def test_two_packages_presenting_as_the_same_name_do_not_collide() -> None:
    """Structurizr rejects two top-level elements sharing a name.

    Real case: `react`, `@xyflow/react` and `@radix-ui/react-dialog` all
    present as "React" in the index's display names, so the emitted file was
    rejected outright by the parser.
    """

    def _ext(node_id: str, name: str, display: str) -> ExternalSystemView:
        return ExternalSystemView(
            id=node_id,
            name=name,
            display_name=display,
            category="library",
            ecosystem="npm",
        )

    dsl = to_dsl(
        _model(
            external_systems=[
                _ext("ext:react", "react", "React"),
                _ext("ext:@xyflow/react", "@xyflow/react", "React"),
                _ext("ext:vite", "vite", "Vite"),
            ],
            container_relations=[],
            component_relations=[],
        )
    )
    declared = [
        line.split("softwareSystem ", 1)[1].split('"')[1]
        for line in dsl.splitlines()
        if "= softwareSystem " in line
    ]
    assert len(declared) == len(set(declared)), declared
    # The unique one keeps its pretty name; the colliding pair falls back to
    # the package name, which is unique by construction.
    assert "Vite" in declared
    assert "react" in declared
    assert "@xyflow/react" in declared


def _declared(dsl: str, keyword: str) -> list[str]:
    """The display names of every element declared with *keyword*."""
    return [
        line.split(f"{keyword} ", 1)[1].split('"')[1]
        for line in dsl.splitlines()
        if f"= {keyword} " in line
    ]


def test_two_containers_with_the_same_basename_do_not_collide() -> None:
    """A container name only has to be unique inside its software system.

    Container names are the leaf directory, so any monorepo holding both
    ``apps/api`` and ``services/api`` produced two ``container "api"`` blocks —
    which Structurizr rejects outright, the same way it rejects two externals
    sharing a name.
    """
    def _container(path: str, language: str) -> Container:
        return Container(
            id=f"pkg:{path}",
            name=path.split("/")[-1],
            path=path,
            language=language,
            file_count=3,
            symbol_count=1,
        )

    dsl = to_dsl(
        _model(
            containers=[
                _container("apps/api", "typescript"),
                _container("services/api", "go"),
                _container("apps/web", "typescript"),
            ],
            components_by_container={},
            container_relations=[],
            component_relations=[],
        )
    )
    declared = _declared(dsl, "container")
    assert len(declared) == len(set(declared)), declared
    # The unique one keeps the short name; the colliding pair falls back to the
    # path, which is unique by construction.
    assert "web" in declared
    assert "apps/api" in declared
    assert "services/api" in declared


def test_two_components_in_one_container_with_the_same_basename_do_not_collide() -> None:
    """``src`` and ``lib`` are both pass-through directories.

    So ``packages/ui/src/health`` and ``packages/ui/lib/health`` both name a
    component "health" inside one container, which the parser rejects.
    """
    container = Container(
        id="pkg:packages/ui",
        name="ui",
        path="packages/ui",
        language="typescript",
        file_count=4,
        symbol_count=2,
    )

    def _component(path: str) -> Component:
        return Component(
            id=f"cmp:{path}",
            name=path.split("/")[-1],
            path=path,
            container_id=container.id,
            file_count=2,
            symbol_count=1,
        )

    dsl = to_dsl(
        _model(
            containers=[container],
            components_by_container={
                container.id: [
                    _component("packages/ui/src/health"),
                    _component("packages/ui/lib/health"),
                    _component("packages/ui/src/costs"),
                ]
            },
            container_relations=[],
            component_relations=[],
        ),
        include_components=True,
    )
    declared = _declared(dsl, "component")
    assert len(declared) == len(set(declared)), declared
    assert "costs" in declared
    assert "src/health" in declared
    assert "lib/health" in declared


def test_the_same_component_name_in_two_containers_is_left_alone() -> None:
    """A component name only has to be unique inside its own container.

    Disambiguating across containers would churn names for no reason, and the
    long form is worse to read.
    """
    def _container(path: str) -> Container:
        return Container(
            id=f"pkg:{path}",
            name=path.split("/")[-1],
            path=path,
            language="python",
            file_count=2,
            symbol_count=1,
        )

    one, two = _container("packages/core"), _container("packages/server")

    def _component(container: Container, leaf: str) -> Component:
        path = f"{container.path}/{leaf}"
        return Component(
            id=f"cmp:{path}",
            name=leaf,
            path=path,
            container_id=container.id,
            file_count=2,
            symbol_count=1,
        )

    dsl = to_dsl(
        _model(
            containers=[one, two],
            components_by_container={
                one.id: [_component(one, "health")],
                two.id: [_component(two, "health")],
            },
            container_relations=[],
            component_relations=[],
        ),
        include_components=True,
    )
    assert _declared(dsl, "component") == ["health", "health"]


def test_an_external_sharing_the_repo_name_is_disambiguated() -> None:
    dsl = to_dsl(
        _model(
            system=System(id="sys:abc", name="react"),
            external_systems=[
                ExternalSystemView(
                    id="ext:react",
                    name="react",
                    display_name="react",
                    category="library",
                    ecosystem="npm",
                )
            ],
            container_relations=[],
            component_relations=[],
        )
    )
    declared = [
        line.split("softwareSystem ", 1)[1].split('"')[1]
        for line in dsl.splitlines()
        if "= softwareSystem " in line
    ]
    assert len(declared) == len(set(declared)), declared
