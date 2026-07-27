"""Emit a Structurizr DSL model from an indexed repository.

One entry point, :func:`to_dsl`, which takes the already-built
:class:`~..models.C4Model` dataclass and returns text. Nothing below this
module touches a database — that is what keeps the emitter unit-testable
without infrastructure, and what keeps emission itself close to free.

Two shapes come out of here:

* **A model fragment** (the default). Just the contents of ``model { … }``,
  meant to be ``!include``d from the user's own ``workspace.dsl`` where their
  views, styles and docs live. We own the model, they own the presentation,
  and regenerating can never clobber hand-written work.
* **A standalone workspace** (``standalone=True``), for someone who has
  nothing yet: the same model wrapped in a ``workspace`` block with default
  views so the file opens and renders on its own.

Identifiers are flat and globally unique, so the fragment resolves inside any
host workspace without that workspace having to opt into a particular
identifier mode.
"""

from __future__ import annotations

from ..models import C4Model
from .elements import write_container, write_external, write_person
from .identifiers import identifiers_for
from .relationships import write_relationships
from .views import write_views
from .writer import Writer, quote

__all__ = ["system_identifier", "to_dsl"]

#: Name of the file we tell users to include. Referenced in the header
#: comment so the file explains itself when it arrives without the terminal
#: output that produced it.
DEFAULT_FILENAME = "repowise-model.dsl"


def _reference_map(model: C4Model, *, include_components: bool) -> dict[str, str]:
    """Map every node id to the identifier used to declare and refer to it.

    Identifiers are **flat and globally unique**, which is the DSL's default
    mode. The alternative, ``!identifiers hierarchical``, gives shorter
    component names but makes every reference a dotted path that only resolves
    when the *host* workspace has turned that mode on — and the whole point of
    shipping a fragment is that it drops into a workspace the user already
    owns without them changing anything in it. Uniqueness is handled by
    :func:`~.identifiers.identifiers_for` instead, so nothing is lost but
    brevity.
    """
    # The system's own id is ``sys:<repository uuid>``, which is stable for one
    # machine and different on every other one — two people exporting the same
    # repo would get files that differ in every reference. Key it on the repo
    # name instead, which is the same everywhere. It still goes through the
    # same allocator, so a name that clashes with something else is handled.
    system_key = f"sys:{model.system.name}"
    raw_ids = (
        [system_key]
        + [p.id for p in model.people]
        + [e.id for e in model.external_systems]
        + [c.id for c in model.containers]
    )
    if include_components:
        raw_ids += [
            component.id
            for container in model.containers
            for component in model.components_by_container.get(container.id, [])
        ]
    references = identifiers_for(raw_ids)
    references[model.system.id] = references.pop(system_key)
    return references


def _display_names(model: C4Model, externals: list) -> dict[str, str]:
    """Names for the top-level elements, made unique.

    Structurizr rejects two top-level elements sharing a name, and display
    names are not unique: ``react``, ``@xyflow/react`` and
    ``@radix-ui/react-dialog`` can all present as "React". The package name
    behind them always is unique — it is what the builder deduplicates on —
    so a colliding group falls back to it.

    Every member of a colliding group falls back, not just the ones after the
    first, so which package keeps the pretty name cannot depend on ordering.
    """
    counts: dict[str, int] = {}
    for external in externals:
        label = external.display_name or external.name
        counts[label] = counts.get(label, 0) + 1

    names: dict[str, str] = {model.system.id: model.system.name}
    for person in model.people:
        names[person.id] = person.name
    for external in externals:
        label = external.display_name or external.name
        names[external.id] = external.name if counts[label] > 1 else label

    # The repository's own name is a top-level element too, so an external
    # sharing it would collide just as loudly.
    taken: dict[str, int] = {}
    for label in names.values():
        taken[label] = taken.get(label, 0) + 1
    if taken.get(model.system.name, 0) > 1:
        for external in externals:
            if names[external.id] == model.system.name:
                names[external.id] = f"{external.name} ({external.ecosystem or 'external'})"
    return names


def system_identifier(model: C4Model, *, include_components: bool = False) -> str:
    """The identifier the emitted system is declared under.

    Exposed so callers can print a view snippet the user can paste verbatim
    instead of one with a placeholder to fill in.
    """
    return _reference_map(model, include_components=include_components)[model.system.id]


