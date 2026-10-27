"""The once-per-version repair of agent hook config that repowise itself owns.

A hook entry becomes legacy because a *repowise* version changed its shape, so
console script replaced a subcommand, a matcher widened. So the honest trigger is
"have the migrations for this version run yet", not "is this an invocation".

Before this, both migrations ran on **every** CLI invocation and on every
``repowise-augment`` fire, which is once per matched tool call. Each one opens and
parses a JSON settings file to discover, almost always, that there is nothing to
do. The stamp turns that into a single small read, and only the version after an
upgrade pays for the parse.

Two things this is deliberately *not*:

* **Not a config refresh.** There is no ``repowise upgrade`` command to hang one
  off (``upgrade_flow.py`` upgrades an index, not the CLI), and rewriting a
  user's global agent config unprompted on the first run after every release is a
  much larger claim than repairing a shape repowise wrote itself. ``repowise
  doctor`` reports what is stale; ``repowise agents refresh`` rewrites it when
  asked.
* **Not moved off ``main.py``.** A Codex rewrite hook whose matcher names a tool
  Codex has since renamed cannot self-heal from its own hook, because a hook
  matching nothing never runs. The CLI is the only place it can happen, so moving
  the call site to ``init``/``agents``/``doctor`` would strand anyone who never
  runs those.

``REPOWISE_SKIP_EDITOR_SETUP`` now applies here too. The variable means what it
says: a benchmark, CI or sandbox run must not touch global config. These
were the last two writers ignoring it. The accepted consequence is that someone
who exports it in their shell profile never gets hook migrations, which is the
correct reading of "do not touch my global config"; ``repowise doctor`` carries a
row per agent that names the stale hook, so it is visible rather than silent.

Nothing here is imported at module scope beyond stdlib: this runs on the
``repowise-augment`` hot path, where the whole point of the separate console
script is that it pulls in almost nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path


def stamp_path() -> Path:
    """The file holding the repowise version whose migrations have already run.

    ``~/.repowise`` is the existing home for global CLI state (``platform.json``,
    ``update-check.json``). Plain text rather than JSON because the only thing to
    record is one version string, and the cheapness of the read is the point.

    Resolved per call, not captured at import: a module-level ``Path.home()``
    would bind whatever ``HOME`` was when the first CLI module loaded, which is
    the exact trap the redirected-``USERPROFILE`` tests exist to catch.
    """
    return Path.home() / ".repowise" / "editor-migrations"


def _migrate_claude_code_hooks() -> None:
    from repowise.cli.editor_integrations.claude_config import migrate_claude_code_hooks

    migrate_claude_code_hooks()


def _migrate_codex_rewrite_hook() -> None:
    from repowise.cli.editor_integrations.codex_config import migrate_codex_rewrite_hook

    migrate_codex_rewrite_hook()


#: The migrations, named. The stamp records these names alongside the version,
#: because the version alone is not a complete key: ``__version__`` is a literal
#: in ``repowise/cli/__init__.py`` and a migration added without bumping it would
#: be **permanently** skipped for anyone who had already run that version, which
#: is every install tracking ``main``, including this repo's own. Adding an entry
#: here changes the stamp, so the new migration runs.
#:
#: Real functions rather than ``module:attribute`` strings. The imports stay lazy
#: either way, but a string path is checked by nothing: a typo in one would be an
#: ImportError caught by the same handler as a migration failure, so the stamp
#: would never be written and every hook fire would pay for the migrations,
#: exactly the cost this module exists to remove, silently and forever.
_MIGRATIONS: tuple[tuple[str, Callable[[], None]], ...] = (
    ("claude_code_hooks", _migrate_claude_code_hooks),
    ("codex_rewrite_hook", _migrate_codex_rewrite_hook),
)


def _expected_stamp() -> str:
    from repowise.cli import __version__

    return f"{__version__} {','.join(name for name, _ in _MIGRATIONS)}"


def _read_stamp() -> str | None:
    try:
        return stamp_path().read_text(encoding="utf-8").strip() or None
    except (OSError, ValueError):
        # Absent, a directory, unreadable, or not UTF-8. All mean the same
        # thing: no usable stamp, so run the migrations.
        return None


def _write_stamp(stamp: str) -> None:
    path = stamp_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(stamp, encoding="utf-8")
    except OSError:
        # A stamp we cannot write costs the pre-stamp behaviour (the migrations
        # run again next time) and nothing else. Never worth failing over.
        pass


def run_editor_migrations() -> bool:
    """Repair legacy agent hook shapes, at most once per repowise version.

    Returns whether the migrations actually ran, which is what the tests assert
    on; no caller acts on it.

    **The stamp is read before anything else, and that ordering is the feature.**
    Gating on ``is_editor_setup_disabled`` first reads better and costs more than
    the problem it solves: that function lives in ``editor_setup``, whose module
    scope imports ``agent_targets.types``, and importing it measures at roughly
    13ms against the ~19ms the whole self-heal was costing. On a path that fires
    once per matched tool call, checking the environment first would have spent
    two thirds of the saving on the check. Reading the stamp needs nothing but
    ``pathlib``, so the common path imports nothing at all.

    Reading is safe to do unconditionally: ``REPOWISE_SKIP_EDITOR_SETUP`` is
    about not *writing* to global config, and a stamp that is absent (which it
    will be, under that variable) short-circuits to the env check anyway.

    The stamp is written only when every migration completes without raising. A
    run that failed has not healed anything, and recording it as done would turn
    a transient failure into a permanent one.
    """
    expected = _expected_stamp()
    if _read_stamp() == expected:
        return False

    from repowise.cli.editor_setup import is_editor_setup_disabled

    if is_editor_setup_disabled():
        return False

    ok = True
    for _name, migrate in _MIGRATIONS:
        try:
            migrate()
        except Exception:
            ok = False

    if ok:
        _write_stamp(expected)
    return True
