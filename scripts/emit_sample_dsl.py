"""Emit sample Structurizr DSL for the parse check, with no index needed.

The unit tests prove the emitter's own invariants — brace balance, no dangling
references, stable identifiers. They cannot prove the result is *valid
Structurizr*, because only Structurizr's parser knows that. This writes a
model exercising every construct the emitter can produce so CI can feed it to
the real parser.

    python scripts/emit_sample_dsl.py <output-dir>

Writes ``workspace.dsl`` (standalone) and a ``fragment/`` directory holding
``repowise-model.dsl`` plus a host workspace that includes it.
"""

from __future__ import annotations

import sys
from pathlib import Path

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
from repowise.server.services.c4_builder.structurizr import system_identifier, to_dsl


def _sample_model() -> C4Model:
    """A model with every construct: nesting, externals, health, layers, a tour.

    Names deliberately include every awkward case we have actually been bitten
    by, because the parser is the only thing that can tell us they survive:

    * a component called ``(root)`` and a path that needs slugging;
    * two containers, and two components inside one container, whose leaf
      directory is the same word;
    * two dependencies presenting under the same display name, and one
      presenting as the repository itself;
    * two layer names that reduce to the same slug, and one carrying a quote;
    * a tour title and a description holding newlines;
    * non-ASCII names, which sanitise to nothing on their own.

    Every one of these produced a file the parser rejected at some point. A
    case that is not here is a case CI does not check.
    """
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
    empty = Container(
        id="pkg:tools",
        name="tools",
        path="tools",
        language="",
        file_count=1,
        symbol_count=0,
    )
    # Same leaf directory as ``packages/core``: a monorepo shape that emitted
    # two `container "core"` blocks and was rejected outright.
    other_core = Container(
        id="pkg:services/core",
        name="core",
        path="services/core",
        language="go",
        file_count=8,
        symbol_count=20,
    )
    # A name that sanitises to nothing on its own, so it leans on the allocator.
    unicode_pkg = Container(
        id="pkg:пакет",
        name="пакет",
        path="пакет",
        language="python",
        file_count=2,
        symbol_count=3,
    )
    components = {
        core.id: [
            Component(
                id="cmp:packages/core#root",
                name="(root)",
                path="packages/core",
                container_id=core.id,
                file_count=2,
                symbol_count=4,
            ),
            Component(
                id="cmp:packages/core/ingestion",
                name="ingestion",
                path="packages/core/ingestion",
                container_id=core.id,
                file_count=30,
                symbol_count=200,
            ),
        ],
        web.id: [
            Component(
                id="cmp:packages/web/app",
                name="app",
                path="packages/web/app",
                container_id=web.id,
                file_count=12,
                symbol_count=60,
            ),
            # ``src`` and ``lib`` are both pass-through directories, so these
            # two both present as "health" inside one container.
            Component(
                id="cmp:packages/web/src/health",
                name="health",
                path="packages/web/src/health",
                container_id=web.id,
                file_count=3,
                symbol_count=9,
            ),
            Component(
                id="cmp:packages/web/lib/health",
                name="health",
                path="packages/web/lib/health",
                container_id=web.id,
                file_count=2,
                symbol_count=5,
            ),
        ],
        empty.id: [],
        other_core.id: [],
        unicode_pkg.id: [],
    }
    return C4Model(
        system=System(id="sys:abc123", name="sample-repo", description="A demo"),
        people=[
            Person(
                id="person:cli",
                name="CLI user",
                description="Runs commands from a terminal",
                kind="cli",
            )
        ],
        containers=[core, web, empty, other_core, unicode_pkg],
        components_by_container=components,
        external_systems=[
            ExternalSystemView(
                id="ext:fastapi",
                name="fastapi",
                display_name="FastAPI",
                category="framework",
                ecosystem="pypi",
                version="0.110",
            ),
            ExternalSystemView(
                id="ext:@scope/pkg",
                name="@scope/pkg",
                display_name="@scope/pkg",
                category="library",
                ecosystem="npm",
                version="^2.0",
            ),
            # These three all present as "React", which the parser refuses as
            # three top-level elements sharing a name.
            ExternalSystemView(
                id="ext:react",
                name="react",
                display_name="React",
                category="library",
                ecosystem="npm",
            ),
            ExternalSystemView(
                id="ext:@xyflow/react",
                name="@xyflow/react",
                display_name="React",
                category="library",
                ecosystem="npm",
            ),
            # And this one presents as the repository itself.
            ExternalSystemView(
                id="ext:sample-repo",
                name="sample-repo-client",
                display_name="sample-repo",
                category="library",
                ecosystem="npm",
            ),
            # No category, ecosystem or version: an empty description literal.
            ExternalSystemView(
                id="ext:bare", name="bare", display_name="bare", category="", ecosystem=""
            ),
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
            Relation(
                source_id=empty.id,
                target_id=core.id,
                label="calls",
                edge_count=1,
                edge_types=("calls",),
                coupling="loose",
            ),
        ],
        component_relations=[
            Relation(
                source_id="cmp:packages/web/app",
                target_id="cmp:packages/core/ingestion",
                label="imports",
                edge_count=12,
                edge_types=("imports",),
                coupling="moderate",
            ),
            Relation(
                source_id="cmp:packages/core#root",
                target_id="ext:@scope/pkg",
                label="imports",
                edge_count=2,
                edge_types=("imports",),
                coupling="loose",
            ),
        ],
        box_signals={
            core.id: BoxSignals(
                hotspot_count=3,
                dead_count=1,
                layers=("Domain", "Ingestion"),
                primary_owner='Ada "Countess" Lovelace',
                primary_owner_pct=62.5,
                min_bus_factor=1,
            ),
            web.id: BoxSignals(layers=("Data Access",)),
            # Reduces to the same slug as "Data Access" did.
            other_core.id: BoxSignals(layers=("Data-Access",)),
            # A quote has to survive into both the tag and the view filter.
            unicode_pkg.id: BoxSignals(layers=('Presentation "UI"',)),
            "cmp:packages/core/ingestion": BoxSignals(hotspot_count=2, layers=("Ingestion",)),
        },
        tour=[
            TourStep(
                order=1,
                title="Start at the CLI\nnot a second comment line",
                reason="Every run enters here",
                target_path="packages/core/cli.py",
                layer_name="Domain",
            ),
            TourStep(
                order=2,
                title="Then ingestion",
                description="Where files\nbecome symbols",
            ),
        ],
    )


def main(destination: Path) -> None:
    model = _sample_model()
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "workspace.dsl").write_text(
        to_dsl(model, standalone=True, include_components=True), encoding="utf-8"
    )

    fragment_dir = destination / "fragment"
    fragment_dir.mkdir(parents=True, exist_ok=True)
    (fragment_dir / "repowise-model.dsl").write_text(
        to_dsl(model, include_components=True), encoding="utf-8"
    )
    # The host workspace a user would write, with the identifier the CLI tells
    # them to use — so this also checks the instructions we print are correct.
    (fragment_dir / "workspace.dsl").write_text(
        'workspace "host" {\n'
        "    !include repowise-model.dsl\n"
        "\n"
        "    views {\n"
        f"        systemContext {system_identifier(model, include_components=True)} {{\n"
        "            include *\n"
        "            autolayout lr\n"
        "        }\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    print(f"wrote {destination / 'workspace.dsl'}")
    print(f"wrote {fragment_dir / 'repowise-model.dsl'}")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "dsl-sample"))
