"""Calls into packages, mixed into ``CallResolver``.

A call the resolver bound to no repo symbol is kept as an
:class:`~.external_calls.ExternalCallSite` when an import of the caller's file
provably binds its callee to an ``external:`` node. Proof is the import
binding itself, read as the resolver reads it: a function-local import binds
its name only inside that function, a name a parameter or local rebinds is not
the import's, and a binding the import maps only guess at is refused. Nothing is
inferred through barrels, star imports or dynamic loads.

A module a test runner swaps for a mock is not the package either: in a file
that calls ``jest.mock("m")`` (or ``vi.mock``, ``doMock``,
``unstable_mockModule``), and in a test file when the repo holds a manual mock
``__mocks__/m.js``, a call on ``m`` runs the mock.
"""

from __future__ import annotations

import re

from ..code_origin import path_origin
from .call_inheritance import _EXTERNAL_PREFIX
from .external_calls import EXTERNAL_CALL_ECOSYSTEMS, ExternalCallSite, package_root
from .models import CallSite, NamedBinding
from .source_text import source_bytes

_CONTAINER_KINDS = frozenset({"function", "method"})
#: Specifier prefixes that are path aliases, never an npm package name.
_ALIAS_PREFIXES = ("~", "#", "$", "/")
#: ``<runner>.<method>(spec, ...)`` calls that replace *spec* for the whole file
#: (Jest and Vitest hoist them above the imports).
_MOCK_RUNNERS = frozenset({"jest", "vi"})
_MOCK_METHODS = frozenset({"mock", "doMock", "unstable_mockModule"})
#: The method name at a call's ``start_byte``, then a plain string first argument.
_MOCK_SPEC = re.compile(rb"[\w$]+\s*\(\s*(['\"`])([^'\"`\\\r\n]+)\1")
#: A partial mock keeps the real module, so it replaces nothing provably: a
#: factory with a parameter (Vitest's ``importOriginal``), or one that reads
#: the original through ``requireActual("m")`` / ``importActual("m")``.
_FACTORY_TAKES_ORIGINAL = re.compile(
    rb"\s*,\s*(?:async\s*)?(?:\(\s*[\w${\[]|[\w$]+\s*=>|function\s*[\w$]*\s*\(\s*[\w${\[])"
)
_ACTUAL_OF = rb"(?:requireActual|importActual)\s*(?:<[^>()]*>)?\s*\(\s*['\"`]%s['\"`]"
_MOCKS_DIR = "__mocks__"
_JS_SUFFIXES = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts")


