"""Risk factors for dead-code findings — soft signals that a file is more
likely loaded at runtime than a static-reachability graph can observe.

The ``_NEVER_FLAG_PATTERNS`` allowlist in :mod:`constants` removes files we
are *confident* are framework/config/generated (``alembic/versions/*``,
``*.config.*``, ``page.tsx``, …). This module is the softer, complementary
layer: a file that *escaped* that allowlist but still looks like a config /
bootstrap / database / environment / script / runtime-asset file. Such a file
is not removed from the report, since surfacing likely-unused code is the
whole point. It is capped below the deletion-ready threshold and tagged with
the risk factors that explain why, so the UI can present it as a *candidate to
review* rather than as *safe to delete*.

Static reachability + git age cannot *prove* a file is unused; it can only
say nothing imports it. For ordinary modules that is a strong signal. For a
``database/environment.db.js`` bootstrap file — exactly the class of file that
is wired up by a runtime loader, a config key, or a string path rather than a
static import — it is not. This module encodes that asymmetry.

Note which way git age points for these files. Confidence rises the longer a
file goes untouched, but a runtime-loaded asset is written once and then
correct forever, so the top confidence tier is precisely where it lands. Age
is evidence of deadness only for a file that something would have had to
import in the first place.

The classifier is pure path inspection, so it can run both at analysis time
(to cap the persisted finding) and at read time (to defensively re-derive
effective safety for findings persisted before this logic existed).
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

# Findings at or above this confidence, with no risk factors, are presented as
# deletion-ready ("safe to delete"). Anything below — or anything carrying a
# risk factor — is a review candidate. Kept here as the single source of truth
# so the analyzer and every read-time consumer agree.
SAFE_CONFIDENCE_THRESHOLD: float = 0.7

# Confidence ceiling applied to a finding that carries a runtime-load risk
# factor. 0.4 is the default ``min_confidence`` floor across CLI, REST router,
# and MCP tools (which import this value as their single source of truth), so the
# finding still surfaces (as a medium / review-required candidate) but never
# reads as deletion-ready.
RISK_CAP_CONFIDENCE: float = 0.4

# Filename-stem tokens → risk-factor tag. Matched against the basename split on
# ``. _ -`` so ``environment.db.js`` yields {environment, db, js} → environment
# + database. Deliberately curated: broad identifiers (app, main, index, core,
# base, util) are excluded because they would cap ordinary modules.
_FILENAME_RISK_TOKENS: dict[str, str] = {
    # config
    "config": "config",
    "configs": "config",
    "configuration": "config",
    "conf": "config",
    "settings": "config",
    "setting": "config",
    "setup": "config",
    # environment
    "env": "environment",
    "environment": "environment",
    "environ": "environment",
    "dotenv": "environment",
    # bootstrap / runtime entry
    "bootstrap": "bootstrap",
    "startup": "bootstrap",
    "entrypoint": "bootstrap",
    # database
    "database": "database",
    "db": "database",
    "schema": "database",
    "seed": "database",
    "seeds": "database",
    "migration": "database",
    "migrations": "database",
    "datastore": "database",
    "sqlite": "database",
    # runtime-loaded web asset
    #
    # The sharpest case in this module. A service worker is registered by URL,
    # so nothing imports it, and it is written once and then correct forever,
    # so it ages into the top confidence tier. Both halves of the evidence
    # point at "delete me" for a file that must not be deleted.
    #
    # Only ``sw``, covering ``sw.js`` and ``firebase-messaging-sw.js``. A bare
    # ``worker`` token would catch every ``queue_worker.py`` and thread-pool
    # module in the world, so the hyphenated ``service-worker`` spelling is
    # handled as a token pair below instead. ``manifest`` is deliberately
    # absent: ``manifest.json`` and ``.webmanifest`` are non-code languages and
    # never become findings, and ``*/manifest.ts`` is already in
    # ``_NEVER_FLAG_PATTERNS``, so the token's only remaining reach would be
    # real modules like ``manifest.rs``.
    "sw": "asset",
}

# Filename token *pairs* → risk-factor tag. ``_SPLIT_RE`` splits on ``. _ -``,
# so ``service-worker.ts`` and ``service_worker.js`` both yield the pair below
# while ``queue_worker.py`` does not.
_FILENAME_RISK_TOKEN_PAIRS: dict[frozenset[str], str] = {
    frozenset({"service", "worker"}): "asset",
}

# Directory-segment tokens → risk-factor tag. A file *inside* one of these
# directories inherits the tag even when its own name is generic.
_DIRECTORY_RISK_TOKENS: dict[str, str] = {
    "config": "config",
    "configs": "config",
    "settings": "config",
    "env": "environment",
    "environments": "environment",
    "bootstrap": "bootstrap",
    "database": "database",
    "db": "database",
    "migrations": "database",
    "scripts": "script",
    "bin": "script",
    "tasks": "script",
    # Served verbatim rather than bundled, so nothing in the tree imports it.
    # ``assets`` is deliberately absent: ``src/assets/`` is the Vite / Vue /
    # Angular convention for *bundled* source, which is imported normally.
    "public": "asset",
    "static": "asset",
    "www": "asset",
}

# Human-readable one-liners per factor, used to build evidence strings.
_FACTOR_BLURB: dict[str, str] = {
    "config": "configuration",
    "environment": "environment/bootstrap",
    "bootstrap": "bootstrap/entry-point",
    "database": "database/schema",
    "script": "script/task",
    "asset": "runtime-loaded web asset",
}

_SPLIT_RE = re.compile(r"[._\-]+")


def path_risk_factors(file_path: str) -> tuple[str, ...]:
    """Return the sorted, de-duplicated runtime-load risk factors for *file_path*.

    Empty tuple means no risk factor — an ordinary module. A non-empty result
    means the file looks like the kind of thing that is referenced outside
    normal static imports (config, bootstrap, database, environment, script,
    runtime asset), so a "no importers" finding for it is weaker evidence than
    it looks.
    """
    if not file_path:
        return ()

    norm = file_path.replace("\\", "/")
    p = PurePosixPath(norm)
    factors: set[str] = set()

    # Filename tokens (includes the extension, which never matches a token).
    tokens = set(_SPLIT_RE.split(p.name.lower()))
    for token in tokens:
        tag = _FILENAME_RISK_TOKENS.get(token)
        if tag:
            factors.add(tag)

    # Token pairs, for names whose signal is the combination rather than any
    # one word (``service-worker`` vs an ordinary ``worker``).
    for pair, tag in _FILENAME_RISK_TOKEN_PAIRS.items():
        if pair <= tokens:
            factors.add(tag)

    # Directory segments.
    for segment in p.parent.parts:
        tag = _DIRECTORY_RISK_TOKENS.get(segment.lower())
        if tag:
            factors.add(tag)

    return tuple(sorted(factors))


def risk_evidence(factors: tuple[str, ...] | list[str]) -> str | None:
    """Build a single human-readable evidence line for *factors*, or None."""
    if not factors:
        return None
    blurbs = ", ".join(_FACTOR_BLURB.get(f, f) for f in factors)
    return (
        f"Runtime-load risk ({blurbs}): files of this kind are often referenced "
        "outside static imports — review before deleting"
    )


def effective_safe_to_delete(
    confidence: float,
    file_path: str,
    stored_safe: bool = True,
) -> bool:
    """Re-derive whether a finding is genuinely deletion-ready.

    Monotonic — only ever *downgrades* the stored flag, never upgrades it, so
    it is safe to apply to findings persisted before risk factors existed:

    - never True when ``stored_safe`` is already False,
    - never True below :data:`SAFE_CONFIDENCE_THRESHOLD`,
    - never True when the path carries any runtime-load risk factor.
    """
    if not stored_safe:
        return False
    if confidence < SAFE_CONFIDENCE_THRESHOLD:
        return False
    return not path_risk_factors(file_path)
