"""Render the plugin skills and commands both hosts ship, from one shared source.

``plugins/codex/skills/`` used to be a hand-copy of ``plugins/claude-code/skills/``
and had already drifted: descriptions rewritten, headings retitled, one directory
renamed. Nothing detected it, because two files that say roughly the same thing in
different words look fine from either side alone. The bodies live in
``plugins/shared/`` now and every host artifact is rendered from them, so a drift
is a regenerate rather than a second hand-edit.

What is deliberately *not* here: a manifest-driven generator with platform
descriptors and fragment slots. That earns its place at roughly six hosts; we have
two. What is borrowed from that design is the render discipline, which costs
nothing and is what makes a golden test possible at all:

* fixed slot order (frontmatter, blank line, body),
* LF newlines regardless of the checkout's line-ending style,
* and **never** a timestamp, a version or a generator banner in a rendered file,
  a file that changes when nothing changed cannot be a golden.

Run ``python scripts/gen_plugin_content.py`` to write, ``--check`` to diff without
writing. ``tests/unit/cli/test_plugin_content.py`` runs the same renderer and fails
when a rendered file on disk has drifted from the shared source.

The shared source format, one file per item:

    ---
    frontmatter: |            # used by every host that has no override
      description: ...
    claude-code:              # optional per-host block
      dir: pre-modification   #   output directory, when the hosts disagree
      frontmatter: |          #   replaces the shared block entirely
        name: ...
    ---

    <body, shared by every host>

Bodies may reference a slash command as ``{{cmd:risk}}``; each host renders it in
its own syntax. Frontmatter is stored verbatim rather than as parsed keys so a
rendered file reproduces byte for byte, folded scalars and all.
"""

from __future__ import annotations

import argparse
import contextlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "plugins" / "shared"

#: Codex prompts ship inside the wheel rather than in ``plugins/codex/``. Codex's
#: plugin manifest has no slot for commands. A plugin may bundle ``skills/``,
#: ``hooks/``, ``assets/``, ``.mcp.json`` and ``.app.json``, and nothing else, so
#: the only surface that produces a Codex slash command is ``~/.codex/prompts/``,
#: which is local-only and written by the CLI. Package data is therefore the one
#: copy that actually reaches a user, and it follows the precedent already set by
#: the bundled ``repowise.core.upgrade`` changelog.
CODEX_PROMPT_DATA = (
    ROOT / "packages" / "cli" / "src" / "repowise" / "cli" / "agent_targets" / "_data" / "codex_prompts"
)


@dataclass(frozen=True)
class Host:
    """Where one host's files go, and how it spells the things hosts spell differently."""

    id: str
    skills_root: Path
    commands_root: Path
    #: ``{id}`` is the shared item's stem.
    command_filename: str
    #: How this host writes a reference to its own slash command.
    command_reference: str
    #: Frontmatter keys the host understands for a *command*. ``None`` means all
    #: of them. Codex prompt frontmatter defines ``description`` and
    #: ``argument-hint`` only, so anything else (``allowed-tools``, a Claude Code
    #: field) is dropped rather than emitted as a key the host will not read.
    command_frontmatter_keys: frozenset[str] | None = None


HOSTS: tuple[Host, ...] = (
    Host(
        id="claude-code",
        skills_root=ROOT / "plugins" / "claude-code" / "skills",
        commands_root=ROOT / "plugins" / "claude-code" / "commands",
        command_filename="{id}.md",
        command_reference="/repowise:{id}",
    ),
    Host(
        id="codex",
        skills_root=ROOT / "plugins" / "codex" / "skills",
        commands_root=CODEX_PROMPT_DATA,
        # Namespaced, because ~/.codex/prompts is a flat global directory shared
        # with every other tool the user has installed.
        command_filename="repowise-{id}.md",
        command_reference="/prompts:repowise-{id}",
        command_frontmatter_keys=frozenset({"description", "argument-hint"}),
    ),
)


# ---------------------------------------------------------------------------
# Shared source
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Item:
    """One skill or command, as authored in ``plugins/shared/``."""

    id: str
    source: Path
    meta: dict
    body: str

    def frontmatter_for(self, host: Host) -> str:
        block = self.meta.get(host.id, {})
        text = block.get("frontmatter") or self.meta.get("frontmatter")
        if not text:
            raise ValueError(f"{self.source}: no frontmatter for host {host.id!r}")
        return text

    def directory_for(self, host: Host) -> str:
        return self.meta.get(host.id, {}).get("dir", self.id)


_FRONTMATTER = re.compile(r"\A---\n(.*?\n)---\n+", re.S)
_TOKEN = re.compile(r"\{\{cmd:([a-z0-9-]+)\}\}")
_KEY_LINE = re.compile(r"\A([A-Za-z][A-Za-z0-9_-]*):")


def load_item(path: Path) -> Item:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    match = _FRONTMATTER.match(text)
    if match is None:
        raise ValueError(f"{path}: shared content must open with a --- frontmatter block")
    meta = yaml.safe_load(match.group(1)) or {}
    if not isinstance(meta, dict):
        raise ValueError(f"{path}: frontmatter must be a mapping")
    return Item(id=path.stem, source=path, meta=meta, body=text[match.end() :])


