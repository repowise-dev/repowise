"""Rails / Zeitwerk autoload index for Ruby import resolution.

Real Rails apps barely use ``require``; class references like
``UserController`` or ``Admin::Reports::Daily`` are resolved at runtime by
Zeitwerk's convention: ``CamelCase`` constants live at
``snake_case.rb`` files under one of a fixed set of autoload roots
(``app/*``, ``lib/``).

This index is detection-gated: returns ``None`` unless ``config/application.rb``
exists. Non-Rails Ruby repos pay zero cost.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

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


@dataclass
class RailsIndex:
    autoload_roots: list[str] = field(default_factory=list)
    name_to_file: dict[str, str] = field(default_factory=dict)
    namespace_to_file: dict[str, str] = field(default_factory=dict)

    def lookup(self, constant: str) -> str | None:
        """Resolve a constant reference (``UserController`` or ``Foo::Bar``)
        to a repo-relative ``.rb`` path.
        """
        if "::" in constant:
            parts = constant.split("::")
            namespaced_path = "/".join(camel_to_snake(p) for p in parts)
            hit = self.namespace_to_file.get(namespaced_path)
            if hit:
                return hit
            # Fall back to last segment lookup
            constant = parts[-1]
        snake = camel_to_snake(constant)
        return self.name_to_file.get(snake)


_DEFAULT_AUTOLOAD_ROOTS = (
    "app/models",
    "app/controllers",
    "app/services",
    "app/jobs",
    "app/mailers",
    "app/helpers",
    "app/channels",
    "app/policies",
    "lib",
)


def _is_rails_repo(repo_path: Path) -> bool:
    return (repo_path / "config" / "application.rb").is_file()


def build_rails_index(repo_path: Path | None) -> RailsIndex | None:
    if repo_path is None or not repo_path.is_dir():
        return None
    if not _is_rails_repo(repo_path):
        return None

    index = RailsIndex()
    repo_resolved = repo_path.resolve()

    for root in _DEFAULT_AUTOLOAD_ROOTS:
        root_path = repo_resolved / root
        if not root_path.is_dir():
            continue
        index.autoload_roots.append(root)
        for rb in root_path.rglob("*.rb"):
            try:
                rel = rb.relative_to(repo_resolved).as_posix()
            except ValueError:
                continue
            stem = rb.stem
            # Path under the autoload root (without extension)
            try:
                relative_to_root = rb.relative_to(root_path).with_suffix("").as_posix()
            except ValueError:
                continue
            index.name_to_file.setdefault(stem, rel)
            index.namespace_to_file.setdefault(relative_to_root, rel)
    return index


def get_or_build_rails_index(ctx: "ResolverContext") -> RailsIndex | None:
    cached = getattr(ctx, "_ruby_rails_index", "__sentinel__")
    if cached != "__sentinel__":
        return cached  # type: ignore[return-value]
    index = build_rails_index(ctx.repo_path)
    ctx._ruby_rails_index = index  # type: ignore[attr-defined]
    return index
