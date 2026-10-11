"""``pnpm-workspace.yaml`` member-glob reading.

Separate from ``ts_workspace`` because it is a manifest reader rather than
part of import resolution — the same split the other package managers get
(``rust_workspace``, ``go_workspace``, ``php_composer``, ``scala_build``).
``ts_workspace`` consumes the globs this returns.
"""

from __future__ import annotations

from pathlib import Path

# pnpm names this file exactly. ``.yml`` is a long-standing request
# (pnpm/pnpm#1380), not a supported spelling — honouring it would map
# members from a manifest pnpm itself ignores.
PNPM_WORKSPACE_FILENAME = "pnpm-workspace.yaml"


def read_pnpm_workspace_patterns(repo_path: Path) -> tuple[list[str], list[str]] | None:
    """Return ``(includes, excludes)`` member globs from ``pnpm-workspace.yaml``.

    ``None`` means "not a pnpm workspace" — the manifest is absent or could
    not be parsed. That is distinct from ``([], [])``, which means "a pnpm
    workspace that declares no member globs", i.e. root-only: pnpm's
    ``packages`` setting is optional and "if the field is omitted, only the
    root package is included in the workspace". Since pnpm 10 the same file
    also holds ``catalog``, ``onlyBuiltDependencies`` and other settings, so
    a manifest with no ``packages`` key is normal rather than an error.

    pnpm ignores ``package.json``'s ``workspaces`` field entirely, so a pnpm
    monorepo declares its members only here::

        packages:
          - "apps/*"
          - "packages/*"
          - "!**/__fixtures__/**"

    A leading ``!`` negates the pattern. Negated entries come back separately
    because the caller applies them by expanding them the same way it expands
    the includes and subtracting the result — see
    :func:`~repowise.core.ingestion.resolvers.ts_workspace.build_workspace_info`.
    """
    import yaml

    manifest = repo_path / PNPM_WORKSPACE_FILENAME
    if not manifest.is_file():
        return None
    try:
        data = yaml.safe_load(manifest.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    entries = data.get("packages")
    if not isinstance(entries, list):
        # A settings-only manifest (catalog, onlyBuiltDependencies, ...) is
        # still a pnpm workspace — root-only.
        return [], []
    includes: list[str] = []
    excludes: list[str] = []
    for entry in entries:
        if not isinstance(entry, str):
            continue
        pattern = entry.strip()
        if pattern.startswith("!"):
            negated = pattern[1:].strip()
            if negated:
                excludes.append(negated)
        elif pattern:
            includes.append(pattern)
    return includes, excludes