def _write_header(writer: Writer, model: C4Model, *, standalone: bool) -> None:
    """The comment block a person reads first, in their editor or a diff.

    Deliberately carries no timestamp: a timestamp makes every regeneration a
    diff, which destroys the "commit it and watch it change meaningfully"
    workflow this file exists for.
    """
    writer.comment(f"Structurizr DSL model for {model.system.name}, generated by Repowise.")
    writer.comment()
    writer.comment("Regenerating overwrites this file — do not hand-edit it.")
    if not standalone:
        writer.comment("It holds the model only; your views, styles and docs stay in")
        writer.comment("your own workspace.dsl. Include it from inside the workspace")
        writer.comment("block — the parser rejects an include that sits outside one:")
        writer.comment()
        writer.comment('    workspace "your name" {')
        writer.comment(f"        !include {DEFAULT_FILENAME}")
        writer.comment()
        writer.comment("        views {")
        writer.comment("            ...")
        writer.comment("        }")
        writer.comment("    }")
    writer.comment()


def _write_tour(writer: Writer, model: C4Model) -> None:
    """The curated reading order, as a comment.

    Structurizr has no concept of a guided tour and there is no honest way to
    force one into a view, but the order is one of the few things in this file
    a person could not have derived themselves — so it goes at the top where
    it is read, rather than being dropped.
    """
    if not model.tour:
        return
    writer.comment("Suggested reading order:")
    for step in model.tour:
        location = f" — {step.target_path}" if step.target_path else ""
        writer.comment(f"  {step.order}. {step.title}{location}")
        detail = step.reason or step.description
        if detail:
            writer.comment(f"     {' '.join(detail.split())}")
    writer.comment()


def to_dsl(
    model: C4Model,
    *,
    standalone: bool = False,
    include_components: bool = False,
    include_externals: bool = True,
    include_metadata: bool = True,
) -> str:
    """Render *model* as Structurizr DSL.

    ``include_components`` is off by default. In a hand-curated Structurizr
    model a component is a grouping somebody chose; ours is a directory, and
    the architects most likely to want this export are exactly the readers who
    would notice the difference.

    ``include_metadata`` carries the health, ownership and layer tags. On by
    default because it is the part no other tool ships; turn it off for a
    plain C4 model.
    """
    writer = Writer()
    _write_header(writer, model, standalone=standalone)
    if include_metadata:
        _write_tour(writer, model)

    externals = list(model.external_systems) if include_externals else []
    references = _reference_map(model, include_components=include_components)
    names = _display_names(model, externals)
    if not include_externals:
        for external in model.external_systems:
            references.pop(external.id, None)

    def write_model_body() -> None:
        for person in sorted(model.people, key=lambda p: p.name):
            write_person(writer, person, references[person.id], names[person.id])
        if model.people:
            writer.blank()

        system_header = (
            f"{references[model.system.id]} = softwareSystem {quote(names[model.system.id])}"
        )
        if model.system.description:
            system_header += f" {quote(model.system.description)}"
        with writer.block(system_header):
            for index, container in enumerate(sorted(model.containers, key=lambda c: c.path)):
                if index:
                    writer.blank()
                components = (
                    sorted(
                        model.components_by_container.get(container.id, []),
                        key=lambda c: c.path,
                    )
                    if include_components
                    else []
                )
                write_container(
                    writer,
                    container,
                    references[container.id],
                    components,
                    {c.id: references[c.id] for c in components},
                    model.box_signals if include_metadata else None,
                )

        if externals:
            writer.blank()
            for external in sorted(externals, key=lambda e: e.name):
                write_external(writer, external, references[external.id], names[external.id])

        relations = list(model.container_relations)
        if include_components:
            # Structurizr derives a container→container relationship from any
            # component→component one that crosses a boundary, so emitting our
            # container-level edges as well is a duplicate the parser rejects.
            # Only containers that produced no components need theirs kept.
            componentless = {
                container.id
                for container in model.containers
                if not model.components_by_container.get(container.id)
            }
            relations = list(model.component_relations) + [
                r
                for r in model.container_relations
                if r.source_id in componentless or r.target_id in componentless
            ]
        writer.blank()
        writer.comment("Relationships")
        write_relationships(writer, relations, references)

    if not standalone:
        with writer.block("model"):
            write_model_body()
        return writer.render()

    with writer.block(f"workspace {quote(model.system.name)}"):
        with writer.block("model"):
            write_model_body()
        writer.blank()
        write_views(
            writer,
            model,
            references,
            include_components=include_components,
        )
    return writer.render()
