"""``.repowise/state.json`` persistence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from repowise.cli._helpers.paths import STATE_FILENAME, ensure_repowise_dir, get_repowise_dir

# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------


def load_state(repo_path: Path) -> dict[str, Any]:
    """Load ``.repowise/state.json`` or return an empty dict if absent."""
    state_path = get_repowise_dir(repo_path) / STATE_FILENAME
    if state_path.exists():
        return json.loads(state_path.read_text(encoding="utf-8"))
    return {}


#: Slots the last whole-repo generation put in front of this repository's
#: signals, which is not the same as the slots that produced a page. The two
#: differ, and only the first answers "has this index ever been offered an
#: Active Landscape?".
ONBOARDING_SLOTS_OFFERED_KEY = "onboarding_slots_offered"


def stamp_offered_slots(state: dict[str, Any], *, enabled: bool = True) -> None:
    """Record which onboarding slots this whole-repo run evaluated.

    Only a run that generates the whole repository may call this: the slot
    gates read whole-repo signals, so a scoped run that saw one changed file
    has not offered anything to anything.

    Written because a missing onboarding row has two causes that look identical
    in a store and want opposite responses. A slot registered after this index
    was built has never been evaluated here, and ``update --full`` would build
    it; a slot that *was* evaluated and whose gate refused the repository will
    be refused again by the same signals, and telling the user to spend a model
    run on it is a lie. Measured on ``test-repos/microdot``: some registered
    slots (``getting_started``, ``active_landscape``) are gate-skipped on every
    run, full or fresh. A notice driven by the rows alone would name them
    forever, and none of them would ever arrive.

    ``enabled`` is the run's ``enable_onboarding``. A run with onboarding off
    offered nothing, and recording otherwise would silence the notice for a
    user who later turns it on.
    """
    from repowise.core.generation.onboarding import iter_specs

    state[ONBOARDING_SLOTS_OFFERED_KEY] = (
        sorted(spec.slot for spec in iter_specs()) if enabled else []
    )


def save_state(repo_path: Path, state: dict[str, Any], *, full_index: bool = False) -> None:
    """Write *state* to ``.repowise/state.json``.

    Every persist stamps the store-format markers (``store_format_version`` and
    the ``written_by_version`` package version that wrote it) so the upgrade
    layer always has a current record of the store's shape and provenance.

    ``full_index`` marks a persist that follows a full-repo (re)generation, so
    the store may be stamped to the terminal store-format version. A routine
    incremental persist leaves it False and the version clamps below any
    ``REINDEX_RECOMMENDED`` gate, keeping a reindex recommendation alive until
    an actual re-index clears it.

    A write that changes ``last_sync_commit`` moves the repository row's
    ``head_commit`` to the same commit, so the two stamps never disagree.
    """
    from repowise.core.workspace.update import read_state_commit, restamp_changed_sync_commit

    ensure_repowise_dir(repo_path)
    previous = read_state_commit(Path(repo_path))
    try:
        from repowise.cli import __version__ as _pkg_version
        from repowise.core.index_scope import clean_legacy_degraded_state
        from repowise.core.upgrade import stamp as _stamp_store_version

        clean_legacy_degraded_state(state)
        _stamp_store_version(state, package_version=_pkg_version, full_index=full_index)
    except Exception:  # never let stamping block a persist
        pass
    from repowise.core.fsutils import atomic_write_text

    # Atomic so a crash mid-write can never leave a truncated state.json —
    # every later update would fail to parse it and demand a full re-init.
    state_path = get_repowise_dir(repo_path) / STATE_FILENAME
    atomic_write_text(state_path, json.dumps(state, indent=2))
    restamp_changed_sync_commit(Path(repo_path), previous, state.get("last_sync_commit"))
