"""Default views and styles — standalone workspaces only.

The fragment stays presentation-free on purpose: views, styles and docs are
the user's, and regenerating our model must never touch them. These exist for
the person who has no workspace yet and wants a picture from one command.
"""

from __future__ import annotations

from ..models import C4Model
from .identifiers import identifiers_for
from .metadata import layer_tag
from .writer import Writer, quote

#: Above roughly this many boxes a Structurizr view stops being readable, so a
#: component view is capped and says so in a comment. Silent truncation would
#: read as "this is everything" when it is not.
MAX_COMPONENTS_PER_VIEW = 20


def _view_key(prefix: str, identifier: str) -> str:
    return f"{prefix}_{identifier}"


def write_views(
    writer: Writer,
    model: C4Model,
    references: dict[str, str],
    *,
    include_components: bool,
    include_layer_views: bool = True,
) -> None:
    """Write a ``views`` block: context, containers, and per-container components.

    ``include_layer_views`` follows the caller's metadata flag. The layer views
    filter on tags the metadata pass emits, so with metadata off they would
    parse and then select nothing at all.
    """
    system_ref = references[model.system.id]
    with writer.block("views"):
        with writer.block(f"systemContext {system_ref} {quote('SystemContext')}"):
            # `include *` alone puts every external in here. Structurizr
            # promotes a container→external edge to the system level, so all
            # ~50 npm and pypi packages land in the context view and it stops
            # being a context view — the one diagram whose whole job is to fit
            # on a slide. They are dependencies rather than systems anyone
            # integrates with, and the container view below still shows them.
            writer.line("include *")
            writer.line('exclude "element.tag==External"')
            writer.line("autolayout lr")

        writer.blank()
        with writer.block(f"container {system_ref} {quote('Containers')}"):
            writer.line("include *")
            writer.line("autolayout lr")

        if include_components:
            for container in sorted(model.containers, key=lambda c: c.path):
                components = model.components_by_container.get(container.id, [])
                if not components:
                    continue
                writer.blank()
                container_ref = references[container.id]
                key = _view_key("components", container_ref)
                with writer.block(f"component {container_ref} {quote(key)}"):
                    # Ranked by size: the biggest directories are the ones a
                    # reader is orienting by. Same order the model block uses
                    # for ties, so the file stays diff-stable.
                    ranked = sorted(components, key=lambda c: (-c.file_count, c.path))
                    if len(ranked) > MAX_COMPONENTS_PER_VIEW:
                        writer.comment(
                            f"Showing the {MAX_COMPONENTS_PER_VIEW} largest of "
                            f"{len(ranked)} components — a view this size stops "
                            f"being readable. Add the rest by hand if you need them."
                        )
                        for component in ranked[:MAX_COMPONENTS_PER_VIEW]:
                            writer.line(f"include {references[component.id]}")
                    else:
                        writer.line("include *")
                    writer.line("autolayout lr")

        if include_layer_views:
            _write_layer_views(writer, model, references)

        writer.blank()
        with writer.block("styles"):
            with writer.block('element "External"'):
                writer.line("background #999999")
                writer.line("color #ffffff")
            # The health colours. Which one wins on an element carrying both is
            # decided by the order of tags *on the element*, not the order these
            # rules are declared — `tags_for` emits Hotspot then Dead, so a dead
            # hotspot reads as dead, the more actionable of the two. Reordering
            # these blocks would change nothing.
            with writer.block('element "Hotspot"'):
                writer.line("background #b5432f")
                writer.line("color #ffffff")
            with writer.block('element "Dead"'):
                writer.line("background #6b6b6b")
                writer.line("color #ffffff")
            with writer.block('relationship "Tight"'):
                writer.line("thickness 4")
            with writer.block('relationship "Loose"'):
                writer.line("style dashed")


def _write_layer_views(writer: Writer, model: C4Model, references: dict[str, str]) -> None:
    """One container view per curated layer, filtered by its tag.

    This is the grouping that is ours rather than C4's, and a filtered view is
    the only way it survives into someone else's toolchain.

    Layer names are curated prose, so neither the key nor the filter can take
    them as they are. Keys go through the same collision-safe allocator the
    element identifiers use, because two names that reduce to one key is a
    workspace Structurizr refuses. The filter goes through ``quote`` and is
    built from :func:`layer_tag`, so it always spells the tag the elements
    were actually given.
    """
    layers = sorted({layer for signals in model.box_signals.values() for layer in signals.layers})
    if not layers:
        return
    system_ref = references[model.system.id]
    keys = identifiers_for(layers)
    for layer in layers:
        writer.blank()
        key = _view_key("layer", keys[layer])
        # The key is slugged for uniqueness, so it reads as
        # `layer_Ingestion_and_Reasoning_Pipeline` in the view picker. The
        # description is the third positional argument and is what Structurizr
        # actually shows a reader, so the curated name survives to the UI.
        with writer.block(f"container {system_ref} {quote(key)} {quote(layer)}"):
            writer.line(f"include {quote(f'element.tag=={layer_tag(layer)}')}")
            writer.line("autolayout lr")
