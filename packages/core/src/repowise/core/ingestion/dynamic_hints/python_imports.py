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
so dead-code analysis would flag both the module and the class as unused,
and test selection would never connect the registry to the module.

This extractor recovers those edges generically. Every string it considers
must resolve to a real in-repo module (via
:func:`python_modules.build_python_module_index`); a string that names
nothing in the repo adds nothing, and only strings that run count (a comment
or docstring loads nothing). Three shapes are read:

* **Dotted module paths** (``"pkg.sub.mod"``), only in files that use
  dynamic-import machinery (:data:`_DYNAMIC_IMPORT_MARKERS`). Gating keeps a
  dotted string in a log message of an ordinary module inert.
* **Attribute references**: ``"pkg.mod:attr"`` in any file (the spelling of
  console-script entry points, ASGI/WSGI app specs and lazy command
  registries), and ``"pkg.mod.Name"`` in gated files outside tests (the
  ``import_string`` / lazy command group spelling; in a test it is a patch
  target). A relative ``"status_cmd:status_command"`` resolves under any
  package the same file names by a dotted string, which is how lazy command
  tables join a base package to short names. The module must define or
  import the attribute (or define ``__getattr__``): ``"agent:start"`` as an
  event name, next to a package called ``agent`` without a ``start``, adds
  nothing.
* **Templated loads** (``import_module(f"pkg.plugins.{name}")`` or
  ``import_module("pkg.plugins." + name)``) in gated non-test files: every
  module matching the template, where each hole is one name segment. The
  template must start with a literal segment, so an all-hole ``f"{a}.{b}"``
  adds nothing.

The mechanism is repo-agnostic: plugin registries, ``importlib`` loaders,
entry-point dispatch tables and lazy CLI groups all reduce to these shapes.
"""

from __future__ import annotations

import re
from bisect import bisect
from collections.abc import Callable
from pathlib import Path

from ...test_paths import is_test_related_path
from ..languages.python_modules import build_python_module_index
from ..languages.python_strings import defines_top_level, live_text
from .base import DynamicEdge, DynamicHintExtractor

# Tokens that signal a file performs runtime module loading. Dotted strings
# only produce edges from files that contain at least one of these, so plain
# dotted strings elsewhere never create spurious reachability.
_DYNAMIC_IMPORT_MARKERS: tuple[bytes, ...] = (
    b"importlib",
    b"import_module",
    b"__import__",
    b"import_string",  # Werkzeug / Flask / Django utilities
    b"load_entry_point",
    b"entry_points(",  # importlib.metadata plugin discovery
    b"pkgutil",
    b"pkg_resources",
    b"lazy_subcommands",  # lazy click group keyword: {"name": "pkg.mod.attr"}
)

# A quoted dotted path of two or more segments (``"pkg.sub.mod"``).
_DOTTED_STRING_RE = re.compile(rb"""['"]([a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)+)['"]""")
# A quoted ``module:attr``: all an ungated file is scanned for.
_ATTR_STRING_RE = re.compile(rb"""['"]([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*):([A-Za-z_]\w*)['"]""")
# Its colon-first tail: a literal-led scan about ten times faster, so files
# without one skip the full pattern.
_ATTR_TAIL_RE = re.compile(rb""":[A-Za-z_]\w*['"]""")

# ``import_module(f"...")`` / ``__import__(f"...")`` and the ``"prefix." + x``
# concatenation. Group 2 is the f-string body, group 4 the literal prefix.
_TEMPLATE_LOAD_RE = re.compile(
    rb"""(?:import_module|__import__)\(\s*"""
    rb"""(?:f(['"])([\w.{}]+)\1|(['"])((?:[a-zA-Z_]\w*\.)+)\3\s*\+)"""
)
_HOLE_RE = re.compile(r"\{[^{}]*\}")
_SEGMENT = r"[a-zA-Z_]\w*"

# Top-level import statements, parenthesised continuation included.
_TOP_IMPORT_RE = re.compile(rb"^(?:from[ \t]+[\w.]+[ \t]+)?import[ \t]+(\([^)]*\)|[^\n]*)", re.M)

_TRIPLE_QUOTE_RE = re.compile(rb"\"\"\"|'''")

_INIT_NAMES = ("__init__.py", "__init__.pyi")

# Target file -> names reached there; an empty tuple means the whole module.
_Refs = dict[str, tuple[str, ...]]
# (target, attribute or None for the whole module, offset of the string)
_Ref = tuple[str, str | None, int]


def _add_ref(refs: _Refs, target: str, name: str | None) -> None:
    """Record *target*; a whole-module reference absorbs any named one."""
    if name is None or refs.get(target) == ():
        refs[target] = ()
        return
    refs[target] = tuple(sorted({*refs.get(target, ()), name}))


