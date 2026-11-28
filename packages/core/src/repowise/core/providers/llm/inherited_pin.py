"""Telling a provider a repo chose from one its first index copied down.

Until #5709 every first index wrote the provider and model it ran on into the
repo's ``.repowise/config.yaml``, whether the repo chose them or inherited the
shared default. Both wrote the same lines, so a repo indexed before that fix
is reviewed once, by what can still be checked:

* ``state.json`` names the provider and model the first index ran on. A
  ``config.yaml`` that names something else was edited afterwards (Settings,
  ``repowise config``, by hand), which is a choice. It is kept.
* When the two agree, the lines came from the index. They are removed only
  while they also equal the shared default, where removing them changes
  nothing today and lets the repo follow the default from then on. Lines that
  differ from the default are kept: a ``--provider`` flag and a default that
  has since moved look the same, and guessing wrong would switch a repo's
  provider behind its owner's back.

The review is stamped in ``state.json`` and never repeated, so a provider the
repo is given later is never mistaken for an inherited one, even when it
equals the default.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from repowise.core.providers.llm.resolve import (
    _clean,
    _parse_json,
    cached_read,
    inherits_default,
    read_repo_config,
)

logger = logging.getLogger(__name__)

#: ``state.json`` key: this repo's provider lines are known to be its own.
PIN_REVIEWED_KEY = "provider_choice_reviewed"


def release_inherited_pin(repo_path: Path | str | None) -> bool:
    """Review *repo_path* once; ``True`` when its inherited provider lines were removed.

    Cheap after the first call: one cached read of ``state.json``. A repo with
    no ``state.json`` has not been indexed, so there is nothing to review.
    """
    if repo_path is None:
        return False
    state_path = Path(repo_path) / ".repowise" / "state.json"
    state = cached_read(state_path, _parse_json)
    if not state or state.get(PIN_REVIEWED_KEY):
        return False
    try:
        released = _review(Path(repo_path), state, state_path)
    except Exception:
        # The review is an upgrade step: failing it must not fail a resolve.
        logger.warning("Could not review the provider recorded for %s", repo_path, exc_info=True)
        return False
    if released:
        logger.info(
            "%s now follows the default provider: the provider in its config.yaml "
            "was the default its first index copied down",
            repo_path,
        )
    return released


def _review(repo_path: Path, state: dict[str, Any], state_path: Path) -> bool:
    from repowise.core.fsutils import atomic_write_text
    from repowise.core.repo_config import (
        carry_config_fingerprints,
        config_dependency_fingerprints,
        config_fingerprint,
        load_repo_config,
        save_repo_config,
    )

    released = _index_wrote_the_default(repo_path, state)
    if released:
        before = (config_fingerprint(repo_path), config_dependency_fingerprints(repo_path))
        config = load_repo_config(repo_path)
        config.pop("provider", None)
        config.pop("model", None)
        save_repo_config(repo_path, config)
        # The repo resolves to the same provider and model as before.
        carry_config_fingerprints(repo_path, before)
    # Re-read: carrying the fingerprints may just have rewritten the file.
    current = json.loads(state_path.read_text(encoding="utf-8"))
    current[PIN_REVIEWED_KEY] = True
    atomic_write_text(state_path, json.dumps(current, indent=2))
    return released


def _index_wrote_the_default(repo_path: Path, state: dict[str, Any]) -> bool:
    config = read_repo_config(repo_path)
    provider, model = _clean(config.get("provider")), _clean(config.get("model"))
    if provider is None:
        return False
    if (provider, model) != (_clean(state.get("provider")), _clean(state.get("model"))):
        return False
    return inherits_default(repo_path, provider, model)


__all__ = ["PIN_REVIEWED_KEY", "release_inherited_pin"]
