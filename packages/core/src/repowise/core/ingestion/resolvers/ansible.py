"""Resolver for Ansible dependency references (lightweight tier, ``yaml``).

Two reference shapes arrive from ``lightweight_imports.ansible``:

* plain file paths (``import_playbook`` / ``import_tasks`` / ``include_tasks``),
  resolved relative to the importing file only (Ansible's own rule; the repo
  root is never a fallback, so a same-named root file cannot steal the edge),
  with ``.yml`` / ``.yaml`` probing when the target omits the extension;
* ``role:<name>`` stamps (``roles:`` entries, ``import_role`` / ``include_role``,
  role ``meta/main.yml`` dependencies), resolved to the role's conventional
  entry file ``roles/<name>/tasks/main.yml`` -- the graph's nodes are files,
  so the entry file is what stands for the role. Looked up from the importer's
  directory upward to the repo root, so a playbook in a subdirectory finds a
  sibling ``roles/`` tree the way Ansible's own search path does.

A miss returns ``None``: Galaxy/collection roles and custom ``roles_path``
layouts are repo-external, and the issue scopes them as deferred rather than
claimed.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from .context import ResolverContext
from .gdscript import _join_relative

_ROLE_PREFIX = "role:"
_YAML_EXTENSIONS = (".yml", ".yaml")


def resolve_ansible_import(
    module_path: str, importer_path: str, ctx: ResolverContext
) -> str | None:
    """Resolve an Ansible file or role reference to a repo-relative path."""
    raw = module_path.strip()
    if not raw:
        return None
    if raw.startswith(_ROLE_PREFIX):
        return _resolve_role(raw[len(_ROLE_PREFIX) :], importer_path, ctx)
    return _resolve_file(raw, importer_path, ctx)


def _resolve_file(target: str, importer_path: str, ctx: ResolverContext) -> str | None:
    names = (
        [target]
        if target.endswith(_YAML_EXTENSIONS)
        else [target + ext for ext in _YAML_EXTENSIONS] + [target]
    )
    importer_dir = PurePosixPath(importer_path).parent
    for name in names:
        candidate = _join_relative(importer_dir, name)
        if candidate is not None and candidate in ctx.path_set:
            return candidate
    return None


def _resolve_role(name: str, importer_path: str, ctx: ResolverContext) -> str | None:
    # Galaxy/collection names (``geerlingguy.nginx``, ``namespace.collection.role``)
    # and path-like names are repo-external by definition; resolving them to a
    # vendored directory that happens to match would be a wrong edge, which is
    # worse than the documented absence.
    if "." in name or "/" in name:
        return None
    # Importer's directory first, walking up to the repo root, so playbooks in
    # a subdirectory resolve a sibling ``roles/`` tree (Ansible's search path
    # covers both the playbook directory and the current directory).
    parts = [p for p in PurePosixPath(importer_path).parent.parts if p not in ("", ".")]
    for i in range(len(parts), -1, -1):
        prefix = "/".join(parts[:i])
        for ext in _YAML_EXTENSIONS:
            candidate = f"{prefix + '/' if prefix else ''}roles/{name}/tasks/main{ext}"
            if candidate in ctx.path_set:
                return candidate
    return None
