"""Which changed files the diff-risk rules read, and what each one is."""

from __future__ import annotations

from pathlib import PurePosixPath

from ...code_origin import code_origin
from ...references import path_identity
from ..change_health.analyzer import language_of
from . import markers as m
from .models import FileTexts

HEALTH_RULES_PATH = ".repowise/health-rules.json"

#: How much of a file the origin classifier reads.
_HEADER_CHARS = 4096


def is_prose(path: str) -> bool:
    return path.lower().endswith(m.PROSE_SUFFIXES)


def threshold_file(path: str) -> m.ThresholdFile | None:
    name = PurePosixPath(path).name
    return next((t for t in m.THRESHOLD_FILES if t.matches(name)), None)


def is_health_rules(path: str) -> bool:
    return path_identity(path) == HEALTH_RULES_PATH


def is_relevant(path: str) -> bool:
    """Whether a rule reads *path*. A caller fetching bytes fetches only these."""
    if _in_fixture_or_generated_dir(path):
        return False
    return (
        language_of(path) is not None
        or is_prose(path)
        or threshold_file(path) is not None
        or is_health_rules(path)
    )


def in_scope(file: FileTexts) -> bool:
    """A relevant file that neither a tool nor a third party wrote."""
    return bool(file.path) and is_relevant(file.path) and not _held(file)


def _in_fixture_or_generated_dir(path: str) -> bool:
    p = PurePosixPath(path_identity(path))
    return bool(m.GOLDEN_NAME.search(p.name)) or any(
        part in m.FIXTURE_OR_GENERATED_DIRS for part in p.parts[:-1]
    )


def _held(file: FileTexts) -> bool:
    """Vendored or generated: a tool or a third party wrote the markers."""
    return _origin(file) in ("vendored", "generated")


def is_production(file: FileTexts) -> bool:
    """Product code: not a tool config or settings module, not tooling or a build file."""
    return not is_tool_config(file.path) and _origin(file) == "production"


def _origin(file: FileTexts) -> str:
    return code_origin(file.path, (file.head or file.base or "")[:_HEADER_CHARS])


def is_tool_config(path: str) -> bool:
    """A tool config (``vite.config.ts``, ``.eslintrc.js``) or a settings module.

    A test-environment check is normal there.
    """
    p = PurePosixPath(path)
    name = p.name
    dot_rc = name.startswith(".") and name[1:].split(".", 1)[0].endswith("rc")
    settings = p.stem in {"settings", "config", "conftest"} or any(
        part in {"settings", "config", "configs"} for part in p.parts[:-1]
    )
    return ".config." in name or ".conf." in name or dot_rc or settings
