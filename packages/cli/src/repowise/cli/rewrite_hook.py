"""Standalone entry point for the ``repowise-rewrite`` PreToolUse hook.

Fires before every shell command an AI agent runs and decides whether to
rewrite it to ``repowise distill <command>`` so the agent sees a compact,
errors-first rendering instead of the raw flood. The default permission
posture is ``allow``: a rewritten command runs without an approval prompt,
uniformly across the main agent and every subagent. This is safe because
``classify`` only ever rewrites a closed set of recognized command families
(test/lint/build/git/search/listing/log) that survive the bailouts below —
no pipes, redirects, compound commands, substitution, or interactive
commands. The rewrite is therefore always ``repowise distill <one simple,
recognized command>``, never an arbitrary command smuggled behind the
wrapper, so auto-allowing it is not a permission escalation. Users who want
to review every rewrite can set ``permission: ask`` in
``.repowise/config.yaml``; a family set to ``off`` is never rewritten.

Hot-path discipline (this fires on EVERY Bash tool call):

  - Module scope imports nothing beyond the stdlib; the adapter modules it
    pulls in are stdlib-only too. No click, no repowise.core, no DB.
  - Classification is a static regex table plus an ignore-list — the
    expensive routing (content sniffing, store writes) happens later inside
    ``repowise distill``, whose latency hides behind the wrapped command.
  - Any failure, any unrecognized payload, any ambiguity → exit 0 with no
    output, which the agent treats as "run the command unchanged".

Bailouts — commands never rewritten:

  - pipes / redirections / compound commands (``| > < && || ; &``),
    substitution (backticks, ``$(``), multi-line commands: the wrapper
    would change shell semantics;
  - watch/follow modes (``--watch``, ``tail -f``): long-running,
    interactive by design;
  - the ignore-list of trivial or interactive commands (cd, echo, vim, …);
  - anything already invoking ``repowise``.
"""

from __future__ import annotations

import os.path
import re
import sys

# Stdlib-only module by design (see hot-path discipline above) — safe to
# import at module scope. (No pathlib: it costs double-digit milliseconds
# of interpreter startup, which this hook pays on every Bash call.)
from repowise.cli.agent_adapters.base import RewriteResult

# ---------------------------------------------------------------------------
# Command normalization — a hot-path mirror of
# ``repowise.core.distill.router.normalize_command``. Duplicated on purpose:
# importing the core router would pull the package __init__ (structlog, the
# engine) into every Bash call. ``test_rewrite_hook.py`` asserts the two stay
# behaviorally identical over a command table; update both together.
# ---------------------------------------------------------------------------

_WRAPPER_RE = re.compile(
    r"^(?:"
    r"uv run|uvx|npx|pnpm exec|pnpm dlx|yarn dlx|poetry run|pipenv run|hatch run|"
    r"python3? -m|py -m|"
    r"cmd(?:\.exe)? /c"
    r")\s+",
    re.IGNORECASE,
)
_ENV_ASSIGN_RE = re.compile(r"^\w+=\S+\s+")
_WHOLE_QUOTED_RE = re.compile(r'^"([^"]*)"$')
_EXE_PATH_RE = re.compile(r'^(?:"[^"]*[\\/]|\S*[\\/])(?P<exe>[\w.-]+?)(?:\.exe)?(?:")?(?=\s|$)')


def _normalize(command: str) -> str:
    cmd = command.strip()
    for _ in range(4):
        previous = cmd
        cmd = _ENV_ASSIGN_RE.sub("", cmd)
        cmd = _WRAPPER_RE.sub("", cmd)
        quoted = _WHOLE_QUOTED_RE.match(cmd)
        if quoted:
            cmd = quoted.group(1).strip()
        if cmd == previous:
            break
    cmd = _EXE_PATH_RE.sub(lambda m: m.group("exe"), cmd)
    return cmd.lower()


# ---------------------------------------------------------------------------
# Classification table. Family names MUST match the registered filter names
# in ``repowise.core.distill`` — the per-family permission config keys off
# them, and ``repowise distill`` routes by the same families.
# ---------------------------------------------------------------------------

