"""Calls into packages, mixed into ``CallResolver``.

A call the resolver bound to no repo symbol is kept as an
:class:`~.external_calls.ExternalCallSite` when an import of the caller's file
provably binds its callee to an ``external:`` node. Proof is the import
binding itself, read as the resolver reads it: a function-local import binds
its name only inside that function, a name a parameter or local rebinds is not
the import's, and a binding the import maps only guess at is refused. Nothing is
inferred through barrels, star imports or dynamic loads.
"""

from __future__ import annotations

from .call_inheritance import _EXTERNAL_PREFIX
from .external_calls import EXTERNAL_CALL_ECOSYSTEMS, ExternalCallSite, package_root
from .models import CallSite, NamedBinding

_CONTAINER_KINDS = frozenset({"function", "method"})
#: Specifier prefixes that are path aliases, never an npm package name.
_ALIAS_PREFIXES = ("~", "#", "$", "/")


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
        if call.receiver_name:
            segments = call.receiver_name.split(".")
            found = self._longest_bound_prefix(file_path, segments, call.line)
            if found is None:
                return None
            width, binding = found
            attributes = [*segments[width:], call.target_name]
        else:
            binding = self._proven_binding(file_path, call.target_name, call.line)
            if binding is None:
                return None
            attributes = []
            # Calling a Python module is never valid; a CommonJS module may be a function.
            if binding.is_module_alias and language == "python":
                return None
        source = binding.source_file or ""
        if not source.startswith(_EXTERNAL_PREFIX) or binding.member_of:
            return None
        module = source[len(_EXTERNAL_PREFIX) :]
        if self._names_repo_code(ecosystem, module):
            return None
        head = binding.local_name.split(".", 1)[0]
        # The file itself rebinds the name (``requests = Session()``), or two
        # imports bind it (``try: import ujson as json`` / ``import json``).
        if head in self._file_top_level.get(file_path, {}) or self._bound_twice(  # type: ignore[attr-defined]
            file_path, binding.local_name
        ):
            return None
        # Last, as the costliest: the caller's own scopes rebinding the name.
        if self._binds_locally(file_path, caller_id, language, head, call.line):  # type: ignore[attr-defined]
            return None
        if not self._binding_reaches(file_path, binding, caller_id):
            return None
        if binding.is_module_alias:
            kind, path = "module", attributes
        elif binding.exported_name == "default":
            kind, path = "default", ["default", *attributes]
        else:
            kind, path = "named", [binding.exported_name or binding.local_name, *attributes]
        return ExternalCallSite(
            file_path=file_path,
            line=call.line,
            caller_symbol_id=caller_id,
            ecosystem=ecosystem,
            module=module,
            package=package_root(ecosystem, module),
            external_node=source,
            local_name=binding.local_name,
            symbol_path=".".join(path),
            binding=kind,  # type: ignore[arg-type]
        )

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
