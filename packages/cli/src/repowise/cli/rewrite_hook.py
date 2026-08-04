"""Standalone entry point for the ``repowise-rewrite`` PreToolUse hook.

Fires before every shell command an AI agent runs and decides whether to
rewrite it to ``repowise distill <command>`` so the agent sees a compact,
errors-first rendering instead of the raw flood. The default permission
posture is ``allow``: a rewritten command runs without an approval prompt,
uniformly across the main agent and every subagent. This is safe because
``classify`` only ever rewrites a closed set of recognized command families
(test/lint/build/git/search/listing/log) that survive the bailouts below —
no redirects, compound commands, substitution, or interactive commands,
and the only pipe shape allowed is a single stage into a bare stdin filter
(``head``/``tail``/``grep``/``rg``) with no quoting to break out of. The
rewrite is therefore always ``repowise distill
<one simple, recognized command>``, never an arbitrary command smuggled
behind the wrapper, so auto-allowing it is not a permission escalation. Users who want
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

  - stdout redirection, background ``&``, substitution (backticks,
    ``$(``), multi-line commands: the wrapper would change shell
    semantics. ``shell_lexer`` decides this structurally, so an operator
    that only *looks* like one because it sits inside quotes
    (``git commit -m "fix a|b"``) is not a bailout;
  - anything containing ``$``: a wrapped command is expanded by distill's
    shell rather than this one, and an unexported variable is empty there;
  - watch/follow modes (``--watch``, ``tail -f``): long-running,
    interactive by design;
  - the ignore-list of trivial or interactive commands (cd, echo, vim, …);
  - anything already invoking ``repowise``.

Compound commands, on POSIX hosts, are rewritten when every top-level
segment is separately safe — see ``_chain_families``. ``a && b``,
``a; b``, and ``a | b`` are wrapped whole, as one single-quoted token, so
the operators bind inside distill's own shell and the exit code and
ordering are the shell's, unchanged. A trailing ``2>&1`` is stripped first
(distill merges stderr into its capture anyway), and a stderr redirect
anywhere in the chain is preserved as written.
"""

from __future__ import annotations

import os.path
import re
import sys
import tempfile

# Stdlib-only module by design (see hot-path discipline above) — safe to
# import at module scope. (No pathlib: it costs double-digit milliseconds
# of interpreter startup, which this hook pays on every Bash call.)
from repowise.cli.agent_adapters.base import RewriteResult
from repowise.cli.shell_lexer import (
    SAFE_FINAL_TOOLS,
    analyze_pipeline,
    is_plain_stdin_filter,
    tokenize,
)

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
    """Mirror of ``router.normalize_command`` — keep the two in step.

    The hook cannot import ``repowise.core`` (hot-path discipline, see the
    module docstring), so this is a deliberate copy rather than a shared
    helper. Any fix here belongs there too.
    """
    cmd = command.strip()
    for _ in range(4):
        previous = cmd
        cmd = _ENV_ASSIGN_RE.sub("", cmd)
        # Strip the exe path *inside* the loop and before the wrapper table:
        # a wrapper invoked by path (".venv/Scripts/python.exe -m pytest")
        # only looks like a wrapper once the path is gone. Running this once
        # at the end left "python -m pytest", whose first token is on the
        # ignore-list, so every path-invoked test and lint run passed through.
        cmd = _EXE_PATH_RE.sub(lambda m: m.group("exe"), cmd)
        cmd = _WRAPPER_RE.sub("", cmd)
        quoted = _WHOLE_QUOTED_RE.match(cmd)
        if quoted:
            cmd = quoted.group(1).strip()
        if cmd == previous:
            break
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
    (
        "install_output",
        re.compile(
            r"^(pip|pip3|uv (?:pip )?(?:install|sync|add)|poetry (?:install|add|lock)|"
            r"npm (?:install|ci|i)\b|pnpm (?:install|add|i)\b|yarn (?:install|add)|"
            r"cargo install|brew install|bundle install|composer install)"
        ),
    ),
    ("infra_plan", re.compile(r"^(?:terraform|tofu) plan\b|^helm (?:diff|upgrade)\b")),
    ("git_status", re.compile(r"^git status\b(?!.*--porcelain)")),
    ("git_log", re.compile(r"^git log\b")),
    # `gh pr diff` emits a plain unified diff, so it distills as one. Only
    # this one gh subcommand is listed: `gh` at large includes mutations
    # (pr merge, pr close), and a rewrite is auto-allowed.
    ("git_diff", re.compile(r"^git (?:diff|show)\b(?!.*--stat)|^gh pr diff\b")),
    # The engine has had a git_diff_stat filter since the hunk filter started
    # skipping --stat; the hook had no pattern that could route to it, so a
    # diffstat was the one git shape that always passed through raw.
    ("git_diff_stat", re.compile(r"^git (?:diff|show)\b(?=.*--stat)")),
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

