"""Removal for the two managed blocks the *generators* write.

Neither had a removal path anywhere in the CLI before this module. The pipeline
writes a ``REPOWISE`` block into ``.claude/CLAUDE.md`` and a ``REPOWISE_AGENTS``
block into ``AGENTS.md``; ``BaseEditorFileGenerator`` exposes ``render``,
``write`` and ``render_full`` and nothing that takes either back out, and no
agent target's ``uninstall`` names either path. So both survived
``agents remove --target=all`` and every other command we ship.

**``AGENTS.md`` holds two different repowise blocks and they are not
interchangeable.** The generator's is fenced by ``REPOWISE_AGENTS``; the one
codex, opencode and hermes share is fenced by ``REPOWISE_DISTILL``. Only the
second has multiple managers, so only the second consults
``registry.other_managers_of``. Routing this one through that guard would ask
whether another *agent* still needs a block written by the *indexer*, which is a
question about the wrong file. Anything here that treats "the repowise block in
AGENTS.md" as one concept is already wrong.

The removal itself is ``marker_block.remove``, the hardened one the targets use:
it refuses an orphaned or duplicated pair rather than guessing at a repair, and
it deletes the file only when the remaining raw text is blank.

This lives in the CLI rather than beside the generators because the marker
implementation does, and the import direction runs cli into core. A second
marker remover in core, with its own idea of what a malformed pair means, is
exactly the duplication that produced this track's worst bugs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from repowise.cli.agent_targets.formats import marker_block
from repowise.cli.agent_targets.formats.marker_block import BlockState
from repowise.cli.agent_targets.types import FileAction


@dataclass(frozen=True)
class GeneratedBlock:
    """One managed block, named by where it lives and how it is fenced."""

    #: Stable id used in the plan and in ``--format json``.
    id: str
    #: Path relative to the repo root.
    relative_path: str
    marker_tag: str

    def path(self, repo_path: Path) -> Path:
        return repo_path / self.relative_path

    @property
    def start(self) -> str:
        from repowise.core.generation.editor_files.base import BaseEditorFileGenerator

        return BaseEditorFileGenerator.MARKER_START_FMT.format(tag=self.marker_tag)

    @property
    def end(self) -> str:
        from repowise.core.generation.editor_files.base import BaseEditorFileGenerator

        return BaseEditorFileGenerator.MARKER_END_FMT.format(tag=self.marker_tag)


#: Derived from the generator classes rather than retyped, so a generator that
#: renames its file or its tag cannot leave this list quietly pointing at a path
#: that no longer exists.
def generated_blocks() -> tuple[GeneratedBlock, ...]:
    from repowise.core.generation.editor_files.agents_md import AgentsMdGenerator
    from repowise.core.generation.editor_files.claude_md import ClaudeMdGenerator

    return (
        GeneratedBlock(
            id="claude-md",
            relative_path=f".claude/{ClaudeMdGenerator.filename}",
            marker_tag=ClaudeMdGenerator.marker_tag,
        ),
        GeneratedBlock(
            id="agents-md",
            relative_path=AgentsMdGenerator.filename,
            marker_tag=AgentsMdGenerator.marker_tag,
        ),
    )


def inspect_block(block: GeneratedBlock, repo_path: Path) -> BlockState:
    """What *block* currently is in *repo_path*, without writing."""
    return marker_block.inspect(block.path(repo_path), block.start, block.end).state


def remove_block(block: GeneratedBlock, repo_path: Path) -> tuple[Path, FileAction, str | None]:
    """Strip *block* from its file, or say why it was left alone.

    The state is re-inspected after the removal rather than reused from before
    it, because the whole question here is what the file looks like afterwards.
    """
    path = block.path(repo_path)
    if marker_block.remove(path, block.start, block.end):
        return path, FileAction.REMOVED, None

    state = marker_block.inspect(path, block.start, block.end).state
    if state in (BlockState.ABSENT_FILE, BlockState.ABSENT):
        return path, FileAction.NOT_FOUND, None
    if state is BlockState.PRESENT:
        # The block is still exactly where it was, which means the write failed
        # rather than that we declined. Those need opposite things from the
        # user, and they also want different exit codes: this is a failure (1),
        # not a leftover (3).
        return path, FileAction.FAILED, marker_block.refusal_reason(state)
    return path, FileAction.KEPT, marker_block.refusal_reason(state)
