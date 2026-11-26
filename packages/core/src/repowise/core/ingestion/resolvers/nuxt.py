"""Nuxt path aliases and auto-imports.

A Nuxt app imports its own code through aliases the framework declares, not a
checked-in tsconfig: ``~/`` and ``@/`` mean the source directory, ``~~/`` and
``@@/`` the project root. The tsconfig that spells them out is generated into
``.nuxt/`` and gitignored, so the checked-in ``tsconfig.json`` only
``extends "./.nuxt/tsconfig.json"`` and every alias import read as a package.

Nuxt also imports without a statement: ``<RecipeCard />`` in a template is the
component file Nuxt names ``RecipeCard`` under ``components/``, and
``useToast()`` is the export of a top-level file in ``composables/`` or
``utils/``. :func:`nuxt_auto_imports` turns those uses into imports the graph
resolves like written ones, so file reachability, unused exports, the call
graph and test links all see them.

A project is a ``package.json`` that :data:`~..framework_facts.NUXT` matches,
with a ``nuxt.config.*`` beside it, rooted at the source directory
:func:`~..framework_facts.nuxt_src_dir` names. The config is required here
and not for the convention anchors: a Nuxt module repository declares ``nuxt``
as a devDependency, and its root ``components/`` must not bind as an app's.
Nothing here runs for any other repository.

Ceilings, each falling back to today's behaviour (no edge):

* ``nuxt.config`` is read as text, never evaluated. ``srcDir`` (by
  ``framework_facts``) and ``pathPrefix: false`` are read when written as
  literals; custom ``alias``, component ``prefix`` and extra ``imports.dirs``
  entries are not.
* A component or auto-import name two files claim gets no edge: Nuxt warns and
  keeps one, and which one is not ours to guess.
* A default export is auto-imported under the camel-cased file name; only named
  exports are bound here.
* An incremental patch re-derives the auto-imports of the files it re-resolves.
  A newly added component reaches files outside the patch on the next full build.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from ..framework_facts import NUXT, NUXT_CONFIGS, npm_manifests, nuxt_src_dir
from ..sfc_source import vue_component_name_from_stem
from .ts_workspace import _probe_path

if TYPE_CHECKING:
    from ..models import CallSite, Import, ParsedFile
    from .context import ResolverContext

_SFC_EXTENSIONS = (".vue",)
_COMPONENT_EXTENSIONS = (".vue", ".tsx", ".jsx", ".ts", ".js", ".mts", ".mjs")
_SCRIPT_EXTENSIONS = (".ts", ".js", ".mts", ".mjs", ".tsx", ".jsx")
# Nuxt's default ``ignore``: never a component or an auto-import.
_IGNORED_RE = re.compile(r"\.(?:spec|test|stories)\.[cm]?[jt]sx?$|\.d\.[cm]?ts$")
_PATH_PREFIX_OFF_RE = re.compile(r"\bpathPrefix\s*:\s*false\b")
_COMPONENTS_KEY_RE = re.compile(r"(?m)^\s*components\s*:")
# scule's splitByCase: separators, then lower->Upper and UPPER->Upperlower boundaries.
_SPLIT_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z0-9]+|[A-Z]+|\d+")


@dataclass(frozen=True)
class NuxtProject:
    root: str  # repo-relative, "" at the repo root
    src_dir: str  # repo-relative
    components: dict[str, str]  # component name -> file
    auto_imports: dict[str, str]  # exported name -> file

    def contains(self, path: str) -> bool:
        return not self.root or path.startswith(self.root + "/")


def _join(*parts: str) -> str:
    return posixpath.normpath(posixpath.join(*[p for p in parts if p])) if any(parts) else ""


def _read_text(ctx: ResolverContext, rel: str) -> str | None:
    data = (ctx.source_map or {}).get(rel)
    if data is not None:
        return data.decode("utf-8", errors="ignore")
    if ctx.repo_path is None:
        return None
    try:
        return (Path(ctx.repo_path) / rel).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def _split(name: str) -> list[str]:
    return _SPLIT_RE.findall(name)


def _pascal(parts: list[str]) -> str:
    return "".join(p[:1].upper() + p[1:] for p in parts)


def component_name(rel: str, path_prefix: bool) -> str | None:
    """The name Nuxt registers for the component at *rel* (relative to its components dir).

    Follows ``resolveComponentNameSegments``: directory segments prefix the
    file name, minus any trailing run of them the file name already starts
    with (``base/BaseButton.vue`` is ``BaseButton``). ``index.vue`` takes its
    directory's name.
    """
    directory, _, file_name = rel.rpartition("/")
    stem = file_name.rsplit(".", 1)[0]
    stem = re.sub(r"(\.(client|server))?(\.global|\.island)*$", "", stem)
    if stem.lower() == "index":
        if not directory:
            return None
        stem = directory.rsplit("/", 1)[-1] if not path_prefix else ""
    prefix = (
        [part for seg in directory.split("/") if seg for part in _split(seg)] if path_prefix else []
    )
    file_parts = _split(stem)
    file_content = "/".join(file_parts).lower()
    keep = len(prefix)
    for index in range(len(prefix) - 1, -1, -1):
        suffix = "/".join(part.lower() for part in prefix[index:])
        if file_content == suffix or file_content.startswith(suffix + "/"):
            keep = index
    name = _pascal(prefix[:keep] + file_parts)
    return name or None


def _unique(pairs: list[tuple[str, str]]) -> dict[str, str]:
    """Name -> file for names exactly one file claims."""
    seen: dict[str, set[str]] = {}
    for name, path in pairs:
        seen.setdefault(name, set()).add(path)
    return {name: next(iter(paths)) for name, paths in seen.items() if len(paths) == 1}


def _components(ctx: ResolverContext, src: str, config: str) -> dict[str, str]:
    path_prefix = not _PATH_PREFIX_OFF_RE.search(config)
    # Nuxt's default dirs make ``global/`` and ``islands/`` roots of their own;
    # a ``components:`` key replaces those defaults.
    own_roots = ("global/", "islands/") if not _COMPONENTS_KEY_RE.search(config) else ()
    base = _join(src, "components") + "/"
    pairs: list[tuple[str, str]] = []
    for path in ctx.sorted_paths:
        if not path.startswith(base) or not path.endswith(_COMPONENT_EXTENSIONS):
            continue
        if _IGNORED_RE.search(path):
            continue
        rel = path[len(base) :]
        for root in own_roots:
            if rel.startswith(root):
                rel = rel[len(root) :]
                break
        name = component_name(rel, path_prefix)
        if name:
            pairs.append((name, path))
    return _unique(pairs)


def _auto_imports(ctx: ResolverContext, src: str) -> dict[str, str]:
    """Named exports of the top-level files (and ``<dir>/index.*``) in composables/ and utils/."""
    parsed_files = ctx.parsed_files or {}
    bases = tuple(_join(src, folder) + "/" for folder in ("composables", "utils"))
    pairs: list[tuple[str, str]] = []
    for path in ctx.sorted_paths:
        if not _is_auto_import_file(path, bases):
            continue
        exports = getattr(parsed_files.get(path), "exports", None) or ()
        pairs.extend((name, path) for name in exports if name != "default")
    return _unique(pairs)


def _is_auto_import_file(path: str, bases: tuple[str, ...]) -> bool:
    """A top-level script (or ``<dir>/index.*``) under one of *bases*, as Nuxt scans them."""
    base = next((b for b in bases if path.startswith(b)), None)
    if base is None or not path.endswith(_SCRIPT_EXTENSIONS) or _IGNORED_RE.search(path):
        return False
    parts = path[len(base) :].split("/")
    return len(parts) == 1 or (len(parts) == 2 and parts[1].rsplit(".", 1)[0] == "index")


def nuxt_projects(ctx: ResolverContext) -> tuple[NuxtProject, ...]:
    """Every Nuxt project in the repository, deepest root first (cached on *ctx*)."""
    cached = getattr(ctx, "_nuxt_projects", None)
    if cached is not None:
        return cached
    projects: list[NuxtProject] = []
    configs = [p for p in ctx.sorted_paths if p.rpartition("/")[2] in NUXT_CONFIGS]
    for manifest in npm_manifests(ctx.repo_path, configs) if ctx.repo_path and configs else ():
        config = NUXT.config(manifest) if NUXT.matches_npm(manifest) else None
        if config is None:
            continue
        src = nuxt_src_dir(manifest, ctx.sorted_paths) or manifest.rel_dir
        projects.append(
            NuxtProject(
                root=manifest.rel_dir,
                src_dir=src,
                components=_components(ctx, src, config),
                auto_imports=_auto_imports(ctx, src),
            )
        )
    projects.sort(key=lambda p: len(p.root), reverse=True)
    result = tuple(projects)
    ctx._nuxt_projects = result  # type: ignore[attr-defined]
    return result


def _project_for(path: str, ctx: ResolverContext) -> NuxtProject | None:
    for project in nuxt_projects(ctx):
        if project.contains(path):
            return project
    return None


def resolve_nuxt_alias(module_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    """Resolve ``~/x``, ``@/x`` (source dir) or ``~~/x``, ``@@/x`` (root) in a Nuxt project."""
    if module_path.startswith(("~~/", "@@/")):
        rest, to_root = module_path[3:], True
    elif module_path.startswith(("~/", "@/")):
        rest, to_root = module_path[2:], False
    else:
        return None
    project = _project_for(importer_path, ctx)
    if project is None:
        return None
    base = _join(project.root if to_root else project.src_dir, rest)
    hit = _probe_path(base, ctx.path_set)
    if hit is not None:
        return hit
    for ext in (".vue", "/index.vue"):
        if base + ext in ctx.path_set:
            return base + ext
    return None


def _alias_spec(project: NuxtProject, target: str) -> str:
    """How the importer would have written it: ``~/`` + the path inside srcDir."""
    if project.src_dir and target.startswith(project.src_dir + "/"):
        return "~/" + target[len(project.src_dir) + 1 :]
    if not project.src_dir:
        return "~/" + target
    return target


def _declares(ctx: ResolverContext, path: str, name: str) -> bool:
    """Whether *path* binds *name* itself: a local, a parameter, a destructured name.

    The symbol table holds top-level declarations only, and a local shadows
    the auto-import. Text-level, so a name merely mentioned in a parameter
    list or a ``{ ... } =`` pattern also counts: the safe side, no edge.
    """
    text = _read_text(ctx, path)
    if text is None:
        return True
    word = rf"\b{re.escape(name)}\b"
    return bool(
        re.search(rf"(?:const|let|var|function|class)\s+{word}", text)
        or re.search(rf"\([^()]*{word}[^()]*\)\s*(?:=>|\{{|:)", text)
        # A typed or defaulted parameter, whose list may nest parentheses.
        or re.search(rf"[(,]\s*{word}\s*\??\s*[:=](?!=)", text)
        or re.search(rf"(?:const|let|var)\s*[{{\[][^}}\]]*{word}[^}}\]]*[}}\]]\s*=", text)
    )


def _component_symbol(target: str) -> str:
    """The name a component file publishes: its SFC component symbol, else ``default``.

    The tag Nuxt registers (``LazyStatsCards``, ``FormInput``) need not match
    the file's own component symbol (``StatsCards``, ``Input``), so the call
    binds to the symbol by the name the file gives it.
    """
    if not target.endswith(".vue"):
        return "default"
    path = PurePosixPath(target)
    return vue_component_name_from_stem(path.stem, path.parent.name) or "default"


def nuxt_auto_imports(
    parsed_files: dict[str, ParsedFile], ctx: ResolverContext
) -> dict[str, list[Import]]:
    """The imports Nuxt writes for each file: component tags and composable/util calls.

    A use binds only when nothing in the file already names it (an import, a
    declaration of its own) and exactly one file in the project provides it.
    """
    if not nuxt_projects(ctx):
        return {}
    result: dict[str, list[Import]] = {}
    for path, parsed in parsed_files.items():
        project = _project_for(path, ctx) if parsed.calls else None
        if project is None or not _takes_auto_imports(path, project):
            continue
        imports = _file_auto_imports(path, parsed, project, ctx)
        if imports:
            result[path] = imports
    return result


def _takes_auto_imports(path: str, project: NuxtProject) -> bool:
    """A Vue or script file of the app; ``server/`` belongs to Nitro's own auto-imports."""
    return path.endswith(_SFC_EXTENSIONS + _SCRIPT_EXTENSIONS) and not path.startswith(
        _join(project.root, "server") + "/"
    )


