"""The once-per-version repair of agent hook config that repowise itself owns.

A hook entry becomes legacy because a *repowise* version changed its shape — a
console script replaced a subcommand, a matcher widened. So the honest trigger is
"have the migrations for this version run yet", not "is this an invocation".

Before this, both migrations ran on **every** CLI invocation and on every
``repowise-augment`` fire, which is once per matched tool call. Each one opens and
parses a JSON settings file to discover, almost always, that there is nothing to
do. The stamp turns that into a single small read, and only the version after an
upgrade pays for the parse.

Two things this is deliberately *not*:

* **Not a config refresh.** There is no ``repowise upgrade`` command to hang one
  off — ``upgrade_flow.py`` upgrades an index, not the CLI — and rewriting a
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
says — a benchmark, CI or sandbox run must not touch global config — and these
were the last two writers ignoring it. The accepted consequence is that someone
who exports it in their shell profile never gets hook migrations, which is the
correct reading of "do not touch my global config"; ``repowise doctor`` carries a
row per agent that names the stale hook, so it is visible rather than silent.

Nothing here is imported at module scope beyond stdlib: this runs on the
``repowise-augment`` hot path, where the whole point of the separate console
script is that it pulls in almost nothing.
"""

from __future__ import annotations

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


def _stamped_version() -> str | None:
    try:
        return stamp_path().read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _write_stamp(version: str) -> None:
    path = stamp_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(version, encoding="utf-8")
    except OSError:
        # A stamp we cannot write costs the pre-stamp behaviour — the migrations
        # run again next time — and nothing else. Never worth failing over.
        pass


def run_editor_migrations() -> bool:
    """Repair legacy agent hook shapes, at most once per repowise version.

    Returns whether the migrations actually ran, which is what the tests assert
    on; no caller acts on it.

    The stamp is written only when both migrations complete without raising. A
    run that failed has not healed anything, and recording it as done would turn
    a transient failure into a permanent one.
    """
    from repowise.cli.editor_setup import is_editor_setup_disabled

    if is_editor_setup_disabled():
        return False

    from repowise.cli import __version__

    if _stamped_version() == __version__:
        return False

    ok = True
    try:
        from repowise.cli.editor_integrations.claude_config import migrate_claude_code_hooks

        migrate_claude_code_hooks()
    except Exception:
        ok = False
    try:
        from repowise.cli.editor_integrations.codex_config import migrate_codex_rewrite_hook

        migrate_codex_rewrite_hook()
    except Exception:
        ok = False

    if ok:
        _write_stamp(__version__)
    return True