class ExternalCallsMixin:
    def _external_call_site(self, file_path: str, call: CallSite) -> ExternalCallSite | None:
        """The package call *call* makes, when its binding is proven; else None."""
        language = self._language_of(file_path) or ""  # type: ignore[attr-defined]
        ecosystem = EXTERNAL_CALL_ECOSYSTEMS.get(language)
        caller_id = call.caller_symbol_id
        if ecosystem is None or caller_id is None or call.edge_type != "calls":
            return None
        # A chained call's receiver is a value, typed by nothing here.
        if call.receiver_call is not None or call.on_unnamed_object:
            return None
        found = self._call_binding(file_path, call, language)
        if found is None:
            return None
        binding, attributes = found
        if self._binding_refused(file_path, binding, caller_id, language, call.line):
            return None
        return _site(file_path, call.line, caller_id, ecosystem, binding, attributes)

    def _call_binding(
        self, file_path: str, call: CallSite, language: str
    ) -> tuple[NamedBinding, list[str]] | None:
        """The package import binding *call* goes through, and the attributes it
        reads past it (``linalg``, ``norm`` in ``np.linalg.norm()``)."""
        if call.receiver_name:
            segments = call.receiver_name.split(".")
            found = self._longest_bound_prefix(file_path, segments, call.line)
            if found is None:
                return None
            width, binding = found
            attributes = [*segments[width:], call.target_name]
        else:
            binding = self._proven_binding(file_path, call.target_name, call.line)
            # Calling a Python module is never valid; a CommonJS module may be a function.
            if binding is None or (binding.is_module_alias and language == "python"):
                return None
            attributes = []
        source = binding.source_file or ""
        if not source.startswith(_EXTERNAL_PREFIX) or binding.member_of:
            return None
        return binding, attributes

    def _binding_refused(
        self, file_path: str, binding: NamedBinding, caller_id: str, language: str, line: int
    ) -> bool:
        """Whether the binding may not be the package at this call, cheapest test first."""
        ecosystem = EXTERNAL_CALL_ECOSYSTEMS[language]
        module = (binding.source_file or "")[len(_EXTERNAL_PREFIX) :]
        if self._names_repo_code(ecosystem, module):
            return True
        if ecosystem == "npm" and self._mocked_away(file_path, module):
            return True
        head = binding.local_name.split(".", 1)[0]
        # The file itself rebinds the name (``requests = Session()``), or two
        # imports bind it (``try: import ujson as json`` / ``import json``).
        if head in self._file_top_level.get(file_path, {}):  # type: ignore[attr-defined]
            return True
        if self._bound_twice(file_path, binding.local_name):
            return True
        # Last, as the costliest: the caller's own scopes rebinding the name.
        if self._binds_locally(file_path, caller_id, language, head, line):  # type: ignore[attr-defined]
            return True
        return not self._binding_reaches(file_path, binding, caller_id)

    def _mocked_away(self, file_path: str, module: str) -> bool:
        """Whether a test runner replaces *module* with a mock in *file_path*."""
        if module in self._mocked_specs(file_path):
            return True
        # A manual mock applies only under the runner, so production callers keep the package.
        return module in self._manual_mocks() and path_origin(file_path) == "test"

    def _mocked_specs(self, file_path: str) -> frozenset[str]:
        """Specifiers *file_path* mocks with ``jest.mock("m")`` and its kin."""
        cache: dict[str, frozenset[str]] | None = getattr(self, "_mocked_specs_by_file", None)
        if cache is None:
            cache = self._mocked_specs_by_file = {}
        specs = cache.get(file_path)
        if specs is None:
            parsed = self._parsed_files.get(file_path)  # type: ignore[attr-defined]
            starts = [
                c.start_byte
                for c in (parsed.calls if parsed else ())
                if c.receiver_name in _MOCK_RUNNERS and c.target_name in _MOCK_METHODS
            ]
            found: set[str] = set()
            if starts:
                data = source_bytes(file_path, parsed.file_info.abs_path, self._source_map)  # type: ignore[attr-defined]
                for start in starts:
                    m = _MOCK_SPEC.match(data, start)
                    # A factory handed the original keeps the package's real exports.
                    if m is None or _FACTORY_TAKES_ORIGINAL.match(data, m.end()):
                        continue
                    spec = m.group(2)
                    factory = data[m.end() : _call_end(data, m.end())]
                    if re.search(_ACTUAL_OF % re.escape(spec), factory) is None:
                        found.add(spec.decode("utf-8", "replace"))
            specs = cache[file_path] = frozenset(found)
        return specs

    def _manual_mocks(self) -> frozenset[str]:
        """Modules a ``__mocks__/`` file of the repo stands in for (``@scope/pkg``).

        Ceiling: Jest applies a package's manual mock only from a ``roots``
        directory; any ``__mocks__/`` here counts, which can refuse a real call
        only in a test file.
        """
        mocks = getattr(self, "_manual_mock_names", None)
        if mocks is None:
            names: set[str] = set()
            for path in self._parsed_files:  # type: ignore[attr-defined]
                parts = path.split("/")
                if _MOCKS_DIR not in parts[:-1] or not path.endswith(_JS_SUFFIXES):
                    continue
                at = len(parts) - 1 - parts[::-1].index(_MOCKS_DIR)
                rest = parts[at + 1 :]
                rest[-1] = rest[-1].rsplit(".", 1)[0]
                if len(rest) > 1 and rest[-1] == "index":
                    rest.pop()
                names.add("/".join(rest))
            mocks = self._manual_mock_names = frozenset(names)
        return mocks

    def _bound_twice(self, file_path: str, local_name: str) -> bool:
        """Whether two imports of *file_path* bind *local_name* to different modules."""
        parsed = self._parsed_files.get(file_path)  # type: ignore[attr-defined]
        sources = {
            b.source_file
            for imp in (parsed.imports if parsed else ())
            for b in imp.bindings
            if b.local_name == local_name
        }
        return len(sources) > 1

    def _names_repo_code(self, ecosystem: str, module: str) -> bool:
        """Whether an unresolved specifier may still name the repo's own code.

        A path alias (``@/lib``, ``~/x``, ``#internal``, ``$lib``) or a module
        whose first segment is a top-level directory or module of the repo (or
        of its ``src/``) is first-party code the resolver did not place, never a
        package. Ceiling: an in-repo workspace package the resolver missed is
        not caught here.
        """
        if ecosystem == "npm" and (module.startswith(_ALIAS_PREFIXES) or module.startswith("@/")):
            return True
        return package_root(ecosystem, module).split("/", 1)[0] in self._repo_roots()

    def _repo_roots(self) -> frozenset[str]:
        """Top-level directory and module names of the repo and of its ``src/``."""
        roots = getattr(self, "_repo_root_names", None)
        if roots is None:
            names: set[str] = set()
            for path in self._parsed_files:  # type: ignore[attr-defined]
                parts = path.split("/")
                for start in (0, 1) if parts[0] == "src" else (0,):
                    if len(parts) > start:
                        names.add(parts[start].split(".", 1)[0])
            names.discard("src")
            roots = self._repo_root_names = frozenset(names)
        return roots

    def _longest_bound_prefix(
        self, file_path: str, segments: list[str], line: int
    ) -> tuple[int, NamedBinding] | None:
        """The longest leading run of *segments* an import binds (``os.path`` in
        ``os.path.join``), as ``(segment count, binding)``."""
        for width in range(len(segments), 0, -1):
            binding = self._proven_binding(file_path, ".".join(segments[:width]), line)
            if binding is not None:
                return width, binding
        return None

    def _proven_binding(self, file_path: str, name: str, line: int) -> NamedBinding | None:
        """The import binding of *name* at *line*, unless the maps only guess at it."""
        local = self._import_maps.local_binding(file_path, name, line)  # type: ignore[attr-defined]
        if local is not None:
            binding, guessed = local
            return None if guessed else binding
        return self._import_bindings.get(file_path, {}).get(name)  # type: ignore[attr-defined]

    def _binding_reaches(self, file_path: str, binding: NamedBinding, caller_id: str) -> bool:
        """Whether a binding written inside a function body (``require`` in a
        function) is in scope for *caller_id*: the caller is that function or
        nested in it. A binding outside every function reaches the whole file."""
        if binding.line is None:
            return True
        parsed = self._parsed_files.get(file_path)  # type: ignore[attr-defined]
        holders = [
            (s.start_line, s.end_line)
            for s in (parsed.symbols if parsed else ())
            if s.kind in _CONTAINER_KINDS and s.start_line <= binding.line <= s.end_line
        ]
        if not holders:
            return True
        start, end = max(holders)
        span = self._spans_for(file_path).get(caller_id)  # type: ignore[attr-defined]
        return span is not None and start <= span[0] and span[1] <= end


