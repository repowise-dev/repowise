"""Structural episodes: facts about the checkout, derived at index time.

These are the only episodes available on a first-ever index of a repository
with no history, no transcripts and no API key, which is the cold start every
previous shape of this idea died on.

**No new walk.** Three of the four facts are read off work the ingestion walk
already did: the nested-repo names come from the traverser's own skip counter
(:attr:`TraversalStats.nested_repo_paths`), the declared console scripts from
the ``pyproject.toml`` pass the traverser already runs in ``__init__``, and the
configuration from :func:`repo_config.load_repo_config`. The formatter check is
the one genuinely new cost in the module — one subprocess, init only, hard
timeout, and it degrades to **silence** rather than to a partial count
presented as a whole one.

Silence is the normal output: a repo where none of these hold emits nothing.
There is no "no issues found" episode.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .store import TIER_STRUCTURAL, Episode, EpisodeStore

_log = logging.getLogger(__name__)

KIND_NESTED_REPOS = "nested_repos"
KIND_EDITABLE_SHADOW = "editable_shadow"
KIND_CONFIG_OVERRIDE = "config_override"
KIND_FORMATTER_DRIFT = "formatter_drift"

#: Kinds derivable from work that has already happened. Cheap enough for the
#: update path.
FREE_KINDS: tuple[str, ...] = (KIND_NESTED_REPOS, KIND_EDITABLE_SHADOW, KIND_CONFIG_OVERRIDE)
#: Everything, including the kind that costs a subprocess. Init only.
ALL_KINDS: tuple[str, ...] = (*FREE_KINDS, KIND_FORMATTER_DRIFT)

#: Wall-clock ceiling on the formatter check. Exceeding it yields no episode.
#: A whole-repo ``ruff format --check .`` over ~2,000 files measures around a
#: second, so this is generous by an order of magnitude and still far short of
#: anything a user would notice against a full index.
FORMATTER_TIMEOUT_S = 10.0

#: Ceiling on the one-shot ``git rev-parse`` that stamps the formatter fact.
_GIT_TIMEOUT_S = 5.0

#: Nested-repo names quoted in the episode body; the count stays exact.
_MAX_NAMED_REPOS = 10

#: Repo-local virtualenv directory names. Only in-repo environments are
#: inspected: ``VIRTUAL_ENV`` on a CI or hosted indexer points at the
#: *indexer's* environment, and a fact derived from that describes the wrong
#: machine. Ceiling accepted deliberately — a developer whose venv lives
#: outside the checkout gets silence, not a wrong episode.
_VENV_DIRNAMES = (".venv", "venv")

#: Config keys that change what a command does, with the reason each matters.
#: Keyed on repowise's own config schema, so this carries to any repo.
_CONFIG_CLAIMS: tuple[tuple[str, str], ...] = (
    (
        "exclude_patterns",
        "files matching these patterns are never indexed, so the index cannot "
        "answer questions about them and their absence is not evidence they do "
        "not exist",
    ),
    (
        "mcp.tools",
        "the MCP tool surface is restricted to this list, so tools outside it "
        "are unavailable in this repo regardless of what the docs describe",
    ),
)


def derive_structural_episodes(
    repo_path: Path | str,
    traverser: Any,
    *,
    allow_formatter_check: bool,
) -> list[Episode]:
    """Derive structural episodes for *repo_path* from an exhausted *traverser*.

    *traverser* must be a :class:`~repowise.core.ingestion.FileTraverser` whose
    walk has already run — the nested-repo names are a by-product of it. Pass
    ``allow_formatter_check=False`` on any path that is not ``init``.

    Every check is independently best-effort: one that fails contributes
    nothing and does not stop the others.
    """
    root = Path(repo_path)
    episodes: list[Episode] = []
    for check, enabled in (
        (_nested_repos, True),
        (_editable_shadow, True),
        (_config_overrides, True),
        (_formatter_drift, allow_formatter_check),
    ):
        if not enabled:
            continue
        try:
            episodes.extend(check(root, traverser))
        except Exception:
            # A failing check costs its own fact and nothing else. Logged
            # because this branch is otherwise indistinguishable from the
            # fact not holding, which is the normal case.
            _log.debug("structural check failed: %s", check.__name__, exc_info=True)
            continue
    return episodes


def record_structural_episodes(
    repo_path: Path | str,
    traverser: Any,
    *,
    allow_formatter_check: bool,
) -> int:
    """Derive and persist structural episodes. Returns the number written.

    A no-op when the repo has not opted in (no ``.repowise`` directory): this
    never creates one. Best-effort throughout — the episode store is an
    enrichment, and no failure here may fail an index.
    """
    root = Path(repo_path)
    if not (root / ".repowise").is_dir():
        return 0
    kinds = ALL_KINDS if allow_formatter_check else FREE_KINDS
    try:
        episodes = derive_structural_episodes(
            root, traverser, allow_formatter_check=allow_formatter_check
        )
        with EpisodeStore.open_for_repo(root) as store:
            store.replace_kinds(tier=TIER_STRUCTURAL, kinds=kinds, episodes=episodes)
        return len(episodes)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# 1. Nested and sibling git repositories — free, from the walk's own counter
# ---------------------------------------------------------------------------


def _nested_repos(root: Path, traverser: Any) -> list[Episode]:
    stats = getattr(traverser, "stats", None)
    # dict.fromkeys rather than the raw list: the counter and the sink both
    # accumulate across walks, and a traverser walked twice would otherwise
    # name every repo twice.
    walked = sorted(dict.fromkeys(getattr(stats, "nested_repo_paths", ()) or ()))
    separate = [rel for rel in walked if _is_separate_checkout(root / rel)]
    if not separate:
        return []
    capped = bool(getattr(stats, "nested_repo_paths_truncated", False))
    named = separate[:_MAX_NAMED_REPOS]
    listed = ", ".join(named)
    if len(separate) > len(named):
        listed += f", and {len(separate) - len(named)} more"
    count = f"At least {len(separate)}" if capped else str(len(separate))
    plural = len(separate) != 1
    body = (
        f"{count} director{'ies' if plural else 'y'} inside this checkout "
        f"{'are' if plural else 'is'} a separate git repository: {listed}. "
        "Run git from inside them rather than from the root — the root's status "
        "will not show their changes, and they are not indexed as part of this repo. "
        "Declared submodules and linked worktrees of this same repository are "
        "excluded; these are independent checkouts."
    )
    return [
        Episode(
            tier=TIER_STRUCTURAL,
            kind=KIND_NESTED_REPOS,
            subject=".",
            body=body,
            evidence=f"ingestion walk stopped at {len(separate)} nested .git boundaries",
            nodes=tuple(separate),
        )
    ]


def _is_separate_checkout(path: Path) -> bool:
    """True when *path*'s ``.git`` marks a genuinely independent repository.

    The traverser stops at any ``.git`` entry, which is right for indexing and
    too broad for this claim: a ``.git`` **file** also marks a linked worktree
    of this same repository and a submodule declared in a nested (rather than
    root) ``.gitmodules``, neither of which is an independent checkout. The
    file names its gitdir, so one small read separates the three. A ``.git``
    directory is always a real repository.
    """
    marker = path / ".git"
    try:
        if marker.is_dir():
            return True
        gitdir = marker.read_text(encoding="utf-8", errors="replace")[:512]
    except OSError:
        return False
    lowered = gitdir.replace("\\", "/").lower()
    return "/worktrees/" not in lowered and "/modules/" not in lowered


# ---------------------------------------------------------------------------
# 2. An editable install shadowing a console script — half free
# ---------------------------------------------------------------------------


def _editable_shadow(root: Path, traverser: Any) -> list[Episode]:
    names = getattr(traverser, "_console_script_names", None) or frozenset()
    distributions = getattr(traverser, "_distributions", None) or frozenset()
    if not names or not distributions:
        return []
    for venv in (root / name for name in _VENV_DIRNAMES):
        if not venv.is_dir():
            continue
        pth = _editable_pth(venv, distributions)
        if pth is None:
            continue
        shadowed = sorted(
            (name, launcher) for name in names if (launcher := _launcher(venv, name)) is not None
        )
        if not shadowed:
            continue
        return [
            Episode(
                tier=TIER_STRUCTURAL,
                kind=KIND_EDITABLE_SHADOW,
                subject=name,
                body=(
                    f"`{name}` is installed as a console script in {venv.name} "
                    f"alongside an editable install ({pth}). Invoking the bare "
                    f"command can run a different copy than the source in this "
                    f"checkout; run the module through the environment's own "
                    f"interpreter (`-m`) when the distinction matters."
                ),
                evidence=f"{launcher} beside {pth}",
            )
            for name, launcher in shadowed
        ]
    return []


def _editable_pth(venv: Path, distributions: frozenset[str]) -> str | None:
    """Name of an editable ``.pth`` for one of *distributions*, or None.

    The distribution match is what makes the claim true rather than merely
    alarming: a venv with an unrelated dependency installed ``-e`` plus a
    normally-installed launcher of the same name is not a shadowed checkout,
    and without this it read as one. Editable ``.pth`` filenames embed the
    distribution (``__editable__.my_pkg-1.2.pth``), so both sides are
    normalised the way packaging does before comparing.

    One listing per site-packages directory, bounded by the venv layout —
    never a tree walk.
    """
    wanted = {_normalise_dist(dist) for dist in distributions}
    wanted.discard("")
    if not wanted:
        return None
    for site in (*venv.glob("Lib/site-packages"), *venv.glob("lib/*/site-packages")):
        for entry in site.glob("*.pth"):
            lowered = entry.name.lower()
            if "editable" not in lowered:
                continue
            normalised = _normalise_dist(entry.stem)
            if any(dist in normalised for dist in wanted):
                return entry.name
    return None


def _normalise_dist(name: str) -> str:
    """Fold a distribution or filename to compare across packaging spellings.

    ``my-pkg``, ``my_pkg`` and ``My.Pkg`` are the same distribution, and an
    editable ``.pth`` may spell it any of those ways.
    """
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _launcher(venv: Path, name: str) -> str | None:
    """The launcher *venv* installed for console script *name*, if any."""
    for scripts_dir, suffixes in ((venv / "Scripts", (".exe", "")), (venv / "bin", ("",))):
        for suffix in suffixes:
            candidate = scripts_dir / f"{name}{suffix}"
            if candidate.is_file():
                return f"{scripts_dir.name}/{candidate.name}"
    return None


# ---------------------------------------------------------------------------
# 3. Config that changes what a command does — free, via the loader
# ---------------------------------------------------------------------------


def _config_overrides(root: Path, _traverser: Any) -> list[Episode]:
    from repowise.core.repo_config import load_repo_config

    config = load_repo_config(root)
    if not config:
        return []
    episodes: list[Episode] = []
    for key, consequence in _CONFIG_CLAIMS:
        value = _dotted_get(config, key)
        if not value:
            continue
        episodes.append(
            Episode(
                tier=TIER_STRUCTURAL,
                kind=KIND_CONFIG_OVERRIDE,
                subject=key,
                body=(
                    f"`{key}` is set in .repowise/config.yaml ({_render(value)}): "
                    f"{consequence}."
                ),
                evidence=f".repowise/config.yaml: {key} = {_render(value)}",
            )
        )
    episodes.extend(_disabled_switches(config))
    return episodes


def _disabled_switches(config: dict) -> list[Episode]:
    """Episodes for boolean blocks a user turned off (hooks, distill)."""
    episodes: list[Episode] = []
    for block_name, block in (("hooks", config.get("hooks")), ("distill", config.get("distill"))):
        off = sorted(_falsy_leaves(block, block_name))
        if not off:
            continue
        episodes.append(
            Episode(
                tier=TIER_STRUCTURAL,
                kind=KIND_CONFIG_OVERRIDE,
                subject=block_name,
                body=(
                    f"{', '.join(f'`{key}`' for key in off)} "
                    f"{'is' if len(off) == 1 else 'are'} disabled in "
                    f".repowise/config.yaml, so the behaviour they gate does not "
                    f"happen in this repo even where it is documented as default."
                ),
                evidence=f".repowise/config.yaml: {', '.join(f'{key} = false' for key in off)}",
            )
        )
    return episodes


def _falsy_leaves(value: Any, prefix: str) -> list[str]:
    """Dotted keys under *prefix* whose value is exactly ``False``."""
    if not isinstance(value, dict):
        return []
    found: list[str] = []
    for key, leaf in value.items():
        path = f"{prefix}.{key}"
        if leaf is False:
            found.append(path)
        elif isinstance(leaf, dict):
            found.extend(_falsy_leaves(leaf, path))
    return found


def _dotted_get(config: dict, dotted: str) -> Any:
    current: Any = config
    for part in dotted.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _render(value: Any) -> str:
    if isinstance(value, list):
        shown = ", ".join(str(v) for v in value[:5])
        return f"{shown}, …" if len(value) > 5 else shown
    return str(value)


# ---------------------------------------------------------------------------
# 4. Formatter-clean tree — the one new subprocess
# ---------------------------------------------------------------------------


def _formatter_drift(root: Path, _traverser: Any) -> list[Episode]:
    """One ``ruff format --check`` when the repo declares ruff as its formatter.

    Any failure — no formatter declared, another formatter declared, no
    executable, a timeout, a crash, unparsable output — yields no episode. A
    budget that quietly produces a partial count is worse than no fact at all.
    """
    if not _declares_ruff_format(root):
        return []
    executable = _ruff_executable(root)
    if executable is None:
        return []
    try:
        completed = subprocess.run(
            [executable, "format", "--check", "."],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=FORMATTER_TIMEOUT_S,
            env={**os.environ, "NO_COLOR": "1"},
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    if completed.returncode == 0:
        return []  # clean tree: nothing happened, so there is no episode
    drifted = [
        line.split(":", 1)[1].strip()
        for line in completed.stdout.splitlines()
        if line.startswith("Would reformat:") and ":" in line
    ]
    if not drifted:
        return []  # non-zero for some other reason — say nothing
    return [
        Episode(
            tier=TIER_STRUCTURAL,
            kind=KIND_FORMATTER_DRIFT,
            subject="ruff format",
            body=(
                f"This tree is not formatter-clean: {len(drifted)} files would be "
                f"reformatted by `ruff format`. Running it repo-wide produces a large "
                f"diff unrelated to any change in progress. Format only the files you "
                f"touched, and check what CI actually enforces before assuming the "
                f"declared format command is a gate."
            ),
            evidence=f"ruff format --check .: {len(drifted)} files would be reformatted",
            # The count is true of the tree at this commit and of no other. It
            # is the only structural fact that cannot re-derive itself on an
            # update (the check is init-only), so it carries its birth and a
            # reader is expected to stop trusting it once the tree has moved.
            birth_commit=_head_commit(root),
        )
    ]


def _declares_ruff_format(root: Path) -> bool:
    """True when the repo names ruff, and only ruff, as its formatter.

    Deliberately stricter than :func:`detect_build_commands`, whose format
    inference fires on a ``pyproject.toml`` containing the words "ruff" and
    "format" anywhere. That is fine for a suggested command and wrong as the
    premise of a stored fact: ruff-as-linter beside black-as-formatter is a
    common pairing, and the two disagree, so the inference would tell a
    black-clean repo it is not formatter-clean by a formatter it never chose.
    A repo that declares any competing formatter is silent regardless.
    """
    pyproject = _read_text(root / "pyproject.toml")
    if any(marker in pyproject for marker in _COMPETING_FORMATTERS):
        return False
    if "[tool.ruff.format]" in pyproject:
        return True
    # An explicit invocation anywhere a project records its own commands.
    for candidate in ("Makefile", "package.json", ".pre-commit-config.yaml", "justfile"):
        text = _read_text(root / candidate)
        if "ruff format" in text or "ruff-format" in text:
            return True
    return False


#: Declaring one of these means the repo formats with something else.
_COMPETING_FORMATTERS: tuple[str, ...] = ("[tool.black]", "[tool.blue]", "[tool.yapf]")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _head_commit(root: Path) -> str | None:
    """The commit the checkout is on, or None. Best-effort, never fatal."""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    sha = completed.stdout.strip()
    return sha if completed.returncode == 0 and sha else None


def _ruff_executable(root: Path) -> str | None:
    """Prefer the checkout's own environment, then PATH."""
    for venv_name in _VENV_DIRNAMES:
        for scripts_dir, filename in (
            (root / venv_name / "Scripts", "ruff.exe"),
            (root / venv_name / "bin", "ruff"),
        ):
            candidate = scripts_dir / filename
            if candidate.is_file():
                return str(candidate)
    return shutil.which("ruff")