def _provider(call: CallSite, project: NuxtProject) -> tuple[str, str] | None:
    """``(file, exported name)`` Nuxt would load for *call*, or None."""
    name = call.target_name
    if call.argument_count is None:
        # A markup tag (no argument list); ``Image()`` in script is not one.
        target = project.components.get(name)
        if target is None and name.startswith("Lazy"):
            target = project.components.get(name[4:])
        if target is not None:
            return target, _component_symbol(target)
    target = project.auto_imports.get(name)
    return (target, name) if target is not None else None


def _file_auto_imports(
    path: str, parsed: ParsedFile, project: NuxtProject, ctx: ResolverContext
) -> list[Import]:
    from ..models import Import, NamedBinding

    known = {name for imp in parsed.imports for name in imp.local_names}
    known.update(sym.name for sym in parsed.symbols)
    wanted: dict[tuple[str, str], str] = {}  # (target, local name) -> exported name
    for call in parsed.calls:
        name = call.target_name
        if call.receiver_name is not None or not name or name in known:
            continue
        found = _provider(call, project)
        if found is None or found[0] == path or (found[0], name) in wanted:
            continue
        if _declares(ctx, path, name):
            known.add(name)
            continue
        wanted[(found[0], name)] = found[1]
    return [
        Import(
            raw_statement=f"// nuxt auto-import: {local}",
            module_path=_alias_spec(project, target),
            imported_names=[local],
            is_relative=False,
            resolved_file=target,
            bindings=[NamedBinding(local_name=local, exported_name=exported, source_file=target)],
        )
        for (target, local), exported in sorted(wanted.items())
    ]