FAMILY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "test_output",
        re.compile(
            r"^(pytest\b|py\.test\b|jest\b|vitest\b|cargo (?:test|nextest)\b|"
            r"go test\b|npm (?:test|run test)\b|pnpm (?:test|run test)\b|"
            r"yarn (?:test|run test)\b)"
        ),
    ),
    (
        "lint_output",
        re.compile(
            r"^(eslint\b|biome (?:check|lint)\b|ruff\b(?!\s+format)|flake8\b|pylint\b|mypy\b|"
            r"cargo clippy\b|golangci-lint\b|npm run lint\b|pnpm (?:run )?lint\b|"
            r"yarn (?:run )?lint\b|next lint\b)"
        ),
    ),
    (
        "build_output",
        re.compile(
            r"^(npm run b|npm run-script b|pnpm (?:run )?b|yarn (?:run )?b|"
            r"tsc\b|cargo (?:build|check)\b|go (?:build|vet)\b|"
            r"make\b|vite build|webpack\b|next build|dotnet build\b|"
            r"npm run (?:type-check|typecheck|compile)\b|gradle|mvn\b)"
        ),
    ),
    ("git_status", re.compile(r"^git status\b(?!.*--porcelain)")),
    ("git_log", re.compile(r"^git log\b")),
    ("git_diff", re.compile(r"^git (?:diff|show)\b(?!.*--stat)")),
    ("search_results", re.compile(r"^(rg\b|grep\b|egrep\b|fgrep\b|git grep\b)")),
    ("file_listing", re.compile(r"^(ls\b|tree\b|find\b|fd\b|git ls-files\b)")),
    ("logs", re.compile(r"^(tail\b|journalctl\b|docker logs\b|kubectl logs\b|cat\b.*\.log\b)")),
)

#: First tokens that are never worth wrapping: trivial, mutating, or
#: interactive. Checked before the regex table as a fast bail.
IGNORED_FIRST_TOKENS = frozenset(
    {
        # trivial / mutating
        "cd",
        "echo",
        "printf",
        "mkdir",
        "rmdir",
        "rm",
        "mv",
        "cp",
        "touch",
        "pwd",
        "which",
        "where",
        "whoami",
        "hostname",
        "date",
        "env",
        "set",
        "export",
        "unset",
        "source",
        "alias",
        "exit",
        "true",
        "false",
        "sleep",
        "kill",
        "chmod",
        "chown",
        "ln",
        "test",
        # interactive / fullscreen
        "vim",
        "vi",
        "nvim",
        "nano",
        "emacs",
        "less",
        "more",
        "top",
        "htop",
        "ssh",
        "man",
        "watch",
        "python",
        "python3",
        "node",
        "irb",
    }
)

# Shell syntax that changes meaning if the command is wrapped: pipes,
# redirections, chaining, backgrounding, substitution, heredocs, newlines.
# The same characters cover PowerShell's equivalents: `;` separators,
# backtick line-continuation/escapes, `$(...)` subexpressions, pipelines,
# and `& "path\to.exe"` call-operator invocations.
_SHELL_SYNTAX_RE = re.compile(r"[|&;<>`\n]|\$\(")

# Watch/follow modes are long-running; wrapping them buffers forever.
_WATCH_RE = re.compile(r"--watch(?:all)?\b|--looponfail\b|(?:^|\s)-f\b.*\.log\b|--follow\b")

# PowerShell cmdlets all follow the Verb-Noun shape (Get-ChildItem,
# Select-Object, ForEach-Object, …), so a Verb-Noun first token is a safe
# fast bail — PS-native pipelines and object output don't survive wrapping
# anyway. The only dashed token among the distill families is exempted.
_PS_CMDLET_RE = re.compile(r"^[a-z]+-[a-z]")
_DASHED_TOOL_TOKENS = frozenset({"golangci-lint"})


def classify(command: str) -> str | None:
    """Return the distill family for *command*, or None to pass through."""
    if not command or _SHELL_SYNTAX_RE.search(command):
        return None
    normalized = _normalize(command)
    if not normalized or normalized.startswith("repowise"):
        return None
    first = normalized.split(None, 1)[0]
    if first in IGNORED_FIRST_TOKENS or (
        _PS_CMDLET_RE.match(first) and first not in _DASHED_TOOL_TOKENS
    ):
        return None
    if _WATCH_RE.search(normalized):
        return None
    for family, pattern in FAMILY_PATTERNS:
        if pattern.match(normalized):
            return family
    return None


# ---------------------------------------------------------------------------
# Per-repo config — ``distill.commands`` block in .repowise/config.yaml
# ---------------------------------------------------------------------------

_VALID_PERMISSIONS = ("ask", "allow")
_OFF_VALUES = ("off", "deny", "disable", "disabled", "none", False)


