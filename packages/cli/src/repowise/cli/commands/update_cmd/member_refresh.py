"""Which workspace members need their per-run refresh, by fingerprint.

Every workspace update ends by restamping each up-to-date member's DB row and
re-rendering each member's editor files (CLAUDE.md, AGENTS.md, ...). Done for
every member, a no-change update cost O(all repos): about a second per repo.
A member's refresh reads only its own store, config, state and editor files,
the editor templates and the date, so a digest of those taken right after a
refresh says whether the next refresh could write anything different. The
next run refreshes only the members whose digest moved.
"""

from __future__ import annotations

import functools
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

#: Recorded digests, one per member path, beside the other workspace data.
_STAMPS_FILE = "member_refresh.json"

#: Repo-relative files a refresh reads or writes. The store and its WAL catch
#: every write to the index (a sync, a decision, a health run); the editor
#: files catch a user deleting or editing one. A new integration that writes a
#: project file adds its path here.
_MEMBER_FILES = (
    ".repowise/wiki.db",
    ".repowise/wiki.db-wal",
    ".repowise/state.json",
    ".repowise/config.yaml",
    ".gitignore",
    ".git/info/exclude",
    ".claude/CLAUDE.md",
    "AGENTS.md",
    ".vscode/mcp.json",
    ".github/copilot-instructions.md",
)

#: The editor-file templates, hashed into the code stamp so a template edit
#: re-renders every member even without a version change.
_EDITOR_TEMPLATES = ("_editor_block.j2", "claude_md.j2", "agents_md.j2")


@functools.cache
def _code_stamp() -> str:
    """The installed repowise version plus the editor generator's source."""
    import repowise.core as core
    from repowise.cli import __version__

    generation = Path(core.__file__).parent / "generation"
    sources = sorted((generation / "editor_files").glob("*.py"))
    sources += [generation / "templates" / name for name in _EDITOR_TEMPLATES]
    digest = hashlib.sha256(__version__.encode())
    for source in sources:
        try:
            digest.update(source.read_bytes())
        except OSError:
            digest.update(b"-")
    return digest.hexdigest()


def member_fingerprint(repo_path: Path) -> str | None:
    """A digest of everything a refresh of *repo_path* depends on, or ``None``.

    ``None`` when the store is a configured database rather than the member's
    own file: its writes leave no trace here, so that member always refreshes.
    The UTC date is in the digest because the refresh advances the store's
    "indexed at" stamp, which every surface shows at day resolution; an
    unchanged member therefore still refreshes once a day.

    Ceiling: uncommitted tree edits the generator reads live (a deleted hotspot
    file, a manifest behind the opt-in ``commands`` section) show up at the next
    commit, index write or day; reading the tree would cost the walk this skip
    exists to avoid. So does an integration that swallows its own write error.
    """
    import os

    from repowise.core.git_head import read_head_commit

    # Same variables as persistence.database.DB_ENV_VARS, read without importing
    # the ORM, which a skipped member never needs.
    if os.environ.get("REPOWISE_DB_URL") or os.environ.get("REPOWISE_DATABASE_URL"):
        return None
    parts = [_code_stamp(), datetime.now(UTC).date().isoformat(), read_head_commit(repo_path) or ""]
    for name in _MEMBER_FILES:
        try:
            st = (repo_path / name).stat()
        except OSError:
            parts.append(f"{name}:-")
            continue
        # An empty WAL holds no writes: a read-only reader (the workspace's
        # sync-time probe) leaves one behind, which must not read as a write.
        if name.endswith("-wal") and st.st_size == 0:
            parts.append(f"{name}:-")
        else:
            # Size as well as mtime: NTFS can give two quick writes one mtime.
            parts.append(f"{name}:{st.st_mtime_ns}:{st.st_size}")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def _stamps_path(ws_root: Path) -> Path:
    from repowise.core.workspace.config import WORKSPACE_DATA_DIR

    return ws_root / WORKSPACE_DATA_DIR / _STAMPS_FILE


def load_member_stamps(ws_root: Path) -> dict[str, str]:
    """The digests the last refresh recorded, keyed by member path."""
    try:
        data = json.loads(_stamps_path(ws_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def save_member_stamps(ws_root: Path, stamps: dict[str, str]) -> None:
    """Record *stamps*. Best-effort: a lost file only costs one full refresh."""
    from repowise.core.fsutils import atomic_write_text

    path = _stamps_path(ws_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(stamps, indent=1, sort_keys=True))
    except OSError:
        pass


def is_unchanged(repo_path: Path, stamps: dict[str, str]) -> bool:
    """Whether *repo_path*'s last refresh still holds, so this one can be skipped."""
    fingerprint = member_fingerprint(repo_path)
    return fingerprint is not None and stamps.get(str(repo_path)) == fingerprint