def _has_attr(blob: bytes, name: str) -> bool:
    """Whether a module defines, imports or lazily serves *name* at top level."""
    if defines_top_level(blob, name) or defines_top_level(blob, "__getattr__"):
        return True
    word = re.compile(rb"\b" + re.escape(name.encode("ascii")) + rb"\b")
    return any(word.search(m.group(1)) for m in _TOP_IMPORT_RE.finditer(blob))


def _string_refs(
    text: bytes, module_index: dict[str, str], *, gated: bool, members: bool
) -> list[_Ref]:
    """Unverified ``(target, attr)`` references the quoted strings in *text* make."""
    found: list[_Ref] = []
    if gated:
        for m in _DOTTED_STRING_RE.finditer(text):
            module = m.group(1).decode("ascii")
            if module in module_index:
                found.append((module_index[module], None, m.start()))
                continue
            parent, _, attr = module.rpartition(".")
            if members and parent in module_index:
                found.append((module_index[parent], attr, m.start()))
    relative: list[tuple[str, str, int]] = []
    for m in _ATTR_STRING_RE.finditer(text) if _ATTR_TAIL_RE.search(text) else ():
        module, attr = m.group(1).decode("ascii"), m.group(2).decode("ascii")
        target = module_index.get(module)
        if target is not None:
            found.append((target, attr, m.start()))
        else:
            relative.append((module, attr, m.start()))
    if relative:
        # Packages this file names: the roots a short reference joins.
        bases = sorted(
            {
                dotted
                for m in _DOTTED_STRING_RE.finditer(text)
                if module_index.get(dotted := m.group(1).decode("ascii"), "").endswith(_INIT_NAMES)
            }
        )
        for module, attr, start in relative:
            found.extend(
                (module_index[f"{b}.{module}"], attr, start)
                for b in bases
                if f"{b}.{module}" in module_index
            )
    return found


def _maybe_not_code(blob: bytes, starts: list[int]) -> bool:
    """Whether a string at *starts* may sit in a comment or docstring.

    Cheap and conservative: a match after ``#`` on its line or inside a
    triple-quoted block says yes, and only then is the file tokenized, which
    on large files costs far more than the scan.
    """
    triples = [m.start() for m in _TRIPLE_QUOTE_RE.finditer(blob)]
    for start in starts:
        line_start = blob.rfind(b"\n", 0, start) + 1
        if bisect(triples, start) % 2 or b"#" in blob[line_start:start]:
            return True
    return False


def _template_targets(template: str, module_index: dict[str, str]) -> list[str]:
    """Files of every module matching a load template; each hole is one segment."""
    if not re.match(_SEGMENT + r"\.", template):
        return []  # starts with a hole: would match any module
    pattern = re.compile(_SEGMENT.join(re.escape(part) for part in _HOLE_RE.split(template)))
    # Linear in modules, but run only per templated load call (a handful per repo).
    return [path for dotted, path in module_index.items() if pattern.fullmatch(dotted)]


def python_dynamic_refs(
    rel: str,
    blob: bytes,
    module_index: dict[str, str],
    source_of: Callable[[str], bytes],
) -> _Refs:
    """Modules the source *blob* of *rel* names by string, as ``{target: names}``.

    *source_of* returns a target file's bytes, read only to check that an
    attribute reference names something the module has. Pure over its inputs,
    so a caller holding source bytes needs no filesystem.
    """
    gated = any(marker in blob for marker in _DYNAMIC_IMPORT_MARKERS)
    if not (gated or _ATTR_TAIL_RE.search(blob)):
        return {}
    members = not is_test_related_path(rel)
    refs: _Refs = {}
    found = _string_refs(blob, module_index, gated=gated, members=members)
    if found and _maybe_not_code(blob, [start for _, _, start in found]):
        found = _string_refs(live_text(rel, blob), module_index, gated=gated, members=members)
    for target, attr, _ in found:
        if attr is None or _has_attr(source_of(target), attr):
            _add_ref(refs, target, attr)
    if gated and members:
        # Raw text: f-strings are not plain string tokens on newer Pythons.
        for m in _TEMPLATE_LOAD_RE.finditer(blob):
            body = m.group(2) or m.group(4) + b"{}"
            for target in _template_targets(body.decode("ascii"), module_index):
                _add_ref(refs, target, None)
    refs.pop(rel, None)
    return refs


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

        targets: dict[str, bytes] = {}

        def source_of(target: str) -> bytes:
            if target not in targets:
                try:
                    targets[target] = self._source_bytes(repo_root / target, target)
                except OSError:
                    targets[target] = b""
            return targets[target]

        edges: list[DynamicEdge] = []
        for abs_path, rel in rel_by_abs.items():
            try:
                blob = self._source_bytes(abs_path, rel)
            except OSError:
                continue
            refs = python_dynamic_refs(rel, blob, module_index, source_of)
            for target, names in sorted(refs.items()):
                edges.append(
                    DynamicEdge(
                        source=rel,
                        target=target,
                        edge_type="dynamic_uses",
                        hint_source=self.name,
                        imported_names=names,
                    )
                )
        return edges