def _find_repo_root(cwd: str) -> str | None:
    try:
        current = os.path.realpath(cwd or ".")
        home = os.path.realpath(os.path.expanduser("~"))
    except OSError:
        return None
    for _ in range(20):
        # ~/.repowise is the *user-level* config dir, not a repo opt-in —
        # without this guard every directory under $HOME would classify as
        # a repowise repo and get its commands rewritten.
        if current != home and os.path.isdir(os.path.join(current, ".repowise")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return None


def _load_commands_config(repo_root: str) -> tuple[bool, str, dict]:
    """Return (enabled, default_permission, per-family overrides).

    Missing file, missing yaml, malformed yaml → permissive defaults with
    ``allow`` (the hook is only installed for users who opted in, and a
    rewrite is always a bailout-filtered ``repowise distill`` wrap — see the
    module docstring for why auto-allow is safe). Set ``permission: ask`` to
    restore per-rewrite approval prompts.
    """
    enabled, permission, families = True, "allow", {}
    config_path = os.path.join(repo_root, ".repowise", "config.yaml")
    try:
        with open(config_path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return enabled, permission, families
    try:
        import yaml  # type: ignore[import-untyped]

        data = yaml.safe_load(text) or {}
    except Exception:
        return enabled, permission, families
    if not isinstance(data, dict):
        return enabled, permission, families
    distill = data.get("distill")
    if not isinstance(distill, dict):
        return enabled, permission, families
    if distill.get("enabled") is False:
        enabled = False
    commands = distill.get("commands")
    if isinstance(commands, dict):
        if commands.get("enabled") is False:
            enabled = False
        raw_permission = commands.get("permission")
        if raw_permission in _OFF_VALUES:
            enabled = False
        elif raw_permission in _VALID_PERMISSIONS:
            permission = raw_permission
        raw_families = commands.get("families")
        if isinstance(raw_families, dict):
            families = raw_families
    return enabled, permission, families


# First tokens that are PowerShell aliases or unix-flavored lookalikes
# (``ls`` → Get-ChildItem, ``cat`` → Get-Content, Windows ``find``/``tree``
# differ from their unix namesakes). Wrapping them through ``repowise
# distill``'s system-shell subprocess would change — or break — what runs,
# so PowerShell-sourced commands starting with these always pass through.
_PS_ALIAS_TOKENS = frozenset(
    {"ls", "dir", "cat", "type", "find", "fd", "tail", "head", "tree", "grep", "egrep", "fgrep"}
)


def decide(
    command: str, cwd: str, shell: str = "posix", source: str | None = None
) -> RewriteResult | None:
    """Full decision: classification + bailouts + per-repo permission config.

    *source* overrides the ledger tag for agents with their own surface
    (``hook-codex``); by default it derives from the shell dialect.
    """
    family = classify(command)
    if family is None:
        return None

    if shell == "powershell":
        first = _normalize(command).split(None, 1)[0]
        if first in _PS_ALIAS_TOKENS:
            return None

    # Only act inside repos that opted into repowise; the hook is installed
    # globally, but a repo without .repowise/ gets untouched commands.
    repo_root = _find_repo_root(cwd)
    if repo_root is None:
        return None

    enabled, permission, families = _load_commands_config(repo_root)
    if not enabled:
        return None
    family_setting = families.get(family)
    if family_setting in _OFF_VALUES:
        return None
    if family_setting in _VALID_PERMISSIONS:
        permission = family_setting

    # The --source tag lands in the savings ledger so `repowise saved
    # --by source` can tell hook surfaces apart from direct CLI use.
    if source is None:
        source = "hook-powershell" if shell == "powershell" else "hook-bash"
    return RewriteResult(
        command=f"repowise distill --source {source} {command.strip()}",
        permission=permission,
        reason=(
            f"repowise distill: compact {family} rendering; full output stays "
            f"recoverable via `repowise expand <ref>`"
        ),
    )


def _select_adapter(argv: list[str]):
    """Pick the adapter from ``--agent <name>`` argv; Claude Code by default.

    Each agent's hook config registers its own flavor (Codex hooks run
    ``repowise-rewrite --agent codex``) — the payloads are near-identical
    JSON, so argv is the only reliable discriminator.
    """
    agent = ""
    for i, arg in enumerate(argv):
        if arg == "--agent" and i + 1 < len(argv):
            agent = argv[i + 1]
        elif arg.startswith("--agent="):
            agent = arg.split("=", 1)[1]
    if agent == "codex":
        from repowise.cli.agent_adapters.codex import CodexAdapter

        return CodexAdapter()
    from repowise.cli.agent_adapters.claude_code import ClaudeCodeAdapter

    return ClaudeCodeAdapter()


def main() -> None:
    try:
        adapter = _select_adapter(sys.argv[1:])
        request = adapter.parse_hook_payload(sys.stdin.read())
        if request is not None:
            source = "hook-codex" if adapter.name == "codex" else None
            result = decide(request.command, request.cwd, request.shell, source=source)
            # An agent that can't honor the decided posture gets a
            # passthrough, never a silently escalated rewrite (Codex has no
            # ask-with-mutation — only families set to `allow` rewrite).
            if result is not None and result.permission in adapter.rewrite_permissions:
                sys.stdout.write(adapter.render_response(result))
                sys.stdout.flush()
    except (SystemExit, KeyboardInterrupt):
        raise
    except BaseException:
        # A hook failure must never surface in the agent transcript.
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
