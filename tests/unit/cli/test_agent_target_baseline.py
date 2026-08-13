"""Byte-level baseline of everything the three shipped integrations write.

This is the acceptance oracle for the ``agent_targets`` seam. Claude Code,
Codex and VS Code are about to be reimplemented on a common descriptor, and the
only way to know a rewrite of that size changed nothing is to pin the bytes
first and diff against them afterwards. **A one-byte diff is a bug, not a
rounding error.**

What it captures: every config file the three integrations write, in both
``project`` and ``user`` scope, under a redirected home. That is the surface
the seam replaces. It deliberately does *not* cover the generated instruction
files (``.claude/CLAUDE.md``, ``AGENTS.md`` bodies) — those come out of
``core.generation.editor_files``, which Phase 1 leaves untouched, and
generating them needs an indexed database. The two markdown generators are
stubbed so the surrounding decision logic still runs and still persists its
config opt-outs, which *are* captured.

Two sources of nondeterminism are pinned rather than tolerated:

* ``resolve_repowise_command()`` returns the absolute path of whichever install
  is running, so it is patched to a fixed sentinel.
* The temp repo and home roots differ per run, so every spelling of them that
  reaches a file (native separators, forward slashes, and the JSON-escaped
  native form) is substituted for a placeholder before comparison.

Line endings are compared as a *writer discipline* rather than as literal
bytes, because they are a platform property: ``Path.write_text`` translates to
``os.linesep`` while the marker-block writers pass ``newline="\\n"``
explicitly. Recording which of the two a file got makes the golden portable
across Windows and POSIX while still failing if the rewrite switches a file
from one writer to the other.

Regenerate with ``REPOWISE_UPDATE_AGENT_BASELINE=1 pytest
tests/unit/cli/test_agent_target_baseline.py``. Regenerating is an explicit,
reviewable act: if a diff shows up in the golden during the seam work and it
was not intended, that is the bug this file exists to catch.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from rich.console import Console

GOLDEN_PATH = Path(__file__).parent / "data" / "agent_target_baseline.json"

#: Stand-in for the absolute console-script path a per-user registration pins.
PINNED_COMMAND = "/pinned/bin/repowise"

_REPO_PLACEHOLDER = "{REPO}"
_HOME_PLACEHOLDER = "{HOME}"

_UPDATE_ENV = "REPOWISE_UPDATE_AGENT_BASELINE"


def _silent_console() -> Console:
    from io import StringIO

    return Console(file=StringIO(), force_terminal=False)


def _path_spellings(root: Path) -> list[str]:
    """Every spelling of *root* that can reach a written file.

    ``generate_mcp_config`` forward-slashes its repo path, the Codex TOML
    serializer JSON-encodes the native form (so a Windows separator arrives
    doubled), and plain interpolation gives the native form. Longest first so a
    substitution never leaves a fragment of a longer spelling behind.
    """
    native = str(root)
    spellings = {native, native.replace("\\", "/"), json.dumps(native)[1:-1]}
    return sorted(spellings, key=len, reverse=True)


def _normalize(text: str, repo: Path, home: Path) -> str:
    for spelling in _path_spellings(repo):
        text = text.replace(spelling, _REPO_PLACEHOLDER)
    for spelling in _path_spellings(home):
        text = text.replace(spelling, _HOME_PLACEHOLDER)
    return text


def _newline_style(raw: bytes) -> str:
    """Which writer produced this file: platform-translating, or explicit LF.

    ``lf`` means every newline is bare — what ``newline="\\n"`` guarantees on
    every platform. ``platform`` means they match ``os.linesep``, i.e. the
    default ``write_text`` translation. ``mixed`` is a real answer and a
    failure worth seeing.
    """
    if b"\n" not in raw:
        return "none"
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n")
    if crlf == 0:
        return "lf"
    if crlf == lf:
        # Every newline is CRLF. That is the platform translation on Windows
        # and something nothing here produces on POSIX, so on POSIX it is drift.
        return "platform" if os.linesep == "\r\n" else "mixed"
    return "mixed"


def _expected_newlines(recorded: str) -> str:
    """The style a *recorded* discipline should produce on this platform."""
    if recorded == "platform":
        return "platform" if os.linesep == "\r\n" else "lf"
    return recorded


def _capture(root: Path, repo: Path, home: Path) -> dict[str, dict[str, str]]:
    """Snapshot every file under *root* as {relative posix path: entry}."""
    captured: dict[str, dict[str, str]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        raw = path.read_bytes()
        rel = path.relative_to(root).as_posix()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:  # pragma: no cover - nothing binary is written
            pytest.fail(f"{rel} is not UTF-8; the baseline assumes text config")
        captured[rel] = {
            "content": _normalize(text.replace("\r\n", "\n"), repo, home),
            "newlines": _newline_style(raw),
        }
    return captured


def _write_everything(repo: Path, console) -> None:
    """Drive every write path of the three integrations, in init's own order.

    Project scope goes through the public orchestrators rather than the
    per-editor savers, because the orchestrators are the contract the seam has
    to preserve. The opt-in distill surfaces are driven directly: they are part
    of the Claude Code and Codex integrations but are reached through
    ``repowise hook rewrite`` rather than through ``init``.
    """
    from repowise.cli.editor_integrations.claude_config import (
        add_claude_code_distill_allow_rules,
        install_claude_code_rewrite_hook,
    )
    from repowise.cli.editor_integrations.codex_config import (
        install_agents_md_distill_section,
        install_codex_rewrite_hook,
    )
    from repowise.cli.editor_setup import (
        EditorSetupOptions,
        register_editor_clients,
        write_editor_project_files,
    )

    options = EditorSetupOptions(
        # CLAUDE.md generation needs an index; the opt-out branch still
        # persists to config.yaml, which the baseline does cover.
        disabled_project_files=frozenset({"claude_md"}),
        project_file_overrides={"agents_md": False},
        # Codex is off by default; the baseline wants its files.
        integration_overrides={"codex": True},
    )

    write_editor_project_files(console, repo, options=options)
    register_editor_clients(console, repo)

    install_claude_code_rewrite_hook()
    add_claude_code_distill_allow_rules()
    install_codex_rewrite_hook()
    install_agents_md_distill_section(repo)


@pytest.fixture
def baseline_env(tmp_path_factory, monkeypatch):
    """A redirected home plus a repo outside it, with the machine pinned out.

    ``HOMEDRIVE``/``HOMEPATH`` are redirected alongside ``USERPROFILE`` per the
    standing trap: both Claude config paths derive from ``Path.home()``, so one
    redirect has to cover Claude Code and Claude Desktop. The repo lives
    *outside* the fake home so the two placeholder substitutions cannot nest.
    """
    home = tmp_path_factory.mktemp("baseline_home")
    repo = tmp_path_factory.mktemp("baseline_repo")

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOMEDRIVE", home.drive or "")
    monkeypatch.setenv("HOMEPATH", str(home)[len(home.drive) :])
    monkeypatch.setattr(Path, "home", lambda: home)

    # The session-wide guard in conftest.py stops every test writing global
    # editor config. This baseline exists to capture exactly those writes, and
    # the redirects above (including ``Path.home``) mean they land in the fake
    # home, so it opts out — otherwise the user scope loses the Claude Desktop
    # entry and the golden no longer matches.
    monkeypatch.delenv("REPOWISE_SKIP_EDITOR_SETUP", raising=False)

    # Claude Desktop only registers when its config directory already exists,
    # so create it — the baseline should cover that write where the platform
    # supports it at all.
    from repowise.cli.editor_integrations import claude_config

    desktop = claude_config._claude_desktop_config_path()
    if desktop is not None:
        desktop.parent.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        claude_config, "resolve_repowise_command", lambda script="repowise": PINNED_COMMAND
    )

    # Instruction-file bodies are out of scope (see the module docstring); the
    # decision logic around them is not.
    from repowise.cli.editor_integrations import claude as claude_integration
    from repowise.cli.editor_integrations import codex as codex_integration

    async def _noop(_repo_path):
        return None

    monkeypatch.setattr(claude_integration, "_write_claude_md_async", _noop)
    monkeypatch.setattr(codex_integration, "_write_agents_md_async", _noop)

    # Probing the real Codex CLI shells out and varies by machine; it steers
    # console text only, never bytes on disk.
    from repowise.cli import mcp_config

    monkeypatch.setattr(mcp_config, "is_codex_cli_installed", lambda: True)
    monkeypatch.setattr(mcp_config, "is_codex_logged_in", lambda: True)

    return home, repo


def test_integration_writes_match_baseline(baseline_env) -> None:
    """The three integrations write exactly the bytes the golden records.

    The gate for the ``agent_targets`` rewrite. When this fails, the rewrite
    changed a file the old code produced — read the diff before touching the
    golden.
    """
    home, repo = baseline_env
    _write_everything(repo, _silent_console())

    actual = {
        "project": _capture(repo, repo, home),
        "user": _capture(home, repo, home),
    }

    if os.environ.get(_UPDATE_ENV):
        GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN_PATH.write_text(
            json.dumps(actual, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        pytest.skip(f"baseline regenerated at {GOLDEN_PATH}")

    expected = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))

    # Claude Desktop has no config location on Linux, so its file is absent
    # there by design rather than by regression.
    from repowise.cli.editor_integrations import claude_config

    if claude_config._claude_desktop_config_path() is None:
        expected["user"] = {
            rel: entry
            for rel, entry in expected["user"].items()
            if "claude_desktop_config.json" not in rel
        }

    for scope in ("project", "user"):
        assert sorted(actual[scope]) == sorted(expected[scope]), (
            f"{scope} scope wrote a different set of files than the baseline"
        )
        for rel in sorted(expected[scope]):
            assert actual[scope][rel]["content"] == expected[scope][rel]["content"], (
                f"{scope}/{rel} content diverged from the baseline"
            )
            assert actual[scope][rel]["newlines"] == _expected_newlines(
                expected[scope][rel]["newlines"]
            ), f"{scope}/{rel} changed line-ending discipline"


def test_writes_are_idempotent(baseline_env) -> None:
    """Re-running the full write path changes nothing at all.

    Re-running ``init`` is the common case, and the ``WriteResult`` contract
    promises an ``unchanged`` action for it.

    This test used to carry one named exception. ``.codex/config.toml`` is
    rebuilt by two regex table-rewrites in sequence — the server table, then
    ``[features]`` — and each strip moved its table to the end, so run 2 swapped
    them back and kept the blank line the swap left, gaining exactly one leading
    ``\\n``. Valid TOML throughout, so it was cosmetic, but it was real and it
    predated the seam. ``toml_merge.write_if_changed`` closed it by comparing
    parsed *documents* rather than text, and the exception was removed here on
    purpose. It was written to keep passing after the fix rather than to fail,
    so nothing but this paragraph would have reminded anyone.
    """
    home, repo = baseline_env
    console = _silent_console()

    def _run() -> dict[str, dict[str, dict[str, str]]]:
        _write_everything(repo, console)
        return {"project": _capture(repo, repo, home), "user": _capture(home, repo, home)}

    first, second, third = _run(), _run(), _run()

    assert first == second, "a second run changed a file the first run had settled"
    assert second == third


@pytest.mark.skipif(sys.platform != "win32", reason="separator handling is Windows-specific")
def test_normalization_covers_windows_separator_spellings() -> None:
    """Both Windows spellings of a path normalize, including the escaped one.

    The Codex TOML serializer routes its ``cwd`` through ``json.dumps``, so the
    repo path lands in the file with doubled backslashes. A normalizer that
    only knew the native and forward-slash forms would leave that one spelling
    machine-specific, and the golden would fail on every machine but the one
    that wrote it.
    """
    repo = Path(r"C:\tmp\repo")
    home = Path(r"C:\tmp\home")
    text = r"C:\tmp\repo C:/tmp/repo C:\\tmp\\repo"
    assert _normalize(text, repo, home) == f"{_REPO_PLACEHOLDER} " * 2 + _REPO_PLACEHOLDER
