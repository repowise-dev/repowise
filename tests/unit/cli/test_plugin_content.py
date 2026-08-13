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

import contextlib
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
        # Strip frontmatter, since that half is allowed to differ per host.
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
    ignores, which is exactly the kind of thing that accumulates until the two
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


def test_the_tree_carries_no_generated_file_without_a_shared_source() -> None:
    """A retired command leaves two files behind, and only this notices.

    `rendered_files` says what *should* exist; nothing compared it to what does,
    so deleting a shared source left both rendered copies on disk and `--check`
    called the tree clean. The retired command then keeps shipping in the wheel
    and keeps being written into `~/.codex/prompts` on every install, and a
    renamed `dir:` override leaves the old SKILL.md for the host to load as a
    second skill with the same name.
    """
    assert GEN.orphaned_files() == []


def test_the_sweep_does_not_reach_a_hand_maintained_file() -> None:
    """The scope the docstring promised, and the first cut did not honour.

    Globbing `*.md` and deleting everything unrecognised removed a README a host
    directory carried, silently and unprompted, on a plain regenerate. Every file
    the generator writes opens with a `---` fence, so requiring one is an
    ownership test a README fails.
    """
    readme = GEN.HOSTS[0].commands_root / "README.md"
    notes = GEN.HOSTS[0].skills_root / "team-only" / "SKILL.md"
    readme.write_text("# Commands\n\nHand-maintained, no frontmatter.\n", encoding="utf-8")
    notes.parent.mkdir(parents=True, exist_ok=True)
    notes.write_text("# Team only\n\nAlso no frontmatter.\n", encoding="utf-8")
    try:
        assert GEN.orphaned_files() == []
        assert GEN.main([]) == 0
        assert readme.exists() and notes.exists()
    finally:
        readme.unlink(missing_ok=True)
        notes.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            notes.parent.rmdir()


def test_retiring_every_command_does_not_delete_the_directory_itself() -> None:
    """`rmdir` on each orphan's parent removed the commands root.

    Including the Codex package-data directory, after which `bundled_prompts()`
    raised FileNotFoundError out of `install`, `stale_prompts` and `doctor`.
    Only a *skill* owns its directory.
    """
    stray = GEN.CODEX_PROMPT_DATA / "repowise-retired.md"
    stray.write_text("---\ndescription: gone\n---\n\nbody\n", encoding="utf-8")
    try:
        assert GEN.main([]) == 0
    finally:
        stray.unlink(missing_ok=True)
    assert GEN.CODEX_PROMPT_DATA.is_dir(), "the sweep removed the package-data directory"
    assert GEN.HOSTS[0].commands_root.is_dir()
    # The thing that broke when it did not.
    from repowise.cli.agent_targets.targets import codex

    assert len(codex.bundled_prompts()) == 18


def test_a_retired_shared_source_is_detected_and_swept(tmp_path, monkeypatch) -> None:
    """Proven by construction rather than by trusting the sweep exists.

    Both hosts' copies are planted directly, because the real operation (delete
    a file from `plugins/shared/`) cannot be done to the checkout from a test.
    """
    stray_claude = GEN.HOSTS[0].commands_root / "retired-command.md"
    stray_codex = GEN.CODEX_PROMPT_DATA / "repowise-retired-command.md"
    for stray in (stray_claude, stray_codex):
        stray.write_text("---\ndescription: gone\n---\n\nbody\n", encoding="utf-8")
    try:
        assert set(GEN.orphaned_files()) == {stray_claude, stray_codex}
        assert GEN.main(["--check"]) == 1, "--check called an orphaned tree clean"
        assert GEN.main([]) == 0
        assert not stray_claude.exists() and not stray_codex.exists()
    finally:
        stray_claude.unlink(missing_ok=True)
        stray_codex.unlink(missing_ok=True)
    assert GEN.main(["--check"]) == 0, "the sweep did not leave the tree clean"
