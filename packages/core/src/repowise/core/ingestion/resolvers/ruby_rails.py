"""Rails / Zeitwerk autoload index for Ruby import resolution.

Real Rails apps barely use ``require``; class references like
``UserController`` or ``Admin::Reports::Daily`` are resolved at runtime by
Zeitwerk's convention: ``CamelCase`` constants live at ``snake_case.rb``
files under one of the autoload roots (every ``app/*`` directory, the
``concerns`` inside them, ``lib``, and the same under an overlay such as
``enterprise/app``).

This index is detection-gated: returns ``None`` unless ``config/application.rb``
exists. Non-Rails Ruby repos pay zero cost.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from repowise.core.fs_walk import iter_glob

if TYPE_CHECKING:
    from .context import ResolverContext


_CAMEL_TO_SNAKE_RE = re.compile(r"(?<!^)(?=[A-Z])")


def camel_to_snake(name: str) -> str:
    """``UserController`` → ``user_controller``; ``HTTPRequest`` → ``h_t_t_p_request``.

    Zeitwerk uses ``ActiveSupport::Inflector.underscore`` which collapses
    runs of capitals (HTTPRequest → http_request); we approximate with a
    second pass that handles the ``ABCDef`` → ``ab_c_def`` case.
    """
    # First pass: insert _ before any uppercase preceded by lowercase
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    s2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1)
    return s2.lower()


def constant_key(constant: str) -> str:
    """The key a constant path is looked up by: ``Api::V1::OpenAIClient`` → ``api/v1/openaiclient``.

    Segments are lowercased with underscores dropped, on both sides of the
    lookup, so a custom acronym inflection (``OpenAI`` defined in
    ``open_ai.rb`` or ``openai.rb``) still meets its file. Two files whose
    names differ only by underscores share a key and the first root wins.
    """
    return "/".join(seg.replace("_", "").lower() for seg in constant.strip(":").split("::"))


def _path_key(relative_to_root: str) -> str:
    return "/".join(seg.replace("_", "").lower() for seg in relative_to_root.split("/"))


@dataclass
class RailsIndex:
    autoload_roots: list[str] = field(default_factory=list)
    name_to_file: dict[str, str] = field(default_factory=dict)
    namespace_to_file: dict[str, str] = field(default_factory=dict)
    #: ``constant_key`` of every autoloaded constant → the file defining it.
    constant_to_file: dict[str, str] = field(default_factory=dict)
    #: File → the ``constant_key`` of the constant Zeitwerk expects it to define.
    file_to_constant: dict[str, str] = field(default_factory=dict)

    def lookup(self, constant: str) -> str | None:
        """Resolve a constant reference (``UserController`` or ``Foo::Bar``)
        to a repo-relative ``.rb`` path.

        Falls back to the last segment's file stem, which can bind a name to
        an unrelated same-named file; :meth:`resolve` never does.
        """
        hit = self.constant_to_file.get(constant_key(constant))
        if hit:
            return hit
        if "::" in constant:
            constant = constant.split("::")[-1]
        return self.name_to_file.get(camel_to_snake(constant))

    def resolve(self, constant: str, from_path: str | None = None) -> str | None:
        """Resolve *constant* as Ruby would from a reference in *from_path*.

        A relative reference is tried in each namespace enclosing the
        referencing file, innermost first, then at the top level; ``::Foo`` is
        top level only. The nesting is the one Zeitwerk gives the file's path,
        not the ``module`` blocks in its source. Ceiling: a compact
        ``class Api::V1::Foo`` opens no ``Api::V1`` scope in Ruby, so there a
        name defined both as ``Api::V1::Bar`` and as ``Bar`` binds to the inner
        one. Reading the lexical nesting off the parse tree is the upgrade.
        """
        if not constant or constant == "::":
            return None
        key = constant_key(constant)
        if constant.startswith("::"):
            return self.constant_to_file.get(key)
        scopes = self.file_to_constant.get(from_path or "", "").split("/")[:-1]
        while scopes:
            hit = self.constant_to_file.get("/".join([*scopes, key]))
            if hit:
                return hit
            scopes.pop()
        return self.constant_to_file.get(key)


# Directories under ``app/`` that Rails does not autoload.
_NON_AUTOLOAD_APP_DIRS = frozenset({"assets", "javascript", "views"})

# ``config.eager_load_paths << Rails.root.join('enterprise/lib')`` and the
# ``autoload_paths`` twin, then ``+= Dir["#{Rails.root}/enterprise/app/**"]``.
_CONFIG_JOIN_RE = re.compile(
    r"(?:autoload|eager_load)(?:_once)?_paths\b[^\n]*?Rails\.root\.join\(\s*['\"]([\w./-]+)['\"]"
)
_CONFIG_DIR_GLOB_RE = re.compile(
    r"(?:autoload|eager_load)(?:_once)?_paths\b[^\n]*?Dir\[\s*\"#\{Rails\.root\}/([\w./-]+?)/\*\*?\"\s*\]"
)


def _app_children(repo: Path, app_dir: str) -> list[str]:
    base = repo / app_dir
    if not base.is_dir():
        return []
    roots: list[str] = []
    for child in sorted(base.iterdir(), key=lambda c: c.name):
        if not child.is_dir() or child.name in _NON_AUTOLOAD_APP_DIRS:
            continue
        roots.append(f"{app_dir}/{child.name}")
        # ``app/models/concerns`` is a root of its own: ``Taggable`` lives at
        # ``app/models/concerns/taggable.rb``, not under ``Concerns::``.
        if (child / "concerns").is_dir():
            roots.append(f"{app_dir}/{child.name}/concerns")
    return roots


def _autoload_roots(repo: Path) -> list[str]:
    """Every directory Zeitwerk autoloads from, derived from the tree.

    Each subdirectory of ``app/`` and of a top-level overlay's ``app/``
    (``enterprise/app``, ``ee/app``) with their ``concerns``, ``lib`` and an
    overlay's ``lib``, plus a literal path ``config/application.rb`` adds to
    the autoload or eager-load paths.
    """
    roots = _app_children(repo, "app")
    overlays = sorted(
        child.name
        for child in repo.iterdir()
        if child.is_dir() and child.name != "app" and (child / "app").is_dir()
    )
    for overlay in overlays:
        roots.extend(_app_children(repo, f"{overlay}/app"))
    for lib in ("lib", *(f"{o}/lib" for o in overlays)):
        if (repo / lib).is_dir():
            roots.append(lib)
    try:
        config = (repo / "config" / "application.rb").read_text(encoding="utf-8", errors="replace")
    except OSError:
        config = ""
    for m in _CONFIG_JOIN_RE.finditer(config):
        rel = m.group(1).strip("/")
        if (repo / rel).is_dir():
            roots.append(rel)
    for m in _CONFIG_DIR_GLOB_RE.finditer(config):
        roots.extend(_app_children(repo, m.group(1).strip("/")))
    return list(dict.fromkeys(roots))


def _is_rails_repo(repo_path: Path) -> bool:
    return (repo_path / "config" / "application.rb").is_file()


def build_rails_index(
    repo_path: Path | None, *, prune_nested_git: bool = True
) -> RailsIndex | None:
    if repo_path is None or not repo_path.is_dir():
        return None
    if not _is_rails_repo(repo_path):
        return None

    index = RailsIndex()
    repo_resolved = repo_path.resolve()
    roots = _autoload_roots(repo_resolved)

    index.autoload_roots.extend(roots)
    # Deepest root first, so a concern's own constant is the top-level one.
    # Its parent root still indexes it under ``Concerns::`` too: an overlay
    # added with ``Dir["enterprise/app/**"]`` gets no ``concerns`` root, and
    # there ``Concerns::Toolable`` is the constant that file defines.
    for root in sorted(roots, key=lambda r: -r.count("/")):
        root_path = repo_resolved / root
        for rb in iter_glob(root_path, "*.rb", prune_nested_git=prune_nested_git):
            try:
                rel = rb.relative_to(repo_resolved).as_posix()
                relative_to_root = rb.relative_to(root_path).with_suffix("").as_posix()
            except ValueError:
                continue
            index.name_to_file.setdefault(rb.stem, rel)
            index.namespace_to_file.setdefault(relative_to_root, rel)
            key = _path_key(relative_to_root)
            index.constant_to_file.setdefault(key, rel)
            index.file_to_constant.setdefault(rel, key)
    return index


def get_or_build_rails_index(ctx: ResolverContext) -> RailsIndex | None:
    cached = getattr(ctx, "_ruby_rails_index", "__sentinel__")
    if cached != "__sentinel__":
        return cached  # type: ignore[return-value]
    index = build_rails_index(ctx.repo_path, prune_nested_git=ctx.prune_nested_git)
    ctx._ruby_rails_index = index  # type: ignore[attr-defined]
    return index
