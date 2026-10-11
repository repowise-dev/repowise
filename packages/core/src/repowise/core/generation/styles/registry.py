"""Style registry — declares the built-in styles and resolves a name to a StyleSpec.

Resolution order is built-in first, then a user-defined custom style under
``.repowise/styles/<name>/`` (a power-user feature). ``comprehensive`` is the
canonical default and fallback.

Custom styles are plain data: a ``style.yaml`` describing the voice plus an
optional ``templates/`` directory (Layer 2). User-supplied text is treated as
untrusted: names are pattern-validated to prevent path traversal, and directive
text is length-bounded so a runaway file can't balloon every prompt.
"""

from __future__ import annotations

import re
from pathlib import Path

import structlog

from . import directives as d
from .spec import StyleSpec

log = structlog.get_logger(__name__)

DEFAULT_STYLE = "comprehensive"

# Custom-style guard rails.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_MAX_DIRECTIVE_CHARS = 8000
_STYLES_DIRNAME = "styles"

# ---------------------------------------------------------------------------
# Built-in styles (see WIKI_STYLES_PLAN.md D1). ``comprehensive`` is inert by
# design — empty directive and note — so it reproduces the pre-feature output
# byte-for-byte and never invalidates existing cached pages.
# ---------------------------------------------------------------------------

_BUILTIN_STYLES: dict[str, StyleSpec] = {
    "comprehensive": StyleSpec(
        name="comprehensive",
        description="Full, narrative documentation for humans and AI (default).",
    ),
    "caveman": StyleSpec(
        name="caveman",
        description="Token-condensed, AI-first pages — terse fragments, ~70% smaller.",
        user_directive=d.CAVEMAN_DIRECTIVE,
        system_note=d.CAVEMAN_SYSTEM_NOTE,
        onboarding_condenses=True,
    ),
    "reference": StyleSpec(
        name="reference",
        description="API-manual style — signature-dense, exhaustive, minimal narrative.",
        user_directive=d.REFERENCE_DIRECTIVE,
        system_note=d.REFERENCE_SYSTEM_NOTE,
        onboarding_condenses=False,
    ),
    "tutorial": StyleSpec(
        name="tutorial",
        description="Guided, beginner-friendly walkthroughs that teach the codebase.",
        user_directive=d.TUTORIAL_DIRECTIVE,
        system_note=d.TUTORIAL_SYSTEM_NOTE,
        onboarding_condenses=True,
    ),
}


def _styles_root(repo_path: Path | str | None) -> Path | None:
    """Return the ``.repowise/styles`` directory for a repo, or None."""
    if repo_path is None:
        return None
    return Path(repo_path) / ".repowise" / _STYLES_DIRNAME


def _clip(value: object) -> str:
    """Coerce to a length-bounded string (untrusted directive/note text)."""
    text = str(value or "")
    return text[:_MAX_DIRECTIVE_CHARS]


def _load_custom_style(name: str, repo_path: Path | str | None) -> StyleSpec | None:
    """Load a user-defined style from ``.repowise/styles/<name>/style.yaml``.

    Returns None when the repo, directory, or manifest is absent or unreadable, or
    when *name* is not a safe identifier. Never raises — a broken custom style
    degrades to "not found" (callers fall back to the default).
    """
    if not _NAME_RE.match(name):
        return None
    root = _styles_root(repo_path)
    if root is None:
        return None
    style_dir = root / name
    manifest = style_dir / "style.yaml"
    if not manifest.is_file():
        return None

    try:
        import yaml

        data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # malformed YAML / IO error → treat as absent
        log.warning("custom_style_unreadable", name=name, error=str(exc))
        return None
    if not isinstance(data, dict):
        return None

    user_directive = _clip(data.get("user_directive"))
    system_note = _clip(data.get("system_note"))
    # A custom style with no voice is pointless (and would be inert); reject so the
    # caller falls back rather than silently producing default output.
    if not user_directive and not system_note:
        log.warning("custom_style_empty", name=name)
        return None

    templates_dir = style_dir / "templates"
    template_dir = templates_dir if templates_dir.is_dir() else None

    try:
        version = int(data.get("style_version", 1))
    except (TypeError, ValueError):
        version = 1

    return StyleSpec(
        name=name,
        description=str(data.get("description") or f"Custom style '{name}'."),
        is_builtin=False,
        user_directive=user_directive,
        system_note=system_note,
        onboarding_condenses=bool(data.get("onboarding_condenses", False)),
        template_dir=template_dir,
        style_version=version,
    )


def list_styles(repo_path: Path | str | None = None) -> list[StyleSpec]:
    """Return all available styles: built-ins plus discovered custom styles."""
    styles = list(_BUILTIN_STYLES.values())
    root = _styles_root(repo_path)
    if root is not None and root.is_dir():
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name in _BUILTIN_STYLES:
                continue
            spec = _load_custom_style(child.name, repo_path)
            if spec is not None:
                styles.append(spec)
    return styles


def is_known_style(name: str | None, repo_path: Path | str | None = None) -> bool:
    """True when *name* resolves to a built-in or a valid custom style."""
    if not name:
        return False
    key = name.strip().lower()
    if key in _BUILTIN_STYLES:
        return True
    return _load_custom_style(key, repo_path) is not None


def resolve_style(name: str | None, repo_path: Path | str | None = None) -> StyleSpec:
    """Resolve a style name to a :class:`StyleSpec`, defaulting to ``comprehensive``.

    Built-ins win; then a custom ``.repowise/styles/<name>/`` style; otherwise the
    default. Unknown names fall back rather than raising — generation should never
    hard-fail on a stale or mistyped config value; the CLI/API validate up front
    where a crisp error is more useful.
    """
    key = (name or DEFAULT_STYLE).strip().lower()
    builtin = _BUILTIN_STYLES.get(key)
    if builtin is not None:
        return builtin
    custom = _load_custom_style(key, repo_path)
    if custom is not None:
        return custom
    return _BUILTIN_STYLES[DEFAULT_STYLE]
