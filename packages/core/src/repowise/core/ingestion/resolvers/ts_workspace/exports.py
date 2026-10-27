"""Node ``exports`` maps: flattening condition objects and matching subpaths."""

from __future__ import annotations

from typing import Any

# Condition collapse order: source first (the ``.ts`` file, not a build), then ESM/default, CJS last.
_CONDITION_PRIORITY: tuple[str, ...] = (
    "source",
    "import",
    "default",
    "node",
    "require",
    "types",
    "browser",
)


def _flatten_export_value(value: Any) -> str | None:
    """Collapse a Node ``exports`` entry to one target; ``None`` when blocked or unhandled."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for cond in _CONDITION_PRIORITY:
            inner = value.get(cond)
            if inner is None:
                continue
            flat = _flatten_export_value(inner)
            if flat:
                return flat
        return None
    if isinstance(value, list):
        for item in value:
            flat = _flatten_export_value(item)
            if flat:
                return flat
    return None


def _ordered_export_targets(value: Any) -> tuple[str, ...]:
    """Every target a Node ``exports`` entry can mean, best first.

    The head is :func:`_flatten_export_value`'s choice. The tail (every other
    leaf, ranked conditions first) covers sources published under a condition
    no fixed list names, such as ``@zod/source``.
    """
    ordered: list[str] = []
    head = _flatten_export_value(value)
    if head is not None:
        ordered.append(head)

    def walk(node: Any) -> None:
        if isinstance(node, str):
            if node not in ordered:
                ordered.append(node)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            for cond in _CONDITION_PRIORITY:
                if cond in node:
                    walk(node[cond])
            for cond, inner in node.items():
                if cond not in _CONDITION_PRIORITY:
                    walk(inner)

    walk(value)
    return tuple(ordered)


_DECLARATION_SUFFIXES: tuple[str, ...] = (".d.ts", ".d.mts", ".d.cts")


def _build_exports_map(pkg_data: dict) -> dict[str, tuple[str, ...]]:
    """``{exports_key: (target, ...)}``; a string ``exports`` is ``"."``, keys not starting with ``.`` drop."""
    raw = pkg_data.get("exports")
    if raw is None:
        return {}
    if isinstance(raw, str):
        return {".": (raw,)}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, tuple[str, ...]] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key.startswith("."):
            continue
        # A spare candidate alone must not keep a key: it would answer ahead of the fallbacks.
        if _flatten_export_value(value) is None:
            continue
        out[key] = _ordered_export_targets(value)
    return out


def _match_export_key(
    subpath: str, exports_map: dict[str, tuple[str, ...]]
) -> tuple[str, ...] | None:
    """The candidate targets for *subpath* (``""`` = bare package), best first.

    Exact keys win over wildcards; among wildcards the longest static prefix wins.
    """
    key = "." if subpath == "" else "./" + subpath
    if key in exports_map:
        return exports_map[key]
    best_targets: tuple[str, ...] | None = None
    best_prefix_len = -1
    for pattern, targets in exports_map.items():
        if "*" not in pattern:
            continue
        prefix, _, suffix = pattern.partition("*")
        if not key.startswith(prefix):
            continue
        if suffix and not key.endswith(suffix):
            continue
        captured = (
            key[len(prefix) : len(key) - len(suffix)]
            if suffix
            else key[len(prefix) :]
        )
        if len(prefix) > best_prefix_len:
            # Past the head, a ``*``-less candidate would answer every subpath alike.
            kept = targets[:1] + tuple(t for t in targets[1:] if "*" in t)
            best_targets = tuple(
                t.replace("*", captured, 1) if "*" in t else t for t in kept
            )
            best_prefix_len = len(prefix)
    return best_targets