# A stderr-merge suffix is the one redirection distill preserves for free:
# it captures both streams and interleaves them, so `cmd 2>&1` and
# `repowise distill cmd` (with the outer shell applying the now-vacuous
# 2>&1 to distill's own empty stderr) see the same bytes. Stripped before
# the lexer runs so `pytest 2>&1 | grep FAIL` still classifies as pytest.
_STDERR_MERGE_RE = re.compile(r"\s+2>&1(?=\s|$)")

# The one thing re-quoting cannot make safe. Everything else that used to
# bail here (`"`, `'`, `\`) is a *quoting* problem, and `_single_quote`
# solves quoting exactly; `$` is an *evaluation-timing* problem, which it
# does not. The wrapped token is expanded by distill's shell rather than the
# outer one, and a shell variable that was never exported is empty there.
# Measured on 7 days of this repo's transcripts: admitting `$` would have
# added 545 tokens on top of 55,517, so the timing question does not pay for
# itself.
_EXPANSION_CHAR = "$"

#: Segment heads a wrapped chain may contain freely: read-only, not
#: interactive, and carrying no output anyone wants filtered. Deliberately
#: NOT ``IGNORED_FIRST_TOKENS``, which also holds rm/mv/chmod/kill — those
#: are ignored because wrapping them is pointless, not because they are safe
#: to auto-allow inside something larger.
_INERT_SEGMENT_TOKENS = frozenset({"cd", "echo", "printf", "pwd", "true", ":"})

#: Ops that only sequence commands. A bare ``&`` (background) is absent on
#: purpose: distill would capture the output of a job that has not run yet.
_CHAIN_OPS = frozenset({"&&", "||", ";"})

# distill executes via the system shell (cmd.exe on Windows, where
# head/tail/grep don't exist), so the safe-pipeline rewrite is
# POSIX-hosts-only. Module constant so tests can pin both platforms.
_POSIX_HOST = os.name == "posix"

# Windows keeps the blunt character bail, quoted or not. Two reasons:
#
#   - PowerShell has no backslash escape, so a Windows path ending in ``\``
#     does not extend a quoted run the way the POSIX rules here assume, and
#     the lexer would split the command in a place PowerShell would not.
#   - Defense in depth on the renderer. ``distill_cmd._render_command`` now
#     caret-escapes what it hands cmd.exe, but it still has to refuse a
#     couple of shapes outright (a ``%NAME%`` cmd would expand, an embedded
#     newline). A rewrite is auto-allowed, so this side stays conservative
#     rather than depending on the far side getting every case right.
#
# So the lexer's false-bail win is a POSIX win. On Windows it still buys the
# structural bailouts, just not the widening.
_WIN_SHELL_METACHAR_RE = re.compile(r"[|&;<>`^\n]|\$\(")


def _split_safe_tail(command: str) -> tuple[str, bool] | None:
    """Split *command* into (classifiable head, needs_inner_shell).

    Returns None when the command carries shell syntax the wrapper can't
    preserve. ``needs_inner_shell`` is True for the safe-pipeline shape
    (``cmd | grep FAIL``): the caller must pass the whole command to
    ``repowise distill`` as one quoted token so the pipe executes inside
    distill's own shell rather than binding to the wrapper.

    The structural decisions (chaining, substitution, redirects, how many
    stages, which tool ends the pipeline) come from ``shell_lexer``; what is
    left here is the policy the lexer deliberately does not own — the
    stderr-merge carve-out, the POSIX-host gate, and the quoting rules for
    the one shape that gets re-quoted.
    """
    declawed = _STDERR_MERGE_RE.sub("", command.strip())
    if not _POSIX_HOST and _WIN_SHELL_METACHAR_RE.search(declawed):
        return None
    pipeline = analyze_pipeline(declawed)
    if pipeline is None or pipeline.redirects:
        # Every redirect other than the stderr merge already stripped above
        # would change what the wrapper captures.
        return None
    if pipeline.final_tool is None:
        return pipeline.producer, False
    # A pipeline is re-quoted as one token. `_single_quote` makes the quoting
    # itself airtight, so only re-expansion is left to bail on.
    if not _POSIX_HOST or _EXPANSION_CHAR in command:
        return None
    return pipeline.producer, True


def _single_quote(command: str) -> str:
    """POSIX single-quote *command* so a shell re-reads it verbatim.

    ``shlex.quote`` in one line, inlined rather than imported: this module
    fires on every Bash tool call and its docstring commits to a stdlib-only,
    minimal-import scope. A single-quoted run ends at the next ``'``, so the
    only escape needed is to close, emit a literal quote, and reopen.
    """
    return "'" + command.replace("'", "'\\''") + "'"


