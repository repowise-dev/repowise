"""Receivers typed by a framework decorator on a module-level def."""

from __future__ import annotations

from ..languages.receiver_types import framework_decorated_type
from ..resolved_call import ResolvedCall
from ._shared import _is_module_level_function


class FrameworkReceiverTypes:
    """Framework-decorator receiver typing."""

    def _framework_receiver_type(
        self,
        file_path: str,
        caller_id: str,
        language: str,
        receiver_name: str,
    ) -> str | None:
        # The type lookup is a dict hit and the shadowing scan reads the file,
        # so the cheap half decides first.
        type_name = self._framework_type_of(file_path, receiver_name, language)
        if type_name is not None and receiver_name in self._bound_names_in(
            file_path, caller_id, language
        ):
            return None
        return type_name

    def _framework_typed_call(
        self, caller_id: str, sym_id: str, tier: str, line: int
    ) -> ResolvedCall:
        """Stamp an edge whose receiver was typed by a framework decorator."""
        if tier == "same_file":
            return ResolvedCall(caller_id, sym_id, 0.93, line, "receiver_framework_same_file")
        if tier == "same_package":
            return ResolvedCall(caller_id, sym_id, 0.90, line, "receiver_framework_same_package")
        if tier == "import":
            return ResolvedCall(caller_id, sym_id, 0.88, line, "receiver_framework_import")
        return ResolvedCall(caller_id, sym_id, 0.75, line, "receiver_framework_global")

    def _framework_names(self, language: str) -> frozenset[str]:
        """Every name a framework decorator retypes anywhere in the repo."""
        if self._framework_name_set is not None:
            return self._framework_name_set
        found: set[str] = set()
        for parsed in self._parsed_files.values():
            if parsed.file_info.language != language:
                continue
            found.update(
                symbol.name
                for symbol in parsed.symbols
                if _is_module_level_function(symbol)
                and framework_decorated_type(symbol.decorators, language)
            )
        self._framework_name_set = frozenset(found)
        return self._framework_name_set

    def _framework_types_in(self, file_path: str, language: str) -> dict[str, str]:
        """``{name: type}`` for one file's module-level decorated defs."""
        types = self._framework_types.get(file_path)
        if types is None:
            parsed = self._parsed_files.get(file_path)
            types = {}
            for symbol in parsed.symbols if parsed else ():
                if not _is_module_level_function(symbol):
                    continue
                type_name = framework_decorated_type(symbol.decorators, language)
                if type_name is not None:
                    types[symbol.name] = type_name
            # Uncapped, unlike the source-text caches: this holds names read off
            # already-resident symbols, and is empty for all but a few files.
            self._framework_types[file_path] = types
        return types

    def _framework_type_of(self, file_path: str, receiver_name: str, language: str) -> str | None:
        """The framework type of *receiver_name*, where this file can see it.

        Declared here, or imported here by name. A decorated def in a file the
        caller never imports is not this receiver, and reaching for it would be
        the bare-name match this tier exists to avoid.
        """
        # One repo-wide pass answers every call site that names nothing
        # decorated, sparing each a per-file symbol walk.
        if receiver_name not in self._framework_names(language):
            return None

        own = self._framework_types_in(file_path, language).get(receiver_name)
        if own is not None:
            return own

        bound = self._import_names.get(file_path, {}).get(receiver_name)
        if bound is None or bound.startswith("external:"):
            return None
        binding = self._import_bindings.get(file_path, {}).get(receiver_name)
        exported = (binding.exported_name if binding else None) or receiver_name
        declaring = self._barrel_origins.get(bound, {}).get(exported) or bound
        return self._framework_types_in(declaring, language).get(exported)