def _call_end(data: bytes, pos: int) -> int:
    """The offset of the ``)`` closing the call whose arguments continue at *pos*.

    Ceiling: brackets inside strings and comments are counted too, so a
    factory holding an unbalanced one ends early or late; that only narrows or
    widens the text searched for ``requireActual``.
    """
    depth = 0
    for i in range(pos, len(data)):
        byte = data[i]
        if byte in b"([{":
            depth += 1
        elif byte in b")]}":
            if depth == 0:
                return i
            depth -= 1
    return len(data)


def _site(
    file_path: str,
    line: int,
    caller_id: str,
    ecosystem: str,
    binding: NamedBinding,
    attributes: list[str],
) -> ExternalCallSite:
    """The record for one proven package call."""
    if binding.is_module_alias:
        kind, path = "module", attributes
    elif binding.exported_name == "default":
        kind, path = "default", ["default", *attributes]
    else:
        kind, path = "named", [binding.exported_name or binding.local_name, *attributes]
    source = binding.source_file or ""
    module = source[len(_EXTERNAL_PREFIX) :]
    return ExternalCallSite(
        file_path=file_path,
        line=line,
        caller_symbol_id=caller_id,
        ecosystem=ecosystem,
        module=module,
        package=package_root(ecosystem, module),
        external_node=source,
        local_name=binding.local_name,
        symbol_path=".".join(path),
        binding=kind,  # type: ignore[arg-type]
    )
