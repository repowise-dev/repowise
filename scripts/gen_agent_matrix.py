"""Render the agent support matrix at ``docs/agent/INTEGRATIONS.md`` from the code.

Two numbers in this repo have a long history of being wrong in public: which
agents we support at what depth, and how many MCP tools we expose. Both were
hand-written in a dozen places, so both drifted the moment a target or a tool
landed. Six artifacts said "ten MCP tools" against a live surface of eleven, and
the README contradicted itself two hundred lines apart.

So neither is written by hand here. The tiers come from
:func:`repowise.cli.agent_targets.types.derive_tier`, the capability columns come
from each descriptor's declared install methods, and the tool counts come from
the live MCP registry. A new agent or a new tool changes this document by being
registered, and :mod:`tests.unit.cli.test_agent_matrix` fails when the file on
disk disagrees.

Render discipline, the same as ``scripts/gen_plugin_content.py`` and for the same
reason: fixed section order, sorted iteration where the order is not already
declared, LF newlines, and never a timestamp, a version or a generator banner in
the output. A file that changes when nothing changed cannot be a golden.

Run ``python scripts/gen_agent_matrix.py`` to write, ``--check`` to report drift
without writing.
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "agent" / "INTEGRATIONS.md"

#: Prose width. Only paragraphs whose text is computed are wrapped here; the
#: hand-authored lines are wrapped as written, because reflowing them through
#: ``textwrap`` would rewrite the file every time a sentence changed length.
_WRAP = 84

# The script runs standalone as well as under pytest, so make the workspace
# packages importable either way. Harmless when they are already installed.
for _pkg in ("cli", "core", "server"):
    _src = ROOT / "packages" / _pkg / "src"
    if _src.is_dir() and str(_src) not in sys.path:
        sys.path.insert(0, str(_src))


# ---------------------------------------------------------------------------
# Hand-authored copy
# ---------------------------------------------------------------------------

#: Hosts served by the Paste-config tier: we write nothing and maintain nothing,
#: ``repowise agents print-config`` emits a snippet they can paste. There is no
#: descriptor to derive this from, by definition. The golden test does check
#: that nothing here collides with a registered target, so promoting one of
#: these to a real integration and forgetting to delete the line is caught.
PASTE_CONFIG_HOSTS: tuple[str, ...] = (
    "Cline",
    "Windsurf",
    "Zed",
    "Continue",
    "Gemini CLI",
    "JetBrains AI Assistant",
    "Amp",
)

#: Default-surface tools the README's curated list leaves out. ``list_repos``
#: answers "which repos is this server serving", which is discovery rather than
#: one of the task-shaped tools the README is selling, and a reader counting
#: capabilities should not have it padding the number. Everything downstream of
#: the README states the real surface instead.
NON_FLAGSHIP_TOOLS: frozenset[str] = frozenset({"list_repos"})

TIER_BLURBS: dict[str, tuple[str, str]] = {
    "full": (
        "Full",
        "MCP tools, a managed instructions file, skills, slash commands, "
        "hook-level interception of tool calls, and transcript mining after "
        "the fact. Every surface repowise has.",
    ),
    "good": (
        "Good",
        "MCP tools, and a managed instructions file or skills where the host "
        "reads them. **No hook-level interception and no transcript mining.** "
        "The agent can ask repowise questions; repowise cannot see or annotate "
        "what the agent does in between.",
    ),
    "paste-config": (
        "Paste-config",
        "`repowise agents print-config <id>` emits the MCP server snippet and "
        "we write nothing. Zero code and zero maintenance per host.",
    ),
}


# ---------------------------------------------------------------------------
# Derived data
# ---------------------------------------------------------------------------


def agent_rows() -> list[dict]:
    """One row per registered target, in registry order.

    Only the static fields. ``registry.describe_agents`` is the projection the
    CLI reads, and it deliberately reports ``present`` / ``method`` /
    ``registrations``, which describe the machine the command ran on. A checked
    in document rendered from those would differ per developer and fail its own
    ``--check`` on the next machine.
    """
    from repowise.cli.agent_targets import registry
    from repowise.cli.agent_targets.types import Capability, derive_tier

    rows: list[dict] = []
    for target in registry.all_targets():
        # Split by who manages the method rather than unioning every
        # capability, because "Claude Code has slash commands" and "repowise
        # installs Claude Code's slash commands" are different claims and only
        # the first is true. The host plugin ships those.
        by_manager: dict[str, set[Capability]] = {"repowise": set(), "host": set()}
        for method in target.methods:
            key = "host" if method.managed_by == "host" else "repowise"
            by_manager[key] |= set(method.provides)
        rows.append(
            {
                "id": target.id,
                "display_name": target.display_name,
                "docs_url": target.docs_url,
                "tier": derive_tier(target).value,
                "direct": by_manager["repowise"],
                "host": by_manager["host"],
                "hook_adapter": target.hook_adapter,
                "session_adapter": target.session_adapter,
            }
        )
    return rows


def tool_counts() -> dict[str, int]:
    """The live MCP tool surface, straight from the registry.

    ``ensure_full_surface`` is what makes the count honest: tool modules import
    lazily, so a registry read without it reports however many tools happened to
    be imported already.
    """
    from repowise.core.registry import mcp_tool_registry
    from repowise.server.mcp_server import ensure_full_surface
    from repowise.server.mcp_server._tool_selection import LEAN, resolve_enabled_tools

    ensure_full_surface()
    entries = mcp_tool_registry.entries()
    default_names = resolve_enabled_tools(entries, is_workspace=False)
    # Every count goes through the resolver the server itself uses, including
    # the lean ones. Counting ``LEAN_TOOLS`` directly looks equivalent and is
    # not: the resolver drops a lean name the registry no longer carries, so a
    # renamed tool would leave the published "six tools" claiming a surface the
    # server had quietly trimmed to five.
    single_repo = len(default_names)
    workspace = len(resolve_enabled_tools(entries, is_workspace=True))
    return {
        "total": len(entries),
        "single_repo": single_repo,
        # The README and the hero image count the *flagship* tools, which is a
        # narrower and deliberate claim: the task-shaped ones the pitch is
        # about. It is the default surface minus the discovery utilities, and
        # it is derived here rather than typed, so a twelfth default tool moves
        # the README too. Docs keep the precise surface numbers.
        "flagship": len(default_names - NON_FLAGSHIP_TOOLS),
        "workspace": workspace,
        # The delta is published in its own right ("adds two more"), so it is
        # derived rather than written as a literal next to a derived total.
        "workspace_extra": workspace - single_repo,
        "opt_in": len([entry for entry in entries if not entry.default]),
        "lean": len(resolve_enabled_tools(entries, is_workspace=False, override=LEAN)),
        "lean_workspace": len(resolve_enabled_tools(entries, is_workspace=True, override=LEAN)),
    }


#: Complete through twenty, not just the values in use today. A partial map is
#: a landmine: the counts here feed a module-scope constant and a parametrize
#: argument in the golden test, so a missing entry is a *collection* error that
#: takes down the very drift guards that exist to say which files to edit. The
#: first tool to push a count to twelve, or the renamed lean tool this file
#: warns about elsewhere, would have hit exactly that.
_WORDS = {
    0: "zero",
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
    11: "eleven",
    12: "twelve",
    13: "thirteen",
    14: "fourteen",
    15: "fifteen",
    16: "sixteen",
    17: "seventeen",
    18: "eighteen",
    19: "nineteen",
    20: "twenty",
}


def spell(count: int) -> str:
    """*count* as an English word, for prose that spells small numbers out.

    Raises past twenty rather than falling back to the digits: the artifacts
    this guards say "eleven MCP tools", and a silent switch to "21 MCP tools"
    mid-sentence is the kind of drift that goes unnoticed for twenty-four
    releases. Past twenty the prose wants rewriting by a person anyway.
    """
    if count not in _WORDS:
        raise ValueError(
            f"no spelled form for {count}. Prose that spells a number this large reads "
            "badly; rewrite the sentence, or extend _WORDS in scripts/gen_agent_matrix.py."
        )
    return _WORDS[count]


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

#: Column caption, and the capability it reads. ``None`` means the column is
#: derived from an adapter name rather than from a declared capability.
_COLUMNS: tuple[tuple[str, str | None], ...] = (
    ("MCP", "mcp"),
    ("Hooks", "hooks"),
    ("Skills", "skills"),
    ("Commands", "commands"),
    ("Instructions", "instructions"),
    ("Transcripts", None),
)


def _cell(row: dict, capability: str | None) -> str:
    if capability is None:
        return "Yes" if row["session_adapter"] else "No"
    from repowise.cli.agent_targets.types import Capability

    wanted = Capability(capability)
    if wanted in row["direct"]:
        return "Yes"
    if wanted in row["host"]:
        return "Plugin"
    return "No"


def _matrix_table(rows: list[dict]) -> str:
    header = "| Agent | Tier | " + " | ".join(name for name, _ in _COLUMNS) + " |"
    rule = "|---|---|" + "---|" * len(_COLUMNS)
    lines = [header, rule]
    for row in rows:
        name = row["display_name"]
        if row["docs_url"]:
            name = f"[{name}]({row['docs_url']})"
        cells = [_cell(row, capability) for _, capability in _COLUMNS]
        tier = TIER_BLURBS[row["tier"]][0]
        lines.append(f"| {name} | {tier} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _join_names(names: list[str]) -> str:
    """``A``, ``A and B``, ``A, B and C`` — a real list, at any length.

    The first version of this handled one name and read as a grammar error at
    two ("VS Code and Cursor sits at Good"), which is the shape of drift a
    generated document is supposed to make impossible. Verb agreement is the
    caller's, because only it knows whether the sentence needs one.
    """
    if len(names) < 3:
        return " and ".join(names)
    return ", ".join(names[:-1]) + f" and {names[-1]}"


def _tier_list() -> str:
    return "\n".join(
        f"- **{title}.** {body}" for title, body in (TIER_BLURBS[key] for key in TIER_BLURBS)
    )


def render() -> str:
    rows = agent_rows()
    counts = tool_counts()
    ids = ", ".join(f"`{row['id']}`" for row in rows)
    good = [row["display_name"] for row in rows if row["tier"] == "good"]

    parts = [
        "# Agent integrations",
        "",
        "<!-- Generated by scripts/gen_agent_matrix.py. Do not edit by hand: run",
        "     `python scripts/gen_agent_matrix.py` after changing a descriptor. -->",
        "",
        "Anything that speaks MCP can read a repowise index. This page is about how",
        "*deeply* each agent is wired, which is a different question, and the one",
        "every breadth claim in this space gets wrong.",
        "",
        "## Tiers",
        "",
        "The tier is **derived from the code, never declared**. A descriptor reaches",
        "Full only by naming both a hook adapter and a transcript adapter, so no",
        "document can claim a depth the integration does not have.",
        "",
        _tier_list(),
        "",
        "## The matrix",
        "",
        "`Yes` means repowise wires it. `Plugin` means the host's own plugin ships",
        "it and repowise does not write it. `No` means the surface does not exist",
        "for that agent.",
        "",
        _matrix_table(rows),
        "",
        f"Target ids for `--target=`: {ids}.",
        "",
    ]

    if good:
        parts += [
            "### What Good tier does not include",
            "",
            f"{_join_names(good)} {'sits' if len(good) == 1 else 'sit'} at Good, and the "
            "honest version of that is worth stating",
            "plainly. These agents get the MCP tools and the config repowise writes.",
            "They do **not** get hook-level interception: repowise never sees a tool",
            "call before it runs, never rewrites a noisy command, and never annotates",
            "a result afterwards. Nor is there transcript mining, so nothing learns",
            "from the session after it ends.",
            "",
            "That is a real integration and it is most of the value. It is not the",
            "same product Full-tier agents get, and breadth that overclaims depth is",
            "worse than narrower breadth.",
            "",
        ]

    parts += [
        "## Paste-config",
        "",
        "Every other MCP host is served without writing a descriptor for it. Print",
        "the server entry and paste it into whatever config that host reads:",
        "",
        "```bash",
        "repowise agents print-config claude-code   # prints, writes nothing",
        "```",
        "",
        "Ask for the target id whose host is closest to yours rather than editing a",
        "snippet by hand. The shapes genuinely differ, and not only in their wrapper:",
        "hosts disagree about the top-level key, about whether each entry carries a",
        "`type` field, about whether the invocation is one array or a command plus a",
        "separate argument list, and about the file format itself, so `codex` prints",
        "TOML and `hermes` prints YAML where the others print JSON. `claude-code` is",
        "the plain `mcpServers` JSON most hosts read and is the right default when",
        "yours is not listed below.",
        "Hosts people ask about most, none of which repowise writes config for today:",
        "",
        ", ".join(PASTE_CONFIG_HOSTS) + ".",
        "",
        "`print-config` takes one of the target ids above; there is no descriptor to",
        "name for a host at this tier, which is the point of the tier.",
        "",
        "## The MCP surface",
        "",
        textwrap.fill(
            f"repowise registers **{spell(counts['total'])} MCP tools**. A single-repo "
            f"server advertises **{spell(counts['single_repo'])}** of them by default, "
            f"and workspace mode adds {spell(counts['workspace_extra'])} more "
            f"automatically for **{spell(counts['workspace'])}**. A further "
            f"**{spell(counts['opt_in'])}** are off by default, enabled through the "
            "`mcp.tools` config block or `--tools +name`. The `lean` profile trims the "
            f"default surface to **{spell(counts['lean'])}** tools "
            f"({spell(counts['lean_workspace'])} in workspace mode) for agents on a "
            "tight context budget.",
            width=_WRAP,
        ),
        "",
        "Per-tool detail: [MCP_TOOLS.md](MCP_TOOLS.md).",
        "",
        "## Adding an agent",
        "",
        "**Adding an agent takes one descriptor file and one registry line.**",
        "",
        "1. Write `packages/cli/src/repowise/cli/agent_targets/targets/<id>.py`",
        "   exporting a `TARGET` that satisfies the `AgentTarget` protocol in",
        "   [`types.py`](../../packages/cli/src/repowise/cli/agent_targets/types.py).",
        "   `vscode.py` is the smallest working example, at one install method and",
        "   one config file.",
        "2. Add one line to `_TARGET_MODULES` in",
        "   [`registry.py`](../../packages/cli/src/repowise/cli/agent_targets/registry.py).",
        "   Order there is the order agents appear in prompts, in `--target=all` and",
        "   in listings, so keep it stable.",
        "3. Run `python scripts/gen_agent_matrix.py` to add the row here.",
        "",
        "There is no third file for anything derived. The tier, this matrix and the",
        "`repowise agents` listing all read the descriptor, and the contract tests in",
        "`tests/unit/cli/test_agent_targets.py` are parameterized over the registry,",
        "so a new target inherits them.",
        "",
        "The README badge rows are the exception: a brand colour and a logo per agent",
        "are not derivable, and the README is not generated, so a new agent needs a",
        "badge added by hand and the count above them updated. That is checked rather",
        "than trusted. `tests/unit/cli/test_agent_matrix.py` fails when the badge rows",
        "and the registry disagree, and names what to add.",
        "",
        "Declare only what the agent genuinely has. `derive_tier` reads the adapter",
        "names, so a descriptor that names a hook adapter it has not implemented",
        "publishes a Full-tier claim on this page.",
        "",
    ]
    return "\n".join(parts).rstrip("\n") + "\n"


def rendered_files() -> dict[Path, str]:
    """Every generated file, mapped to the text it should hold."""
    return {OUTPUT: render()}


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def write_if_changed(path: Path, text: str) -> bool:
    """Write *text* as LF, and report whether it moved the file.

    Normalises the file's line endings before comparing, because this repo is
    checked out with ``core.autocrlf`` on Windows and an untouched generated
    file reads back as CRLF. Git stores LF either way.
    """
    if path.exists() and path.read_text(encoding="utf-8").replace("\r\n", "\n") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="report drift and exit non-zero instead of writing",
    )
    args = parser.parse_args(argv)

    files = rendered_files()
    drifted: list[Path] = []
    for path in sorted(files):
        text = files[path]
        if args.check:
            current = (
                path.read_text(encoding="utf-8").replace("\r\n", "\n") if path.exists() else None
            )
            if current != text:
                drifted.append(path)
        elif write_if_changed(path, text):
            drifted.append(path)

    verb = "stale" if args.check else "wrote"
    for path in drifted:
        print(f"{verb}: {path.relative_to(ROOT).as_posix()}")
    if args.check and drifted:
        print(
            f"\n{len(drifted)} generated file(s) disagree with the agent registry. "
            "Run: python scripts/gen_agent_matrix.py",
            file=sys.stderr,
        )
        return 1
    if not drifted:
        print("up to date" if args.check else "no changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