def load_items(kind: str) -> list[Item]:
    """Every shared item of *kind*, in id order.

    Sorted, because the render has to be reproducible and a directory listing is
    not ordered the same way on every filesystem.
    """
    return [load_item(path) for path in sorted((SHARED / kind).glob("*.md"))]


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def select_keys(frontmatter: str, allowed: frozenset[str] | None) -> str:
    """Drop top-level frontmatter keys *allowed* does not name.

    Line-based rather than parse-and-redump, because the stored text is verbatim
    and re-emitting it through a YAML dumper would reflow folded scalars, which
    turns every render into a diff and defeats the golden. A continuation line
    (indented, or blank inside a folded scalar) follows whichever key is open, so
    dropping a multi-line value takes its continuations with it.
    """
    if allowed is None:
        return frontmatter
    kept: list[str] = []
    keeping = False
    for line in frontmatter.splitlines(keepends=True):
        key = _KEY_LINE.match(line)
        if key is not None:
            keeping = key.group(1) in allowed
        if keeping:
            kept.append(line)
    return "".join(kept)


def render(item: Item, host: Host, *, frontmatter_keys: frozenset[str] | None) -> str:
    frontmatter = select_keys(item.frontmatter_for(host), frontmatter_keys)
    if not frontmatter.strip():
        raise ValueError(f"{item.source}: nothing left of the frontmatter for host {host.id!r}")
    body = _TOKEN.sub(lambda m: host.command_reference.format(id=m.group(1)), item.body)
    return f"---\n{frontmatter}---\n\n{body}"


def rendered_files() -> dict[Path, str]:
    """Every generated file, mapped to the text it should hold."""
    out: dict[Path, str] = {}
    for host in HOSTS:
        for item in load_items("skills"):
            path = host.skills_root / item.directory_for(host) / "SKILL.md"
            # No key filter: skill frontmatter is authored per host in the
            # shared file, so it already says what that host understands.
            out[path] = render(item, host, frontmatter_keys=None)
        for item in load_items("commands"):
            path = host.commands_root / host.command_filename.format(id=item.id)
            out[path] = render(item, host, frontmatter_keys=host.command_frontmatter_keys)
    return out


def orphaned_files() -> list[Path]:
    """Generated files on disk that no shared source produces any more, sorted.

    ``rendered_files`` says what *should* exist and nothing compared it to what
    *does*, so deleting a shared source left both rendered copies behind and
    ``--check`` called the tree clean. The consequences are not cosmetic: a
    retired command keeps shipping in the wheel and keeps being written into
    ``~/.codex/prompts`` on every install, and renaming a ``dir:`` override
    leaves the old ``SKILL.md`` for the host to load as a *second* skill with the
    same name, a small version of the hand-fork this whole file exists to close.

    Scoped by *content*, not by location. Globbing the directory and deleting
    everything unrecognised reaches a `README.md` a host directory carries, or a
    command somebody hand-maintains, silently and unprompted, on a plain
    regenerate. Every file this script writes opens with a `---` frontmatter
    fence, so requiring one is a cheap ownership test that a README fails and a
    generated artifact cannot.
    """
    expected = set(rendered_files())
    found: list[Path] = []
    for host in HOSTS:
        candidates = [
            *sorted(host.skills_root.glob("*/SKILL.md")),
            *sorted(host.commands_root.glob("*.md")),
        ]
        for path in candidates:
            if path in expected:
                continue
            try:
                if path.read_text(encoding="utf-8").startswith("---\n"):
                    found.append(path)
            except (OSError, ValueError):
                # Unreadable or not UTF-8: not something this script wrote, and
                # certainly not something to delete on a guess.
                continue
    return found


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def write_if_changed(path: Path, text: str) -> bool:
    """Write *text* as LF, and report whether it moved the file.

    The comparison normalises the file's line endings first. This repo is checked
    out with ``core.autocrlf`` on Windows, so an untouched generated file reads
    back as CRLF, so comparing raw bytes would rewrite all thirty on every
    run and report a drift that does not exist. Git stores LF either way.
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

    orphans = orphaned_files()
    if not args.check:
        for path in orphans:
            with contextlib.suppress(OSError):
                path.unlink()
            # Only a *skill* owns its directory. Doing this for every orphan
            # removed the commands root itself once the last command retired,
            # including the Codex package-data directory, after which
            # ``bundled_prompts`` raised FileNotFoundError out of ``install``.
            # ``rmdir`` refuses a non-empty directory, so a skill that also
            # carried assets keeps them.
            if path.name == "SKILL.md":
                with contextlib.suppress(OSError):
                    path.parent.rmdir()

    verb = "stale" if args.check else "wrote"
    for path in drifted:
        print(f"{verb}: {path.relative_to(ROOT).as_posix()}")
    for path in orphans:
        print(f"{'orphaned' if args.check else 'removed'}: {path.relative_to(ROOT).as_posix()}")
    if args.check and (drifted or orphans):
        print(
            f"\n{len(drifted) + len(orphans)} generated file(s) disagree with "
            "plugins/shared/. Run: python scripts/gen_plugin_content.py",
            file=sys.stderr,
        )
        return 1
    if not drifted and not orphans:
        print("up to date" if args.check else "no changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
