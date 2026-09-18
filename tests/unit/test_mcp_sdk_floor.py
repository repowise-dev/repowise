"""The declared MCP floor covers what the server imports at module scope.

``pyproject.toml`` asked for ``mcp>=1.0`` while the server imports
``mcp.server.transport_security`` at the top of ``_server.py`` (the allowlist
that has to match ``--host``, #1737). That module first ships in 1.10.0, so
the declared floor let a resolver install a version on which the entire MCP
server package fails to import with ``ModuleNotFoundError``. Nothing in the
repo read the pin against the import, which is why it could sit wrong.

These tests are the half that does not depend on anyone remembering. The
version facts are the ones verified against PyPI:

* ``streamable-http`` first reaches ``FastMCP.run`` in 1.8.0 (1.7.1 has only
  ``stdio`` and ``sse`` in the annotation), so the transport is not what sets
  the floor.
* ``mcp.server.transport_security`` is absent in 1.7.1, 1.8.0 and 1.9.4, and
  present in 1.10.0. There is no release between 1.9.4 and 1.10.0, so the
  import pins the floor to exactly 1.10.0.

The declared pin is asserted to be at or above that floor rather than equal to
it, so a future bump does not turn this red for the wrong reason.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: The first release that carries ``mcp.server.transport_security``, which
#: ``packages/server/src/repowise/server/mcp_server/_server.py`` imports at
#: module scope. Verified per release against PyPI.
_TRANSPORT_SECURITY_FLOOR: tuple[int, int, int] = (1, 10, 0)

#: The first release where ``FastMCP.run`` accepts ``transport="streamable-http"``.
#: Streamable HTTP needs 1.8.0; the import above needs 1.10.0, which is higher.
_STREAMABLE_HTTP_FLOOR: tuple[int, int, int] = (1, 8, 0)


def _declared_pin() -> str:
    """The ``mcp`` requirement string from the project's dependencies."""
    with open(ROOT / "pyproject.toml", "rb") as handle:
        deps = tomllib.load(handle)["project"]["dependencies"]
    return next(d for d in deps if d.split(">=")[0].split("==")[0].strip() == "mcp")


def _declared_floor() -> tuple[int, int, int]:
    spec = _declared_pin()
    # Only the lower bound of the declared range is under test here.
    lower = spec.split(">=", 1)[1].split(",")[0].strip()
    parts = [int(p) for p in lower.split(".")]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])  # type: ignore[return-value]


def test_the_declared_mcp_floor_covers_the_module_scope_import() -> None:
    """The import is what sets the floor, and the pin has to reach it."""
    floor = _declared_floor()

    assert floor >= _TRANSPORT_SECURITY_FLOOR, (
        f"pyproject.toml declares {_declared_pin()!r}, which permits an mcp "
        f"older than {'.'.join(map(str, _TRANSPORT_SECURITY_FLOOR))}. "
        "mcp.server.transport_security does not exist before that release, and "
        "_server.py imports it at module scope, so every install under the "
        "floor fails to import the MCP server package at all."
    )


def test_the_floor_is_set_by_the_import_and_not_by_the_transport() -> None:
    """Stated as a fact, so a later reader does not lower the pin to 1.8.0.

    Streamable HTTP is the feature this change is about, and it is easy to
    assume the transport is what demands the version. It needs 1.8.0; the
    transport_security import needs 1.10.0. Dropping the pin to the transport
    floor alone would reintroduce the import failure.
    """
    assert _TRANSPORT_SECURITY_FLOOR > _STREAMABLE_HTTP_FLOOR
    assert _declared_floor() >= _TRANSPORT_SECURITY_FLOOR


def test_the_server_still_imports_transport_security_at_module_scope() -> None:
    """The premise of the floor: the import is not function-local.

    If it ever moves inside a function, the floor could legitimately come down
    and this file's reason for existing changes. Pin the shape so that move is
    a deliberate one, noticed here rather than discovered by an install.
    """
    source = (
        ROOT
        / "packages"
        / "server"
        / "src"
        / "repowise"
        / "server"
        / "mcp_server"
        / "_server.py"
    ).read_text(encoding="utf-8")

    module_scope = [
        line
        for line in source.splitlines()
        if line.startswith(("import ", "from "))
    ]
    assert any("mcp.server.transport_security" in line for line in module_scope), (
        "transport_security is no longer imported at module scope; the "
        "declared mcp floor may no longer need to be 1.10.0."
    )
