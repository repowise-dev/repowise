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

from ..models import Component, Container, ExternalSystemView, Person
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


def write_person(writer: Writer, person: Person, identifier: str) -> None:
    writer.line(f"{identifier} = person {quote(person.name)} {quote(person.description)}")


def write_external(writer: Writer, external: ExternalSystemView, identifier: str) -> None:
    header = (
        f"{identifier} = softwareSystem {quote(external.display_name or external.name)} "
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
) -> None:
    """One ``container`` block, with its components nested inside it.

    An empty container is written as a one-line element rather than an empty
    block — the shape a person would write by hand.
    """
    header = (
        f"{identifier} = container {quote(container.name)} "
        f"{quote(container_description(container))}"
    )
    if container.language:
        header += f" {quote(container.language)}"

    nested = [c for c in components if c.id in component_identifiers]
    if not nested:
        writer.line(header)
        return

    with writer.block(header):
        for component in nested:
            comp_header = (
                f"{component_identifiers[component.id]} = component {quote(component.name)} "
                f"{quote(component_description(component))}"
            )
            writer.line(comp_header)
