"""Dynamic-hint extractor for Alpine.js component registration.

Why this exists
===============
Alpine.js wires components into the runtime by *value*, not by import::

    import explorer from './data/explorer.js'
    Alpine.data('explorer', explorer)
    Alpine.store('search', searchStore)
    Alpine.magic('clipboard', () => ...)
    Alpine.directive('tooltip', tooltipFn)

The registration names a string key and passes a JS identifier. The
module that *defines* ``explorer`` exports it solely to be handed to
``Alpine.data`` — there is no other importer that references the export
by name, so the defining file reads as unreachable and the exported
function reads as an unused export. (In Hugo this is the
``docs/assets/js/alpinejs/**`` cluster: ``explorer`` / ``search`` etc.)

Design
======
Pure two-pass regex scan, mirroring the Go reflect extractor
(:mod:`dynamic_hints.go`): first build an ``identifier → defining file``
map from top-level ``function`` / ``const`` / ``class`` declarations across
the repo's JS/TS files, then find every ``Alpine.<verb>('key', ident)``
registration and emit a ``dynamic_uses`` edge from the registering file to
the file that defines ``ident``. No JS parser is pulled in — the patterns
work on partial/minified sources and are cheap.

A name can be defined in more than one file (common for small helpers);
we emit to every definition. Over-emitting a use edge only ever rescues a
false dead-code finding — it never invents one — which is the safe
direction for this pass.
"""

from __future__ import annotations

import re
from pathlib import Path

from .base import DynamicEdge, DynamicHintExtractor

_SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".next", "vendor"}
_JS_EXTS = (".js", ".mjs", ".cjs", ".ts")

# Alpine.data('explorer', explorer) / .store / .magic / .directive — capture
# the identifier passed as the second argument. An inline function/object
# literal (``Alpine.data('x', () => ...)``) has no identifier to resolve and
# is intentionally not matched.
_ALPINE_REGISTER_RE = re.compile(
    r"""Alpine\s*\.\s*(?:data|store|magic|directive)\s*\(\s*"""
    r"""['"][^'"]+['"]\s*,\s*([A-Za-z_$][\w$]*)""",
)

# Top-level definitions that a registration identifier can resolve to.
# ``export`` prefixes and ``default`` are tolerated.
_DEF_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*(?:export\s+)?(?:default\s+)?function\s+([A-Za-z_$][\w$]*)", re.MULTILINE),
    re.compile(r"^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)", re.MULTILINE),
    re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=", re.MULTILINE),
)


class AlpineDynamicHints(DynamicHintExtractor):
    """Emit ``dynamic_uses`` edges from Alpine registrations to their handlers."""

    name = "alpine"

    def extract(self, repo_root: Path) -> list[DynamicEdge]:
        name_to_files: dict[str, list[str]] = {}
        sources: list[tuple[str, str]] = []
        repo_root_resolved = repo_root.resolve()

        for ext in _JS_EXTS:
            for src in self._rglob(repo_root, f"*{ext}"):
                try:
                    rel_path = src.resolve().relative_to(repo_root_resolved)
                except ValueError:
                    continue
                if any(part in _SKIP_DIRS for part in rel_path.parts):
                    continue
                try:
                    text = src.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                rel = rel_path.as_posix()
                # Only files that actually register with Alpine are scanned for
                # registrations in pass 2, but every JS file is a candidate
                # *definition* site.
                sources.append((rel, text))
                for pattern in _DEF_RES:
                    for match in pattern.finditer(text):
                        name_to_files.setdefault(match.group(1), []).append(rel)

        if not name_to_files:
            return []

        edges: list[DynamicEdge] = []
        seen: set[tuple[str, str]] = set()
        for rel, text in sources:
            if "Alpine" not in text:
                continue
            for match in _ALPINE_REGISTER_RE.finditer(text):
                ident = match.group(1)
                for target in name_to_files.get(ident, ()):
                    if target == rel or (rel, target) in seen:
                        continue
                    seen.add((rel, target))
                    edges.append(DynamicEdge(
                        source=rel,
                        target=target,
                        edge_type="dynamic_uses",
                        hint_source=f"{self.name}:register",
                    ))
        return edges
