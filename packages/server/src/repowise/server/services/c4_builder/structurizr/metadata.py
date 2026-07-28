"""Health, ownership and layer membership as tags and properties.

This is the half of the export that is ours. Containers are package manifests
and components are directories — any tool can produce those. What no other
tool has is which files churn, who owns them, and which curated layer they sit
in, and C4 has no vocabulary for any of it.

Tags are the escape hatch. A user writes one filtered view per layer, or
colours hotspots red, using their own styles against tags we merely emit. We
neither build nor maintain the views; we supply the vocabulary.

Two rules:

* **Namespace every property key** (``repowise.*``) so it can never collide
  with something the user set themselves.
* **Omit what is unknown.** Health data is sparse. A missing bus factor
  emitted as ``0`` reads as "nobody owns this", which is a different and much
  more alarming claim than "we do not know".
"""

from __future__ import annotations

from ..models import BoxSignals
from .writer import Writer, quote

#: Prefix on every property key we emit.
_NAMESPACE = "repowise"

#: Prefix on a layer tag, so a layer called "Tight" cannot be mistaken for the
#: coupling tag of the same name.
_LAYER_TAG_PREFIX = "Layer: "


def layer_tag(name: str) -> str:
    """The tag text a layer is emitted under.

    Shared with the view filter in :mod:`.views`: a filter that selects on a
    string the element was never tagged with parses fine and then matches
    nothing, which is harder to spot than a parse error. One function means
    they cannot drift.
    """
    return f"{_LAYER_TAG_PREFIX}{name}"


def tags_for(signals: BoxSignals | None) -> list[str]:
    """Tags for one box, in a stable order.

    ``Hotspot`` and ``Dead`` are deliberately coarse: the count is available
    as a property for anyone who wants it, but a style rule wants a yes/no.
    """
    if signals is None:
        return []
    tags: list[str] = []
    if signals.hotspot_count:
        tags.append("Hotspot")
    if signals.dead_count:
        tags.append("Dead")
    tags.extend(layer_tag(name) for name in signals.layers)
    return tags


def properties_for(signals: BoxSignals | None) -> dict[str, str]:
    """Namespaced properties for one box. Values are strings; DSL has no types.

    A count of zero is emitted — we counted, and zero is the answer. A count we
    never took is omitted: a repo with no churn data at all would otherwise
    read as a repo with no hotspots, which is the clean bill of health the
    omit-what-is-unknown rule exists to prevent.
    """
    if signals is None:
        return {}
    properties: dict[str, str] = {f"{_NAMESPACE}.deadFiles": str(signals.dead_count)}
    if signals.hotspot_count is not None:
        properties[f"{_NAMESPACE}.hotspots"] = str(signals.hotspot_count)
    if signals.primary_owner:
        properties[f"{_NAMESPACE}.owner"] = signals.primary_owner
        if signals.primary_owner_pct is not None:
            properties[f"{_NAMESPACE}.ownerPct"] = f"{signals.primary_owner_pct:g}"
    if signals.min_bus_factor is not None:
        properties[f"{_NAMESPACE}.minBusFactor"] = str(signals.min_bus_factor)
    if signals.layers:
        properties[f"{_NAMESPACE}.layers"] = ", ".join(signals.layers)
    return properties


def write_metadata(
    writer: Writer,
    signals: BoxSignals | None,
    *,
    extra_tags: list[str] | None = None,
) -> bool:
    """Write the ``tags`` line and ``properties`` block. True if anything was.

    Callers use the return value to decide between a one-line element and a
    block, so an element with nothing to say does not get an empty body.
    """
    tags = (extra_tags or []) + tags_for(signals)
    properties = properties_for(signals)
    if not tags and not properties:
        return False

    if tags:
        writer.line("tags " + " ".join(quote(tag) for tag in tags))
    if properties:
        with writer.block("properties"):
            for key in sorted(properties):
                writer.line(f"{quote(key)} {quote(properties[key])}")
    return True
