"""The golden that keeps the two plugin hosts from forking again.

`plugins/codex/skills/` was a hand-copy of `plugins/claude-code/skills/` and had
already drifted: every description rewritten, headings retitled ("Dead Code
Cleanup With Repowise" against "with"), one directory renamed. Nothing caught it,
because a stale copy of a document is indistinguishable from a deliberate variant
unless something asserts which one it is.

So the assertions here are on the **generated files**, not on the generator's
return value. A renderer can be perfectly correct about text nobody ever wrote to
disk; that is the failure mode this test exists to make impossible.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _generator():
    """Import `scripts/gen_plugin_content.py`, which is not on the import path."""
    path = ROOT / "scripts" / "gen_plugin_content.py"
    spec = importlib.util.spec_from_file_location("gen_plugin_content", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


GEN = _generator()


def _on_disk(path: Path) -> str:
    """The file's text with line endings normalised.

    The generator writes LF, but this repo is checked out with `core.autocrlf` on
    Windows, so an untouched generated file reads back as CRLF. Comparing raw
    bytes would fail on every Windows checkout and pass on every POSIX one, which
    is worse than not testing it.
    """
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


@pytest.mark.parametrize(
    "path", sorted(GEN.rendered_files()), ids=lambda p: p.relative_to(ROOT).as_posix()
)
def test_generated_plugin_file_matches_the_shared_source(path: Path) -> None:
    expected = GEN.rendered_files()[path]
    assert path.exists(), (
        f"{path.relative_to(ROOT).as_posix()} is missing. "
        "Run: python scripts/gen_plugin_content.py"
    )
    assert _on_disk(path) == expected, (
        f"{path.relative_to(ROOT).as_posix()} has drifted from plugins/shared/. "
        "Edit the shared source, then run: python scripts/gen_plugin_content.py"
    )


def test_the_two_hosts_ship_the_same_skill_bodies() -> None:
    """The fork, stated directly rather than inferred from the file list.

    The per-file test above would still pass if someone gave a host its own copy
    of a body in the shared source. This is the property that actually matters:
    one body, rendered twice.
    """
    hosts = {host.id: host for host in GEN.HOSTS}
    for item in GEN.load_items("skills"):
        claude = GEN.render(item, hosts["claude-code"], frontmatter_keys=None)
        codex = GEN.render(item, hosts["codex"], frontmatter_keys=None)
        # Strip frontmatter — that half is allowed to differ per host.
        claude_body = claude.split("\n---\n\n", 1)[1]
        codex_body = codex.split("\n---\n\n", 1)[1]
        # ...but only where the hosts spell a slash command differently.
        assert claude_body.replace("/repowise:", "@") == codex_body.replace(
            "/prompts:repowise-", "@"
        ), f"{item.source.name}: the two hosts have different skill bodies"


def test_every_skill_reaches_both_hosts() -> None:
    """A shared file that renders for one host only is a fork with extra steps."""
    rendered = GEN.rendered_files()
    for item in GEN.load_items("skills"):
        for host in GEN.HOSTS:
            path = host.skills_root / item.directory_for(host) / "SKILL.md"
            assert path in rendered, f"{item.source.name} renders nothing for {host.id}"


def test_codex_prompts_carry_only_frontmatter_codex_reads() -> None:
    """`allowed-tools` is a Claude Code field.

    Emitting it into a Codex prompt is not harmful, it is just a key the host
    ignores — which is exactly the kind of thing that accumulates until the two
    formats are a fork again.
    """
    prompts = sorted(GEN.CODEX_PROMPT_DATA.glob("*.md"))
    assert prompts, "no Codex prompts were generated"
    for path in prompts:
        frontmatter = _on_disk(path).split("---\n")[1]
        keys = {line.split(":", 1)[0] for line in frontmatter.splitlines() if ":" in line}
        assert keys <= {"description", "argument-hint"}, f"{path.name} carries {keys}"


def test_rendering_is_idempotent() -> None:
    """`--check` against a clean tree, which is what CI runs."""
    assert GEN.main(["--check"]) == 0
