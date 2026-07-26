"""Default views and styles — standalone workspaces only.

The fragment stays presentation-free on purpose: views, styles and docs are
the user's, and regenerating our model must never touch them. These exist for
the person who has no workspace yet and wants a picture from one command.
"""

from __future__ import annotations

from ..models import C4Model
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
) -> None:
    """Write a ``views`` block: context, containers, and per-container components."""
    system_ref = references[model.system.id]
    with writer.block("views"):
        with writer.block(f"systemContext {system_ref} {quote('SystemContext')}"):
            writer.line("include *")
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

        writer.blank()
        with writer.block("styles"):
            with writer.block('element "External"'):
                writer.line("background #999999")
                writer.line("color #ffffff")
            with writer.block('relationship "Tight"'):
                writer.line("thickness 4")
            with writer.block('relationship "Loose"'):
                writer.line("style dashed")
