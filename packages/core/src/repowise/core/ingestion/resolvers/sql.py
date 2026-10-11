"""dbt import resolution for SQL files (lightweight regex tier).

Gated on ``dbt_project.yml``: the model index is built only from files
under a dbt project root, so a repo with no dbt project resolves nothing
locally (and non-dbt SQL files carry no ``ref()``/``source()`` imports
in the first place).

Resolution order:

1. ``source:<schema>.<table>`` → ``external:source:<schema>.<table>``.
   Sources are by definition outside the project; the typed external
   keeps the warehouse boundary visible in the graph.
2. ``ref('model')`` → a model-name index built per dbt project from
   ``dbt_project.yml``'s ``model-paths`` / ``seed-paths`` /
   ``snapshot-paths`` (defaults ``models/`` / ``seeds/`` /
   ``snapshots/``): every ``.sql`` stem under model and snapshot paths
   plus every ``.csv`` stem under seed paths. When two projects declare
   the same model name, the importer's own project wins.
3. ``ref('package', 'model')`` → a project whose declared ``name``
   matches the package wins; otherwise the bare model name is tried
   across all indexed projects (monorepos index sibling packages);
   otherwise ``external:dbt:<package>.<model>``.
4. Anything else → ``external:dbt:<name>``.

Installed dbt packages under ``dbt_packages/`` (and compiled output
under ``target/``) are never indexed — refs into them stay external.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from .context import ResolverContext

log = structlog.get_logger(__name__)

_PROJECT_FILE = "dbt_project.yml"

_DEFAULT_MODEL_PATHS = ("models",)
_DEFAULT_SEED_PATHS = ("seeds", "data")  # "data" is the pre-1.0 default
_DEFAULT_SNAPSHOT_PATHS = ("snapshots",)


def _as_path_tuple(value: object, default: tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return tuple(v.strip("/") for v in value if v) or default
    return default


def _read_project(repo_path, root: str) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """Return (project_name, sql_dirs, csv_dirs) for the project at *root*.

    Directories are project-relative. Any yml problem degrades to the dbt
    defaults — never a crash, never a guess beyond them.
    """
    name = ""
    model_paths = _DEFAULT_MODEL_PATHS
    seed_paths = _DEFAULT_SEED_PATHS
    snapshot_paths = _DEFAULT_SNAPSHOT_PATHS
    if repo_path is not None:
        try:
            import yaml

            proj_file = repo_path / root / _PROJECT_FILE if root else repo_path / _PROJECT_FILE
            with open(proj_file, encoding="utf-8", errors="replace") as f:
                data = yaml.safe_load(f) or {}
            if isinstance(data, dict):
                name = str(data.get("name") or "")
                # "source-paths" / "data-paths" are the pre-1.0 key names.
                model_paths = _as_path_tuple(
                    data.get("model-paths") or data.get("source-paths"), _DEFAULT_MODEL_PATHS
                )
                seed_paths = _as_path_tuple(
                    data.get("seed-paths") or data.get("data-paths"), _DEFAULT_SEED_PATHS
                )
                snapshot_paths = _as_path_tuple(data.get("snapshot-paths"), _DEFAULT_SNAPSHOT_PATHS)
        except Exception as exc:
            log.debug("dbt_project.yml unreadable, using defaults", root=root, error=str(exc))
    return name, model_paths + snapshot_paths, seed_paths


def _get_index(ctx: ResolverContext) -> dict[str, list[tuple[str, str, str]]]:
    """``{model_name: [(project_root, project_name, path), …]}``, built once."""
    cached = getattr(ctx, "_dbt_model_index", None)
    if cached is not None:
        return cached

    roots = [
        "" if path == _PROJECT_FILE else path[: -len(_PROJECT_FILE) - 1]
        for path in ctx.sorted_paths
        if (path == _PROJECT_FILE or path.endswith("/" + _PROJECT_FILE))
        and "dbt_packages/" not in path
    ]

    index: dict[str, list[tuple[str, str, str]]] = {}
    for root in roots:
        project_name, sql_dirs, csv_dirs = _read_project(ctx.repo_path, root)
        sql_prefixes = tuple(f"{root}/{d}/" if root else f"{d}/" for d in sql_dirs)
        csv_prefixes = tuple(f"{root}/{d}/" if root else f"{d}/" for d in csv_dirs)
        for path in ctx.sorted_paths:
            if "dbt_packages/" in path or "target/" in path:
                continue
            if path.endswith(".sql"):
                prefixes = sql_prefixes
            elif path.endswith(".csv"):
                prefixes = csv_prefixes
            else:
                continue
            if not path.startswith(prefixes):
                continue
            stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            index.setdefault(stem, []).append((root, project_name, path))

    ctx._dbt_model_index = index  # cached like every other lazy per-language index
    if index:
        log.debug("dbt model index built", projects=len(roots), models=len(index))
    return index


def resolve_dbt_import(module_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    if module_path.startswith("source:"):
        return f"external:source:{module_path[len('source:') :]}"

    index = _get_index(ctx)
    package = ""
    candidates = index.get(module_path)
    if candidates is None and "." in module_path:
        # Two-arg form encoded as "package.model".
        package, _, model = module_path.partition(".")
        candidates = index.get(model)

    if candidates:
        chosen = None
        if package:
            chosen = next((p for _r, pname, p in candidates if pname == package), None)
        if chosen is None:
            importer_root = next(
                (r for r, _n, _p in candidates if r and importer_path.startswith(r + "/")),
                "",
            )
            chosen = next((p for r, _n, p in candidates if r == importer_root), candidates[0][2])
        if chosen == importer_path:
            return None  # self-reference
        return chosen

    return f"external:dbt:{module_path}"
