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
  ``import_module("pkg.plugins." + name)``) in gated non-test code: every
  module matching the template, where each hole is one name segment. A
  template that starts with a hole adds nothing; one with a single-segment
  prefix or more than :data:`_TEMPLATE_FANOUT_CAP` matches links the package
  only. A call in a comment or docstring is skipped.

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
from ..languages.python_strings import PY_DYNAMIC_LOAD_MARKERS, defines_top_level, live_text
from ..source_text import source_bytes
from .base import DynamicEdge, DynamicHintExtractor

# Dotted strings only produce edges from files that load modules at run time,
# so plain dotted strings elsewhere never create spurious reachability.
_DYNAMIC_IMPORT_MARKERS = tuple(m.encode("ascii") for m in PY_DYNAMIC_LOAD_MARKERS)

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
_DOTTED_PREFIX_RE = re.compile(rf"{_SEGMENT}(?:\.{_SEGMENT})*")

# Most modules one templated load may link before it links the package only.
_TEMPLATE_FANOUT_CAP = 50

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


class ModuleStringResolver:
    """Resolves module-path strings against one repo's modules.

    *source_of* returns a module file's bytes, read only to check that an
    attribute reference names something the module has. Attribute checks and
    template expansions are memoised, so one instance serves a whole repo.
    """

    def __init__(self, module_index: dict[str, str], source_of: Callable[[str], bytes]) -> None:
        self.module_index = module_index
        self._source_of = source_of
        self._attrs: dict[tuple[str, str], bool] = {}
        self._templates: dict[str, list[str]] = {}

    def has_attr(self, target: str, name: str) -> bool:
        """Whether a module defines, imports or lazily serves *name* at top level."""
        key = (target, name)
        if key not in self._attrs:
            blob = self._source_of(target)
            word = re.compile(rb"\b" + re.escape(name.encode("ascii")) + rb"\b")
            self._attrs[key] = (
                defines_top_level(blob, name)
                or defines_top_level(blob, "__getattr__")
                or any(word.search(m.group(1)) for m in _TOP_IMPORT_RE.finditer(blob))
            )
        return self._attrs[key]

    def template_targets(self, template: str) -> list[str]:
        """Files of every module matching a load template; each hole is one segment."""
        if template not in self._templates:
            self._templates[template] = self._expand(template)
        return self._templates[template]

    def _expand(self, template: str) -> list[str]:
        prefix = _HOLE_RE.split(template, maxsplit=1)[0].rstrip(".")
        if not _DOTTED_PREFIX_RE.fullmatch(prefix):
            return []  # starts with a hole: would match any module
        pattern = re.compile(_SEGMENT.join(re.escape(part) for part in _HOLE_RE.split(template)))
        matched = [path for dotted, path in self.module_index.items() if pattern.fullmatch(dotted)]
        # Ceiling: a one-segment prefix or a wide match reads as a plugin
        # directory, not a short table, so only the package is linked. Upgrade
        # path: resolve the hole's values when the source spells them out.
        if "." not in prefix or len(matched) > _TEMPLATE_FANOUT_CAP:
            package = self.module_index.get(prefix, "")
            return [package] if package.endswith(_INIT_NAMES) else []
        return matched


def _string_refs(
    text: bytes, module_index: dict[str, str], *, gated: bool, runtime: bool
) -> list[_Ref]:
    """Unverified ``(target, attr)`` references the quoted strings in *text* make."""
    found: list[_Ref] = []
    if gated:
        for m in _DOTTED_STRING_RE.finditer(text):
            module = m.group(1).decode("ascii")
            if module in module_index:
                found.append((module_index[module], None, m.start()))
                continue
            # A module path and the attribute it reads, not a type's qualifier.
            parent, _, attr = module.rpartition(".")
            if runtime and parent in module_index:
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


def _maybe_not_code(blob: bytes, triples: list[int], start: int) -> bool:
    """Whether the text at *start* may sit in a comment or a triple-quoted string.

    Cheap and conservative: a ``#`` earlier on the line, or an odd count of
    triple quotes before it (*triples* holds their offsets), says yes.
    """
    line_start = blob.rfind(b"\n", 0, start) + 1
    return bool(bisect(triples, start) % 2) or b"#" in blob[line_start:start]


def python_dynamic_refs(rel: str, blob: bytes, resolver: ModuleStringResolver) -> _Refs:
    """Modules the source *blob* of *rel* names by string, as ``{target: names}``.

    Pure over its inputs, so a caller holding source bytes needs no filesystem.
    """
    gated = any(marker in blob for marker in _DYNAMIC_IMPORT_MARKERS)
    if not (gated or _ATTR_TAIL_RE.search(blob)):
        return {}
    # A loader outside tests; in a test a member path is a patch target.
    runtime = not is_test_related_path(rel)
    index = resolver.module_index
    triples = [m.start() for m in _TRIPLE_QUOTE_RE.finditer(blob)]
    refs: _Refs = {}
    found = _string_refs(blob, index, gated=gated, runtime=runtime)
    # Tokenize, which costs far more than the scan, only when a match may sit
    # in a comment or docstring.
    if any(_maybe_not_code(blob, triples, start) for _, _, start in found):
        found = _string_refs(live_text(rel, blob), index, gated=gated, runtime=runtime)
    for target, attr, _ in found:
        if attr is None or resolver.has_attr(target, attr):
            _add_ref(refs, target, attr)
    if gated and runtime:
        # Raw text, since f-strings are not plain string tokens on newer
        # Pythons; a call in a comment or docstring is skipped by position.
        for m in _TEMPLATE_LOAD_RE.finditer(blob):
            if _maybe_not_code(blob, triples, m.start()):
                continue
            body = m.group(2) or m.group(4) + b"{}"
            for target in resolver.template_targets(body.decode("ascii")):
                _add_ref(refs, target, None)
    refs.pop(rel, None)
    return refs


def string_inputs(rel: str, blob: bytes) -> tuple:
    """What :func:`python_dynamic_refs` reads from *blob*, for comparing two versions.

    The markers that gate it, every string it may resolve (anywhere, and in
    code only), and each templated load with whether it sits in code. Two
    versions alike here get the same refs from the same modules. Ceiling: a
    ``module:attr`` reference also depends on the target defining *attr*,
    which only the target's text says.
    """
    gates = tuple(m for m in _DYNAMIC_IMPORT_MARKERS if m in blob)
    strings = _resolvable(blob)
    triples = [m.start() for m in _TRIPLE_QUOTE_RE.finditer(blob)]
    templates = sorted(
        {(m.group(0), _maybe_not_code(blob, triples, m.start())) for m in _TEMPLATE_LOAD_RE.finditer(blob)}
    )
    live = _resolvable(live_text(rel, blob)) if strings else []
    return gates, strings, live, templates


def _resolvable(text: bytes) -> list[bytes]:
    return sorted({m.group(0) for r in (_DOTTED_STRING_RE, _ATTR_STRING_RE) for m in r.finditer(text)})


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

        resolver = ModuleStringResolver(
            module_index, lambda target: source_bytes(target, repo_root / target, self._source_map)
        )
        edges: list[DynamicEdge] = []
        for abs_path, rel in rel_by_abs.items():
            blob = source_bytes(rel, abs_path, self._source_map)
            if not blob:
                continue
            refs = python_dynamic_refs(rel, blob, resolver)
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
