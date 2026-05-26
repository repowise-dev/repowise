"""File-category classification for category-adaptive documentation.

A ``file_page`` is mostly source code, but a migration, a CI workflow, a
schema, or a config file deserve a different summary voice than a service
class. :func:`file_category` assigns one of a small set of categories from a
file's path and language; the ``file_page`` template branches its guidance on
the result so a migration reads like data and a workflow reads like a pipeline.

Pure and deterministic — path/language string checks only.
"""

from __future__ import annotations

from pathlib import PurePosixPath

# Categories the file_page prompt knows how to adapt to.
CATEGORY_CODE = "code"
CATEGORY_CONFIG = "config"
CATEGORY_DOC = "doc"
CATEGORY_DATA = "data"
CATEGORY_PIPELINE = "pipeline"

_DOC_SUFFIXES = frozenset({".md", ".mdx", ".rst", ".txt", ".adoc"})
_CONFIG_LANGUAGES = frozenset({"yaml", "toml", "json", "ini", "properties", "hcl"})
_DATA_DIR_TOKENS = frozenset({"migrations", "versions", "models", "schema", "schemas", "entities"})
_DATA_SUFFIXES = frozenset({".sql", ".prisma", ".graphql", ".proto"})
_PIPELINE_PATH_HINTS = (
    ".github/workflows/",
    ".gitlab-ci",
    "jenkinsfile",
    "azure-pipelines",
    ".circleci/",
    "/pipelines/",
    "/etl/",
)


def file_category(path: str, language: str = "", *, is_config: bool = False) -> str:
    """Return the documentation category for *path*.

    Resolution order (most specific first): doc → pipeline → data → config →
    code. The default is :data:`CATEGORY_CODE`.
    """
    p = PurePosixPath(path)
    lower = path.lower()
    suffix = p.suffix.lower()

    if suffix in _DOC_SUFFIXES:
        return CATEGORY_DOC

    if any(hint in lower for hint in _PIPELINE_PATH_HINTS):
        return CATEGORY_PIPELINE

    segments = {seg.lower() for seg in p.parts[:-1]}
    if suffix in _DATA_SUFFIXES or segments & _DATA_DIR_TOKENS:
        return CATEGORY_DATA

    if is_config or (language or "").lower() in _CONFIG_LANGUAGES:
        return CATEGORY_CONFIG

    return CATEGORY_CODE
