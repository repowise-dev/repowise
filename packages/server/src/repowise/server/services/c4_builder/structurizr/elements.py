"""Emit the ``person`` / ``softwareSystem`` / ``container`` / ``component`` blocks.

Descriptions come from what the index already knows — file and symbol counts,
the dominant language, the dependency's ecosystem. Nothing here invents prose
or calls a model: a wrong description in someone's committed architecture file
is worse than a plain one.

Structurizr tags every element with its own type (``Container``, ``Component``
…), so those are not repeated here. ``External`` is the exception: it is a
convention rather than something the parser adds, and it is what a user's
existing styles will already key on.
"""

from __future__ import annotations

from ..models import BoxSignals, Component, Container, ExternalSystemView, Person
from .metadata import properties_for, tags_for, write_metadata
from .writer import Writer, quote


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _size_description(file_count: int, symbol_count: int) -> str:
    parts = [_plural(file_count, "file")]
    if symbol_count:
        parts.append(_plural(symbol_count, "symbol"))
    return ", ".join(parts)


def container_description(container: Container) -> str:
    return _size_description(container.file_count, container.symbol_count)


def component_description(component: Component) -> str:
    return _size_description(component.file_count, component.symbol_count)


def external_description(external: ExternalSystemView) -> str:
    """Ecosystem and version, when the manifest gave us them."""
    parts = [external.category, external.ecosystem, external.version]
    return " · ".join(p for p in parts if p)


def write_person(writer: Writer, person: Person, identifier: str, name: str | None = None) -> None:
    writer.line(f"{identifier} = person {quote(name or person.name)} {quote(person.description)}")


def write_external(
    writer: Writer,
    external: ExternalSystemView,
    identifier: str,
    name: str | None = None,
) -> None:
    """One external dependency.

    *name* is resolved by the caller, which is the only place that can see
    whether two packages present under the same display name.
    """
    header = (
        f"{identifier} = softwareSystem {quote(name or external.display_name or external.name)} "
        f"{quote(external_description(external))}"
    )
    with writer.block(header):
        writer.line('tags "External"')


def write_container(
    writer: Writer,
    container: Container,
    identifier: str,
    components: list[Component],
    component_identifiers: dict[str, str],
    signals: dict[str, BoxSignals] | None = None,
) -> None:
    """One ``container`` block, with its components nested inside it.

    A container with nothing to nest and nothing to say is written as a
    one-line element rather than an empty block — the shape a person would
    write by hand.
    """
    signals = signals or {}
    header = (
        f"{identifier} = container {quote(container.name)} "
        f"{quote(container_description(container))}"
    )
    if container.language:
        header += f" {quote(container.language)}"

    nested = [c for c in components if c.id in component_identifiers]
    own_metadata = _has_metadata(signals.get(container.id))
    if not nested and not own_metadata:
        writer.line(header)
        return

    with writer.block(header):
        write_metadata(writer, signals.get(container.id))
        for component in nested:
            write_component(
                writer,
                component,
                component_identifiers[component.id],
                signals.get(component.id),
            )


def write_component(
    writer: Writer,
    component: Component,
    identifier: str,
    signals: BoxSignals | None = None,
) -> None:
    header = (
        f"{identifier} = component {quote(component.name)} "
        f"{quote(component_description(component))}"
    )
    if not _has_metadata(signals):
        writer.line(header)
        return
    with writer.block(header):
        write_metadata(writer, signals)


def _has_metadata(signals: BoxSignals | None) -> bool:
    return bool(signals is not None and (tags_for(signals) or properties_for(signals)))
