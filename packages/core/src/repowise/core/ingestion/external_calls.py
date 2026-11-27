"""Calls into third-party packages: the record the call resolver keeps for them.

A call whose callee an import binds to an ``external:`` node has no repo symbol
to point at, so it never becomes a graph edge. It is still a fact worth keeping:
"``api/users.py::get_user`` calls ``requests.get`` at line 42". The resolver
writes one :class:`ExternalCallSite` per such call it can prove
(:meth:`CallResolver._external_call_site`), the graph builder collects them, and
they are stored in their own table so no graph metric moves.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any, Literal

#: Bump when what is recorded changes; a store stamped with another version is
#: incomplete, and the next update rebuilds the graph in full to refill it.
#: Java and Kotlin calls are recorded since this version too.
EXTERNAL_CALL_SITES_VERSION = 1

#: Languages whose import bindings prove a call's package, to the ecosystem
#: their imports come from.
EXTERNAL_CALL_ECOSYSTEMS: dict[str, str] = {
    "python": "pypi",
    "javascript": "npm",
    "typescript": "npm",
    "java": "maven",
    "kotlin": "maven",
}

ExternalBinding = Literal["named", "module", "default"]


@dataclass(frozen=True, slots=True)
class ExternalCallSite:
    """One call into a package, bound by an import of the caller's file."""

    file_path: str
    line: int
    caller_symbol_id: str
    ecosystem: str
    #: The import specifier the resolver keyed (``os.path``, ``lodash/merge``).
    module: str
    #: The root of ``module``: an import name, not always the distribution.
    package: str
    #: ``external:<module>``, the import target's graph node id.
    external_node: str
    #: The name as the caller's file writes it (``np``); for a call on a Java
    #: or Kotlin receiver, the name its declared type is imported by.
    local_name: str
    #: The dotted path inside ``module`` that is called (``linalg.norm``); empty
    #: when the module object itself is called (CommonJS ``express()``).
    symbol_path: str
    binding: ExternalBinding
    #: The number of arguments the call passes, None where it is not known: what
    #: selects one of several overloads of the called name.
    argument_count: int | None = None
    #: Where the called name sits, 1-based line and byte column, None where not
    #: known: a later line than ``line`` where the call starts with its receiver
    #: (``client\n    .get(id)``). A reader of the arguments starts there.
    name_line: int | None = None
    name_column: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> ExternalCallSite:
        return cls(**{f.name: data[f.name] for f in fields(cls)})


def package_root(ecosystem: str, module: str) -> str:
    """The package an import specifier belongs to: ``os`` for ``os.path``,
    ``@scope/pkg`` for ``@scope/pkg/sub``, ``lodash`` for ``lodash/merge``.

    Empty for ``maven``: a Java or Kotlin import does not mark where its
    package ends (``java.util.Map.Entry`` names a nested class, and a Kotlin
    import names a class, a top-level function or an object's member alike).
    """
    if ecosystem == "maven":
        return ""
    if ecosystem == "pypi":
        return module.split(".", 1)[0]
    parts = module.split("/")
    if module.startswith("@") and len(parts) >= 2:
        return "/".join(parts[:2])
    return parts[0]
