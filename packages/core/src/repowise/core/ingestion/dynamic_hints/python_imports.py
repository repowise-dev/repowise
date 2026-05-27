"""Dynamic-import hints for Python.

Static import resolution sees ``import x`` / ``from x import y`` but is
blind to runtime-resolved imports — the registry / plugin pattern where a
module is named by a *string* and loaded with ``importlib.import_module``
(``getattr`` then pulls the class out by name). The canonical shape::

    _PROVIDERS = {
        "ollama": ("my_pkg.providers.ollama", "OllamaProvider"),
    }
    module = importlib.import_module(_PROVIDERS[name][0])
    cls = getattr(module, _PROVIDERS[name][1])

Nothing statically imports ``my_pkg.providers.ollama`` or ``OllamaProvider``,
so dead-code analysis would flag both the module and the class as unused.

This extractor recovers those edges generically: in any file that uses
dynamic-import machinery, every quoted string that resolves to a real
in-repo module (via :func:`python_modules.build_python_module_index`) yields
a ``dynamic_uses`` edge to that module's file. The analyzer then treats the
target file as runtime-loaded and stops flagging its public members.

Gating on the presence of dynamic-import machinery (rather than emitting an
edge for any dotted string that happens to match a module) keeps the signal
precise: a dotted string in a log message or docstring of an ordinary module
is ignored. The mechanism is repo-agnostic — it works for any Python plugin
registry, ``importlib`` loader, or entry-point dispatch table.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..languages.python_modules import build_python_module_index
from .base import DynamicEdge, DynamicHintExtractor

# Tokens that signal a file performs runtime module loading. An edge is only
# emitted from files that contain at least one of these, so plain dotted
# strings elsewhere never create spurious reachability.
_DYNAMIC_IMPORT_MARKERS: tuple[str, ...] = (
    "importlib",
    "import_module",
    "__import__",
    "import_string",  # Werkzeug / Flask / Django utilities
    "load_entry_point",
    "entry_points(",  # importlib.metadata plugin discovery
    "pkgutil",
    "pkg_resources",
)

# A quoted dotted path of two or more identifier segments — i.e. something
# shaped like ``a.b`` / ``pkg.sub.mod``. Single-segment names are too
# ambiguous (and resolve via ordinary stem matching anyway).
_DOTTED_STRING_RE = re.compile(r"""['"]([a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)+)['"]""")


class PythonDynamicHints(DynamicHintExtractor):
    name = "python_dynamic_import"

    def extract(self, repo_root: Path) -> list[DynamicEdge]:
        rel_by_abs: dict[Path, str] = {}
        for py in self._rglob(repo_root, "*.py"):
            try:
                rel = py.relative_to(repo_root).as_posix()
            except ValueError:
                continue
            rel_by_abs[py] = rel

        if not rel_by_abs:
            return []

        module_index = build_python_module_index(rel_by_abs.values())
        if not module_index:
            return []

        edges: list[DynamicEdge] = []
        for abs_path, rel in rel_by_abs.items():
            try:
                text = abs_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if not any(marker in text for marker in _DYNAMIC_IMPORT_MARKERS):
                continue

            seen: set[str] = set()
            for match in _DOTTED_STRING_RE.finditer(text):
                dotted = match.group(1)
                if dotted in seen:
                    continue
                seen.add(dotted)
                target = module_index.get(dotted)
                if target and target != rel:
                    edges.append(
                        DynamicEdge(
                            source=rel,
                            target=target,
                            edge_type="dynamic_uses",
                            hint_source=self.name,
                        )
                    )
        return edges
