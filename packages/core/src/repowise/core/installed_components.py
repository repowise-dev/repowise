"""Files a component CLI copied into the repository, read from the CLI's config.

shadcn/ui's ``add`` command writes its primitive wrappers (``dialog.tsx``,
``context-menu.tsx``) into the directory that ``components.json`` names as the
``ui`` alias. The copies are near-identical to one another by design, so a
clone detector reads the kit as duplication. Two pieces of evidence must agree:
the config file declares the directory, and the file carries a registry
component's name directly in it. A ``components/ui`` folder with no
``components.json`` above it, and a team's own component, story or test kept
beside the kit (``PathTooltip.tsx``, ``button.stories.tsx``), stay production.
"""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath

__all__ = ["is_installed_component"]

_CONFIG = "components.json"
# Where each path alias a ``components.json`` uses points, relative to the
# config's directory: the tsconfig ``@/*`` / ``~/*`` alias maps to the project
# root or its ``src/``.
_ALIAS_ROOTS: dict[str, tuple[str, ...]] = {"@/": ("", "src/"), "~/": ("", "src/")}
# The file names ``shadcn add`` writes into the ``ui`` directory.
_REGISTRY_UI_NAMES = frozenset(
    {
        "accordion", "alert", "alert-dialog", "aspect-ratio", "avatar", "badge",
        "breadcrumb", "button", "button-group", "calendar", "card", "carousel",
        "chart", "checkbox", "collapsible", "combobox", "command", "context-menu",
        "dialog", "drawer", "dropdown-menu", "empty", "field", "form", "hover-card",
        "input", "input-group", "input-otp", "item", "kbd", "label", "menubar",
        "native-select", "navigation-menu", "pagination", "popover", "progress",
        "radio-group", "resizable", "scroll-area", "select", "separator", "sheet",
        "sidebar", "skeleton", "slider", "sonner", "spinner", "switch", "table",
        "tabs", "textarea", "toast", "toaster", "toggle", "toggle-group", "tooltip",
        "use-toast",
    }
)
_COMPONENT_SUFFIXES = frozenset({".tsx", ".ts", ".jsx", ".js"})


def _aliases(config_dir: Path) -> dict:
    """The ``aliases`` table of a ``components.json`` in *config_dir*, or empty."""
    try:
        data = json.loads((config_dir / _CONFIG).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    aliases = data.get("aliases") if isinstance(data, dict) else None
    return aliases if isinstance(aliases, dict) else {}


def _ui_alias(aliases: dict) -> str | None:
    """The ``ui`` alias; configs written before it existed put the kit under
    ``<components>/ui``."""
    ui = aliases.get("ui")
    if isinstance(ui, str):
        return ui
    components = aliases.get("components")
    return f"{components}/ui" if isinstance(components, str) else None


def _ui_dirs(config_dir: Path) -> tuple[PurePosixPath, ...]:
    """The ``ui`` directories a ``components.json`` in *config_dir* declares,
    relative to *config_dir*; empty when there is no readable config."""
    ui = _ui_alias(_aliases(config_dir))
    for prefix, roots in _ALIAS_ROOTS.items():
        if ui is not None and ui.startswith(prefix):
            rest = ui[len(prefix) :].strip("/")
            return tuple(PurePosixPath(root + rest) for root in roots)
    return ()


def is_installed_component(
    repo_root: str | os.PathLike[str],
    rel_path: str,
    cache: dict[str, tuple[PurePosixPath, ...]],
) -> bool:
    """Whether *rel_path* is a registry component directly in a ``ui``
    directory that a ``components.json`` in one of its ancestors declares.

    *cache* maps a repo-relative directory to its declared ``ui`` directories,
    so each directory's config is read once per pass.
    """
    path = PurePosixPath(rel_path.replace("\\", "/"))
    if path.suffix not in _COMPONENT_SUFFIXES or path.stem not in _REGISTRY_UI_NAMES:
        return False
    for base in path.parents:
        key = base.as_posix()
        if key not in cache:
            cache[key] = _ui_dirs(Path(repo_root, key))
        if any(path.parent == base / ui for ui in cache[key]):
            return True
    return False
