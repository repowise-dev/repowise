"""The agent support matrix and every published tool count stay true to the code.

Two guards, and they cover different failure modes.

The first is the ordinary golden: ``docs/agent/INTEGRATIONS.md`` on disk must equal
what ``scripts/gen_agent_matrix.py`` renders. Asserted against the **file**, never
against the generator's return value, for the reason the plugin-content golden
already writes down: a renderer can be perfectly correct about text nobody ever
wrote to disk.

The second is the one this whole exercise exists for. A generated doc protects
itself and nothing else, and the tool count was never wrong in one place. It was
wrong in six, including twice in the README two hundred lines apart, because every
mention was hand-typed and no two were checked against each other. So the counts
are computed from the live MCP registry and asserted **as exact phrases** against
the artifacts in ``COUNT_CLAIMS``. Add a tool and this test names each file to edit.

``COUNT_CLAIMS`` covers each sentence individually, not each file, so a file
appearing in it is not evidence that every count it publishes is guarded. Adding a
new count sentence anywhere means adding a row here. Release changelogs are
excluded on purpose: they record what was true at the time.

Accepted cost: the rows that match source docstrings are pinned to the current
hard wrap, so re-flowing one of those docstrings turns this red. That is cheap to
fix and the failure names the file and the exact phrase, which is a better trade
than matching loosely enough to miss a real stale count.

Phrases rather than a regex on purpose: several artifacts correctly say "the ten
flagship tools", which is a deliberate subset of the eleven-tool default surface
and not a stale count. A pattern loose enough to catch drift is loose enough to
condemn those, and a test that cries wolf gets its assertion deleted.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _generator():
    """Load the generator by path; ``scripts/`` is not an importable package."""
    path = ROOT / "scripts" / "gen_agent_matrix.py"
    spec = importlib.util.spec_from_file_location("gen_agent_matrix", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["gen_agent_matrix"] = module
    spec.loader.exec_module(module)
    return module


GEN = _generator()
COUNTS = GEN.tool_counts()


def _on_disk(path: Path) -> str:
    """Read *path* with line endings normalised.

    The repo is checked out with ``core.autocrlf`` on Windows, so a generated
    file reads back as CRLF while the generator renders LF. Git stores LF.
    """
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


# ---------------------------------------------------------------------------
# The generated matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", sorted(GEN.rendered_files()), ids=lambda p: p.relative_to(ROOT).as_posix()
)
def test_the_generated_matrix_matches_the_registry(path: Path) -> None:
    expected = GEN.rendered_files()[path]
    assert path.exists(), (
        f"{path.relative_to(ROOT).as_posix()} is missing. "
        "Run: python scripts/gen_agent_matrix.py"
    )
    assert _on_disk(path) == expected, (
        f"{path.relative_to(ROOT).as_posix()} has drifted from the agent registry. "
        "Run: python scripts/gen_agent_matrix.py"
    )


def test_rendering_is_idempotent() -> None:
    """``--check`` against a clean tree, which is what a contributor runs."""
    assert GEN.main(["--check"]) == 0


def test_every_registered_target_has_a_row() -> None:
    """The table is the registry, not a subset of it."""
    from repowise.cli.agent_targets import registry

    text = _on_disk(GEN.OUTPUT)
    for target in registry.all_targets():
        assert f"| {target.display_name} |" in text or f"[{target.display_name}](" in text, (
            f"{target.id} is registered but has no row in the matrix"
        )
        assert f"`{target.id}`" in text, f"{target.id} is missing from the target id list"


def test_no_paste_config_host_is_secretly_a_registered_target() -> None:
    """A host promoted to a real integration must leave the Paste-config list.

    The Paste-config names are hand-authored, because a host we write no code
    for has no descriptor to derive them from. This is the one way that list can
    go stale: Phase 5 adds Cursor as a target and the doc keeps advertising it as
    something we do not wire.
    """
    from repowise.cli.agent_targets import registry

    known = {t.display_name.casefold() for t in registry.all_targets()}
    known |= {t.id.casefold() for t in registry.all_targets()}
    collisions = [name for name in GEN.PASTE_CONFIG_HOSTS if name.casefold() in known]
    assert not collisions, (
        f"{collisions} are registered targets and must be removed from "
        "PASTE_CONFIG_HOSTS in scripts/gen_agent_matrix.py"
    )


def _readme_badge_rows() -> dict[str, set[str]]:
    """Tier label to the set of badge alt texts under it, from README.md.

    Scoped to the ``## Supported agents`` section, which is load-bearing rather
    than tidy. A plain search of the whole README is satisfied by a badge
    sitting anywhere at all, so it passes on a VS Code badge moved into the
    Full-tier row, and it collides with the language block below (``alt="Go"``,
    ``alt="Java"``), which would pre-satisfy any future agent sharing a name
    with a language.
    """
    readme = _on_disk(ROOT / "README.md")
    start = readme.index("## Supported agents")
    section = readme[start : readme.index("\n## ", start + 1)]
    rows: dict[str, set[str]] = {}
    for block in re.findall(r"<p>(.*?)</p>", section, re.S):
        label = re.search(r"<strong>(.*?)\s*(?:tier)?\s*&nbsp;</strong>", block)
        if label is None:
            continue
        rows[label.group(1).strip()] = set(re.findall(r'alt="([^"]+)"', block))
    return rows


def test_the_readme_badge_rows_match_the_registry() -> None:
    """The README badges are hand-maintained, so something has to check them.

    Everything else about the tiers is derived, which makes the badges the one
    place a fourth agent can be silently missing, or an old one can sit under
    the wrong tier. They cannot be generated: a brand colour and a logo slug
    per agent are editorial, and the README is not a generated file.
    """
    from repowise.cli.agent_targets import registry
    from repowise.cli.agent_targets.types import derive_tier

    expected: dict[str, set[str]] = {}
    for target in registry.all_targets():
        label = GEN.TIER_BLURBS[derive_tier(target).value][0]
        expected.setdefault(label, set()).add(target.display_name)

    found = _readme_badge_rows()
    assert found == expected, (
        f"README.md's agent badge rows are {found}, the registry says {expected}. "
        "Every registered agent needs exactly one badge, under its own tier row, "
        "in the '## Supported agents' section."
    )


def test_the_readme_agent_headline_counts_the_registry() -> None:
    """The sentence above the badges is a count, and counts drift.

    This is the same failure the tool count spent twenty-four releases in. A
    headline that says three while the registry holds four is the version of it
    that a reader meets first.
    """
    from repowise.cli.agent_targets import registry
    from repowise.cli.agent_targets.types import Tier, derive_tier

    targets = registry.all_targets()
    full = [t for t in targets if derive_tier(t) is Tier.FULL]
    expected = (
        f"**{GEN.spell(len(targets)).capitalize()} agents wired end to end · "
        f"{GEN.spell(len(full))} at the Full tier"
    )
    assert expected in _on_disk(ROOT / "README.md"), (
        f"README.md does not open the agent section with {expected!r}. "
        f"The registry holds {len(targets)} targets, {len(full)} of them Full tier."
    )


def test_the_hooks_column_cannot_lie() -> None:
    """A target's declared HOOKS capability agrees with its hook adapter.

    The matrix reads the capability; ``derive_tier`` reads the adapter name.
    Nothing forced those two to agree, so a descriptor could publish a Hooks
    column of `Yes` at Good tier, or Full tier with an empty Hooks column, and
    both would render without complaint.
    """
    from repowise.cli.agent_targets import registry
    from repowise.cli.agent_targets.types import Capability, capabilities_of

    for target in registry.all_targets():
        declares = Capability.HOOKS in capabilities_of(target)
        assert declares == bool(target.hook_adapter), (
            f"{target.id}: HOOKS capability is {declares} but hook_adapter is "
            f"{target.hook_adapter!r}"
        )


# ---------------------------------------------------------------------------
# Published tool counts
# ---------------------------------------------------------------------------

#: Every artifact that publishes an MCP tool count, and the exact phrase it must
#: carry. ``{n}`` is the count as digits, ``{w}`` spelled out, ``{W}``
#: capitalised. Keys name the count from :func:`gen_agent_matrix.tool_counts`.
#:
#: Deliberately not exhaustive over the repo: release changelogs record what was
#: true at the time and must not be retro-edited, and the several artifacts that
#: say "the ten flagship tools" are describing a documented subset rather than
#: the default surface.
COUNT_CLAIMS: tuple[tuple[str, str, str], ...] = (
    # The README and the image at the top of it count the flagship tools, a
    # narrower claim than the advertised surface. See NON_FLAGSHIP_TOOLS.
    ("README.md", "flagship", "**{w} task-shaped MCP tools**"),
    ("README.md", "flagship", "## The {w} MCP tools"),
    ("README.md", "flagship", "#the-{w}-mcp-tools"),
    ("README.md", "flagship", "decisions, and {w} MCP tools"),
    ("README.md", "flagship", "{W} is a deliberate ceiling"),
    ("docs/README.md", "flagship", "decisions, and {w} MCP tools"),
    ("docs/README.md", "flagship", "The {w} task-shaped tools,"),
    ("docs/agent/MCP_TOOLS.md", "total", "{n} tools are registered in total"),
    ("docs/agent/MCP_TOOLS.md", "single_repo", "advertises {n} by default"),
    ("docs/business/COMMERCIAL.md", "single_repo", "the {w} MCP tools,"),
    ("docs/business/COMMERCIAL.md", "single_repo", "| {W} MCP tools |"),
    ("docs/start/USER_GUIDE.md", "single_repo", "**{W} tools, task-shaped.**"),
    ("docs/architecture/pluggable-storage.md", "single_repo", "{w} MCP tools still expose"),
    ("website/mcp-server.md", "total", "It registers {n} tools:"),
    ("website/mcp-server.md", "single_repo", "a curated {n}-tool default surface"),
    ("packages/server/README.md", "total", "registers {n} MCP tools"),
    ("packages/server/README.md", "single_repo", "advertises **{n} by default**"),
    (".claude-plugin/marketplace.json", "single_repo", "{w} task-shaped MCP tools"),
    (
        "plugins/claude-code/.claude-plugin/plugin.json",
        "single_repo",
        "{w} task-shaped MCP tools",
    ),
    ("docs/agent/MCP_TOOLS.md", "single_repo", "**Default (single-repo):** {n} tools"),
    ("docs/agent/MCP_TOOLS.md", "single_repo", "those {n} plus"),
    ("docs/architecture/ARCHITECTURE.md", "single_repo", "advertises **{n}** tools by default"),
    ("docs/reference/CLI_REFERENCE.md", "lean", "the {w}-tool agent-lean profile"),
    ("packages/server/README.md", "total", "{n} registered MCP tools"),
    ("packages/server/README.md", "single_repo", "({n} advertised by default"),
    # The CLI help text is the copy every user reads, and it was the only place
    # publishing the opt-in and lean counts outside a doc.
    (
        "packages/cli/src/repowise/cli/commands/mcp_cmd.py",
        "single_repo",
        "{w} by default in single-repo mode",
    ),
    (
        "packages/cli/src/repowise/cli/commands/mcp_cmd.py",
        "workspace_extra",
        "plus {w} more by default",
    ),
    ("packages/cli/src/repowise/cli/commands/mcp_cmd.py", "opt_in", "{W} more are opt-in"),
    # The lean count is written twice in this file, in the --tools help and in
    # the worked example. One row matching either would let the other go stale,
    # so each occurrence is pinned by the text around it.
    (
        "packages/cli/src/repowise/cli/commands/mcp_cmd.py",
        "lean",
        "{w}-tool agent-lean profile. Overrides",
    ),
    (
        "packages/cli/src/repowise/cli/commands/mcp_cmd.py",
        "lean",
        "--tools lean        # {w}-tool agent-lean profile",
    ),
    ("scripts/gen_readme_hero.py", "flagship", '"{n} MCP tools"'),
    ("scripts/gen_readme_hero.py", "flagship", "decisions, and {w} MCP tools"),
    (".github/assets/one-index.svg", "flagship", "{n} MCP tools"),
    (".github/assets/one-index.svg", "flagship", "decisions, and {w} MCP tools"),
    (".github/assets/one-index-dark.svg", "flagship", "{n} MCP tools"),
    (".github/assets/one-index-dark.svg", "flagship", "decisions, and {w} MCP tools"),
)


def test_the_flagship_set_is_a_real_subset_of_the_default_surface() -> None:
    """Removing a name that is not there would silently do nothing.

    ``flagship`` is the default surface minus :data:`NON_FLAGSHIP_TOOLS`. If a
    tool in that set is renamed or made opt-in, the subtraction stops removing
    anything and the README's ten quietly becomes the surface's eleven, which
    is precisely the drift this file exists to catch.
    """
    from repowise.core.registry import mcp_tool_registry
    from repowise.server.mcp_server import ensure_full_surface
    from repowise.server.mcp_server._tool_selection import resolve_enabled_tools

    ensure_full_surface()
    default = resolve_enabled_tools(mcp_tool_registry.entries(), is_workspace=False)
    absent = GEN.NON_FLAGSHIP_TOOLS - default
    assert not absent, (
        f"{sorted(absent)} are excluded from the flagship count but are not in the "
        "default surface, so excluding them does nothing. Update NON_FLAGSHIP_TOOLS "
        "in scripts/gen_agent_matrix.py."
    )
    assert COUNTS["flagship"] == COUNTS["single_repo"] - len(GEN.NON_FLAGSHIP_TOOLS)


def test_the_readme_tool_table_lists_exactly_the_flagship_tools() -> None:
    """The count above the table and the rows in it are one claim, not two.

    This is how the README drifted: a row was added for `list_repos` and the
    heading followed it to eleven while the pitch above stayed at ten. A phrase
    guard on the heading alone would have called that consistent.
    """
    readme = _on_disk(ROOT / "README.md")
    heading = f"## The {GEN.spell(COUNTS['flagship'])} MCP tools"
    start = readme.index(heading)
    section = readme[start : readme.index("\n## ", start + 1)]
    listed = re.findall(r"^\| `([a-z_]+)\(", section, re.M)
    assert len(listed) == COUNTS["flagship"], (
        f"README.md's tool table lists {len(listed)} tools ({listed}) but the "
        f"flagship count is {COUNTS['flagship']}."
    )
    excluded = sorted(set(listed) & GEN.NON_FLAGSHIP_TOOLS)
    assert not excluded, (
        f"{excluded} is excluded from the README's count but has a row in its table."
    )


@pytest.mark.parametrize(
    "relpath", [".github/assets/one-index.svg", ".github/assets/one-index-dark.svg"]
)
def test_the_hero_image_draws_as_many_tools_as_it_claims(relpath: str) -> None:
    """The card headline and the chips under it are the same claim.

    They came apart once already: the headline moved and the chip list did not,
    so the first image in the README said eleven over ten chips.
    """
    svg = _on_disk(ROOT / relpath)
    chips = re.findall(r'<text[^>]*class="mono"[^>]*>([^<]+)</text>', svg)
    assert len(chips) == COUNTS["flagship"], (
        f"{relpath} draws {len(chips)} tool chips ({chips}) against a headline of "
        f"{COUNTS['flagship']}. Fix the list in scripts/gen_readme_hero.py and "
        "regenerate both themes."
    )


def _phrase(template: str, count: int) -> str:
    word = GEN.spell(count)
    return template.format(n=count, w=word, W=word.capitalize())


def _appears(phrase: str, text: str) -> bool:
    """Whether *phrase* occurs in *text* as a whole token, not as a tail.

    A plain substring test reports "7 tools are registered in total" inside "17
    tools are registered in total", which condemns the correct sentence for
    containing the digits of a wrong one.
    """
    return re.search(r"(?<![0-9A-Za-z])" + re.escape(phrase), text) is not None


@pytest.mark.parametrize(
    ("relpath", "key", "template"),
    COUNT_CLAIMS,
    ids=[f"{rel}:{tmpl}" for rel, _, tmpl in COUNT_CLAIMS],
)
def test_a_published_tool_count_matches_the_registry(
    relpath: str, key: str, template: str
) -> None:
    path = ROOT / relpath
    assert path.exists(), f"{relpath} is named in COUNT_CLAIMS but does not exist"
    expected = _phrase(template, COUNTS[key])
    assert expected in _on_disk(path), (
        f"{relpath} does not say {expected!r}. The live MCP surface is "
        f"{COUNTS['total']} tools registered, {COUNTS['single_repo']} advertised in "
        f"single-repo mode, {COUNTS['workspace']} in workspace mode. Update the file, "
        "then regenerate docs/agent/INTEGRATIONS.md and the README hero SVGs."
    )


@pytest.mark.parametrize(
    ("relpath", "key", "template"),
    COUNT_CLAIMS,
    ids=[f"{rel}:{tmpl}" for rel, _, tmpl in COUNT_CLAIMS],
)
def test_no_artifact_still_carries_a_superseded_tool_count(
    relpath: str, key: str, template: str
) -> None:
    """The same sentence with a different number must not survive alongside it.

    The README held both "ten task-shaped MCP tools" and "The eleven MCP tools"
    at once, so asserting the right phrase is present would have passed on it.
    A stale count is usually a second copy of a sentence, not a missing one.
    """
    text = _on_disk(ROOT / relpath)
    stale = [
        _phrase(template, other)
        # Every count the generator can spell, which is the set a hand-edit
        # could plausibly have left behind.
        for other in sorted(GEN._WORDS)
        if other != COUNTS[key] and _appears(_phrase(template, other), text)
    ]
    assert not stale, f"{relpath} still carries a superseded count: {stale}"