def _chain_families(command: str) -> tuple[str, ...] | None:
    """Families of a chain whose every segment is safe to wrap wholesale.

    ``_split_safe_tail`` handles one simple command and the single-pipe
    shape. Anything else -- ``a && b``, ``a; b``, a pipeline with more than
    one producer -- used to bail outright, and that bail is where most of the
    unrealised savings sit: on 7 days of this repo's transcripts, 89% of the
    tokens ``repowise saved --missed`` reports were declined for command
    shape, not for being unrecognized.

    A chain is admitted only when *every* top-level segment is one of:

      - a command ``_classify_head`` recognizes (the same closed set a lone
        command must be in),
      - an inert builtin (``_INERT_SEGMENT_TOKENS``),
      - a bare stdin filter on the right of a pipe (``SAFE_FINAL_TOOLS``),

    and at least one segment is recognized. That rule is the whole safety
    argument: a rewrite is auto-allowed, and wrapping a chain of things the
    agent could already run approval-free grants it nothing new. One
    unrecognized segment -- ``git status && ./deploy.sh`` -- and the whole
    command passes through, because wrapping it would auto-allow the part
    nobody vetted.

    Returns None when the chain is not admissible; otherwise the recognized
    families in order, whose first element names the rewrite.
    """
    if not _POSIX_HOST:
        # Same reason ``_split_safe_tail`` gates the pipeline shape: distill
        # re-runs the wrapped token through the system shell, which is
        # cmd.exe here, and these are POSIX command lines.
        return None
    declawed = _STDERR_MERGE_RE.sub("", command.strip())
    if _EXPANSION_CHAR in declawed:
        return None

    segments: list[list[str]] = [[]]
    piped_from: set[int] = set()
    skip_next_arg = False
    for token in tokenize(declawed):
        if token.kind == "arg":
            if skip_next_arg:  # a redirect target, not part of the command
                skip_next_arg = False
                continue
            segments[-1].append(token.text)
        elif token.kind == "op":
            if token.text not in _CHAIN_OPS:
                return None  # background &, substitution, backtick, newline
            segments.append([])
        elif token.kind == "pipe":
            if token.text != "|":  # `|&` also pipes stderr
                return None
            segments.append([])
            piped_from.add(len(segments) - 1)
        elif token.kind == "redirect":
            # stdout redirects send the output somewhere distill will never
            # see. A stderr redirect only decides whether distill's
            # errors-first rendering has errors to lead with, which is the
            # caller's business either way.
            if not token.text.startswith("2"):
                return None
            skip_next_arg = True
    if skip_next_arg:
        return None  # trailing redirect with no target: malformed, bail

    families: list[str] = []
    for index, segment in enumerate(segments):
        if not segment:
            continue
        head = " ".join(segment)
        normalized = _normalize(head)
        if not normalized:
            # An assignment with nothing after it (`FOO=bar`) normalizes
            # away. It sets state the wrapped shell would not share.
            return None
        first = normalized.split(None, 1)[0]
        if first in _INERT_SEGMENT_TOKENS:
            continue
        if index in piped_from and first in SAFE_FINAL_TOOLS:
            # grep/tail are both producer families and stdin filters, and on
            # the right of a pipe they are the latter -- so this has to be
            # judged before `_classify_head`, which would happily call
            # `grep -f patterns.txt` a search and wave it through. Membership
            # is not the test either: `grep -f` reads a pattern file and
            # `tail -F` never closes the pipe. Ask the lexer, which owns both.
            if not is_plain_stdin_filter(segment):
                return None
            continue
        family = _classify_head(head)
        if family is None:
            return None
        families.append(family)
    return tuple(families) or None


# Watch/follow modes are long-running; wrapping them buffers forever.
_WATCH_RE = re.compile(r"--watch(?:all)?\b|--looponfail\b|(?:^|\s)-f\b.*\.log\b|--follow\b")

# Every tool in the `logs` family can stream instead of returning, and the
# flag that does it is bare `-f`/`-F` far more often than it is `--follow`.
# `_WATCH_RE` only catches the `-f` form when a literal `.log` follows, which
# misses `journalctl -f`, `docker logs -f web`, and `tail -f /var/log/syslog`.
# Checking this per-family keeps `-f` meaning what it means elsewhere
# (`make -f Makefile`, `docker compose -f x.yml` stay rewritable).
_FOLLOW_FLAG_RE = re.compile(r"(?:^|\s)(?:-[a-z]*[fF]|--follow)(?:[\s=]|$)")
_STREAMING_FAMILIES = frozenset({"logs"})

