"""Paths a repository's own secret-scanner config says hold no real secrets.

Read from ``.gitleaks.toml`` at the repository root, the file gitleaks itself
loads: the global ``[allowlist]`` table and each ``[[allowlists]]`` entry. Its
``paths`` are matched as gitleaks does, an unanchored regex search over the
repo-relative POSIX path. ``[extend] useDefault = true`` also brings in the
paths of gitleaks' default allowlist (:data:`_GITLEAKS_DEFAULT_PATHS`).

An entry is honoured only when its path alone allows a finding, as in gitleaks:
one scoped by ``targetRules`` names gitleaks rule ids this scanner does not
have, and one with ``condition = "AND"`` beside ``commits``, ``regexes`` or
``stopwords`` needs a check a path cannot answer. Ceiling: ``regexes`` and
``stopwords`` test the matched secret value and ``commits`` a history scan's
commit, so they are not read; ``[extend] path``, ``.gitleaksignore`` (keyed on
gitleaks rule ids) and other scanners' ignore files (detect-secrets baselines,
``.gitguardian.yaml``) are not read either.

Go RE2 and Python ``re`` mostly agree on the shapes such paths use; where they
differ, matching more than gitleaks would silence a real secret. So patterns
compile with ``re.ASCII`` (RE2's ``\\w``, ``\\d`` and case folding are ASCII),
and a pattern Python reads differently or cannot compile is skipped, so it
silences nothing: a POSIX class ``[[:alpha:]]`` (a plain set to Python), a
mid-pattern ``(?i)``, ``\\z``, ``\\Q...\\E``.
"""

from __future__ import annotations

import logging
import re
import tomllib
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CONFIG_FILENAME = ".gitleaks.toml"

#: The ``paths`` of the global allowlist in gitleaks' default config
#: (``config/gitleaks.toml``, v8.30), applied when a repo's config extends it.
_GITLEAKS_DEFAULT_PATHS = (
    r"gitleaks\.toml",
    r"(?i)\.(?:bmp|gif|jpe?g|png|svg|tiff?)$",
    r"(?i)\.(?:eot|[ot]tf|woff2?)$",
    r"(?i)\.(?:docx?|xlsx?|pdf|bin|socket|vsidx|v2|suo|wsuo|.dll|pdb|exe|gltf)$",
    r"go\.(?:mod|sum|work(?:\.sum)?)$",
    r"(?:^|/)vendor/modules\.txt$",
    r"(?:^|/)vendor/(?:github\.com|golang\.org/x|google\.golang\.org|gopkg\.in|istio\.io"
    r"|k8s\.io|sigs\.k8s\.io)(?:/.*)?$",
    r"(?:^|/)gradlew(?:\.bat)?$",
    r"(?:^|/)gradle\.lockfile$",
    r"(?:^|/)mvnw(?:\.cmd)?$",
    r"(?:^|/)\.mvn/wrapper/MavenWrapperDownloader\.java$",
    r"(?:^|/)node_modules(?:/.*)?$",
    r"(?:^|/)(?:deno\.lock|npm-shrinkwrap\.json|package-lock\.json|pnpm-lock\.yaml|yarn\.lock)$",
    r"(?:^|/)bower_components(?:/.*)?$",
    r"(?:^|/)(?:angular|bootstrap|jquery(?:-?ui)?|plotly|swagger-?ui)[a-zA-Z0-9.-]*(?:\.min)?\.js(?:\.map)?$",
    r"(?:^|/)javascript\.json$",
    r"(?:^|/)(?:Pipfile|poetry)\.lock$",
    r"(?i)(?:^|/)(?:v?env|virtualenv)/lib(?:64)?(?:/.*)?$",
    r"(?i)(?:^|/)(?:lib(?:64)?/python[23](?:\.\d{1,2})+|python/[23](?:\.\d{1,2})+/lib(?:64)?)(?:/.*)?$",
    r"(?i)(?:^|/)[a-z0-9_.]+-[0-9.]+\.dist-info(?:/.+)?$",
    r"(?:^|/)vendor/(?:bundle|ruby)(?:/.*?)?$",
    r"\.gem$",
    r"verification-metadata\.xml",
    r"Database.refactorlog",
    r"(?:^|/)\.git$",
)

_NOT_BY_PATH_ALONE = ("commits", "regexes", "stopwords")


def _no_path(path: str) -> bool:
    return False


def secret_path_allowlist(root: str | Path | None) -> Callable[[str], bool]:
    """Whether a repo-relative path is one *root*'s secret-scanner config allowlists.

    No config, or one that does not parse, allowlists nothing.
    """
    if root is None:
        return _no_path
    try:
        config = tomllib.loads(Path(root, CONFIG_FILENAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return _no_path
    patterns = [*_compiled(_allowed_paths(config))]
    if not patterns:
        return _no_path
    return lambda path: any(p.search(path) for p in patterns)


def _allowed_paths(config: Mapping[str, Any]) -> Iterator[str]:
    """Every ``paths`` entry that allows a finding on its own."""
    extend = config.get("extend")
    if isinstance(extend, Mapping) and extend.get("useDefault") is True:
        yield from _GITLEAKS_DEFAULT_PATHS
    entries = config.get("allowlists")
    entries = [*entries] if isinstance(entries, list) else []
    entries.append(config.get("allowlist"))
    for entry in entries:
        if isinstance(entry, Mapping) and _allows_by_path_alone(entry):
            paths = entry.get("paths")
            if isinstance(paths, list):
                yield from (p for p in paths if isinstance(p, str))


def _allows_by_path_alone(entry: Mapping[str, Any]) -> bool:
    if entry.get("targetRules"):
        return False
    condition = str(entry.get("condition", "")).upper()
    return condition not in ("AND", "&&") or not any(entry.get(k) for k in _NOT_BY_PATH_ALONE)


def _compiled(paths: Iterator[str]) -> Iterator[re.Pattern[str]]:
    for raw in paths:
        if "[[:" in raw:
            logger.debug("gitleaks allowlist path %r skipped: POSIX class", raw)
            continue
        try:
            yield re.compile(raw, re.ASCII)
        except re.error as exc:
            logger.debug("gitleaks allowlist path %r skipped: %s", raw, exc)