# PowerShell cmdlets all follow the Verb-Noun shape (Get-ChildItem,
# Select-Object, ForEach-Object, …), so a Verb-Noun first token is a safe
# fast bail — PS-native pipelines and object output don't survive wrapping
# anyway. The only dashed token among the distill families is exempted.
_PS_CMDLET_RE = re.compile(r"^[a-z]+-[a-z]")
_DASHED_TOOL_TOKENS = frozenset({"golangci-lint"})

# `find`/`fd` sit in the listing family, but these flags turn a listing into
# an arbitrary-command runner: `find . -name '*.tmp' -exec rm {} \;` is a
# delete, not an `ls`. A rewrite is auto-allowed, so a command that merely
# *starts* like something recognized must not inherit that approval. Kept
# per-tool because the spellings collide with unrelated flags elsewhere
# (`-x` is fd's exec, and also `pytest -x`, which stays rewritable).
_ACTION_FLAG_RES = (
    ("find", re.compile(r"(?:^|\s)-(?:exec(?:dir)?|ok(?:dir)?|delete)(?:\s|$)")),
    ("fd", re.compile(r"(?:^|\s)(?:-[xX]|--exec(?:-batch)?)(?:\s|$)")),
)


def classify(command: str) -> str | None:
    """Return the distill family for *command*, or None to pass through."""
    if not command:
        return None
    split = _split_safe_tail(command)
    if split is not None:
        return _classify_head(split[0])
    families = _chain_families(command)
    return families[0] if families else None


def _classify_head(head_command: str) -> str | None:
    """Family for an already syntax-vetted command (no bailout checks)."""
    normalized = _normalize(head_command)
    if not normalized or normalized.startswith("repowise"):
        return None
    first = normalized.split(None, 1)[0]
    if first in IGNORED_FIRST_TOKENS or (
        _PS_CMDLET_RE.match(first) and first not in _DASHED_TOOL_TOKENS
    ):
        return None
    if _WATCH_RE.search(normalized):
        return None
    for tool, action_re in _ACTION_FLAG_RES:
        if first == tool and action_re.search(normalized):
            return None
    for family, pattern in FAMILY_PATTERNS:
        if pattern.match(normalized):
            if family in _STREAMING_FAMILIES and _FOLLOW_FLAG_RE.search(normalized):
                return None
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
        temp_root = os.path.realpath(tempfile.gettempdir())
    except OSError:
        return None
    for _ in range(20):
        # ~/.repowise is the *user-level* config dir, not a repo opt-in —
        # without this guard every directory under $HOME would classify as
        # a repowise repo and get its commands rewritten. The system temp
        # ROOT gets the same treatment: a .repowise there is always a stray
        # artifact (a tool that indexed with cwd=$TMP), never an opt-in, and
        # it would otherwise capture every temp-dir cwd on the machine.
        # Repos legitimately created UNDER either directory still match.
        if current not in (home, temp_root) and os.path.isdir(os.path.join(current, ".repowise")):
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
    split = _split_safe_tail(command) if command else None
    chain: tuple[str, ...] = ()
    if split is not None:
        head_command, needs_inner_shell = split
        family = _classify_head(head_command)
        if family is None:
            return None
        if shell == "powershell":
            first = _normalize(head_command).split(None, 1)[0]
            if first in _PS_ALIAS_TOKENS:
                return None
    elif command and shell != "powershell":
        # A chain is POSIX shell syntax by construction (`&&`, `;`, `|`), so
        # it is only ever offered to the POSIX dialect.
        chain = _chain_families(command) or ()
        if not chain:
            return None
        family, needs_inner_shell = chain[0], True
    else:
        return None

    # Only act inside repos that opted into repowise; the hook is installed
    # globally, but a repo without .repowise/ gets untouched commands.
    repo_root = _find_repo_root(cwd)
    if repo_root is None:
        return None

    enabled, permission, families = _load_commands_config(repo_root)
    if not enabled:
        return None
    # Every family the command touches has to be allowed, not just the one
    # that names the rewrite: otherwise `git_diff: off` would be bypassable
    # by chaining a git diff behind anything still on.
    for touched in chain or (family,):
        if families.get(touched) in _OFF_VALUES:
            return None
    family_setting = families.get(family)
    if family_setting in _VALID_PERMISSIONS:
        permission = family_setting

    # The --source tag lands in the savings ledger so `repowise saved
    # --by source` can tell hook surfaces apart from direct CLI use.
    if source is None:
        source = "hook-powershell" if shell == "powershell" else "hook-bash"
    # A pipeline or chain is passed as ONE quoted token so its operators bind
    # inside distill's shell (distill re-runs a single token verbatim via
    # shell=True) instead of binding to the wrapper. Single quotes, so the
    # inner shell reads the token back byte for byte; `$` already bailed, so
    # nothing in it re-expands.
    wrapped = _single_quote(command.strip()) if needs_inner_shell else command.strip()
    return RewriteResult(
        command=f"repowise distill --source {source} {wrapped}",
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
