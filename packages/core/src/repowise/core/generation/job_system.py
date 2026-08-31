"""Job system for the repowise generation engine.

JobSystem manages long-running generation jobs via JSON checkpoint files.
Each job maps to a single {job_id}.json file in the configured jobs_dir.
The checkpoint records progress (completed/failed pages), current level, and
job status.

Durability contract
-------------------
A job's state lives in memory and is flushed to disk atomically. Page
completions are flushed on a bound (``_FLUSH_EVERY_PAGES``), at each level
boundary, and whenever a run ends or is interrupted; everything else is
flushed as it happens. So an unclean kill can lose at most the last
``_FLUSH_EVERY_PAGES`` entries of ``completed_page_ids``.

That window is safe because *nothing reads this field to decide what to
generate*. A resumed run derives its skip set from the vector store (see
``_GenerationRun._seed_resume``), never from this file, so a lost entry
cannot make a resume skip a page or regenerate one it should have kept.
``failed_page_ids`` and every status transition are flushed immediately,
because the post-run failure report reads them back off disk.

Writes go through ``atomic_write_text``: a reader now never sees the
truncated file a crash used to be able to leave behind mid-write.

Phase 4 will replace this with a full SQLAlchemy-backed job table.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import structlog

from repowise.core.fsutils import atomic_write_text

log = structlog.get_logger(__name__)

# Page completions buffered before a flush. Bounds both the write amplification
# (one whole-file rewrite per page rebuilt a list that grows by one each time)
# and the state a kill can lose.
_FLUSH_EVERY_PAGES = 128

JobStatus = Literal["pending", "running", "completed", "failed", "paused"]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------


@dataclass
class Checkpoint:
    """Persistent state for a single generation job."""

    job_id: str
    status: str  # JobStatus literal
    created_at: str  # ISO-8601 UTC
    updated_at: str  # ISO-8601 UTC
    repo_path: str
    config_snapshot: dict[str, object]
    total_pages: int
    completed_pages: int
    failed_pages: int
    completed_page_ids: list[str]
    failed_page_ids: list[str]
    error_message: str | None
    provider_name: str
    model_name: str
    current_level: int

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Checkpoint:
        """Reconstruct a Checkpoint from a JSON-decoded dict."""
        return cls(
            job_id=d["job_id"],
            status=d["status"],
            created_at=d["created_at"],
            updated_at=d["updated_at"],
            repo_path=d["repo_path"],
            config_snapshot=d.get("config_snapshot", {}),
            total_pages=d.get("total_pages", 0),
            completed_pages=d.get("completed_pages", 0),
            failed_pages=d.get("failed_pages", 0),
            completed_page_ids=d.get("completed_page_ids", []),
            failed_page_ids=d.get("failed_page_ids", []),
            error_message=d.get("error_message"),
            provider_name=d.get("provider_name", ""),
            model_name=d.get("model_name", ""),
            current_level=d.get("current_level", 0),
        )


@dataclass
class _LiveJob:
    """A job's in-memory state between flushes.

    ``completed`` indexes ``checkpoint.completed_page_ids`` for membership.
    The list stays the on-disk shape and keeps completion order; scanning it
    per page was quadratic in the page count on its own, separately from the
    I/O.
    """

    checkpoint: Checkpoint
    completed: set[str]
    unflushed: int = 0


# ---------------------------------------------------------------------------
# JobSystem
# ---------------------------------------------------------------------------


class JobSystem:
    """Manage generation job checkpoints via JSON files on disk.

    Args:
        jobs_dir: Directory where {job_id}.json checkpoint files are stored.
                  Created automatically if it does not exist.
    """

    def __init__(self, jobs_dir: Path) -> None:
        self._jobs_dir = jobs_dir
        jobs_dir.mkdir(parents=True, exist_ok=True)
        self._live: dict[str, _LiveJob] = {}

    # ------------------------------------------------------------------
    # Job lifecycle
    # ------------------------------------------------------------------

    def create_job(
        self,
        repo_path: str,
        config: Any,  # GenerationConfig
        provider_name: str,
        model_name: str,
    ) -> str:
        """Create a new job and return its UUID."""
        job_id = str(uuid.uuid4())
        # Serialize config to dict (works for frozen dataclasses)
        try:
            serializer = getattr(config, "to_dict", None)
            config_dict: dict[str, object] = (
                serializer() if callable(serializer) else dataclasses.asdict(config)
            )
        except TypeError:
            config_dict = {}

        # Normalise to the JSON shapes (tuples become lists) up front. The
        # snapshot used to acquire them by way of the disk round-trip every
        # read did; now that a reader can be served from memory, doing it here
        # is what keeps both answers the same.
        config_dict = json.loads(json.dumps(config_dict))

        now = _now_iso()
        checkpoint = Checkpoint(
            job_id=job_id,
            status="pending",
            created_at=now,
            updated_at=now,
            repo_path=repo_path,
            config_snapshot=config_dict,
            total_pages=0,
            completed_pages=0,
            failed_pages=0,
            completed_page_ids=[],
            failed_page_ids=[],
            error_message=None,
            provider_name=provider_name,
            model_name=model_name,
            current_level=0,
        )
        self._save(checkpoint)
        log.info("Job created", job_id=job_id, repo_path=repo_path)
        return job_id

    def start_job(self, job_id: str, total_pages: int) -> None:
        """Transition job from pending → running and set total_pages."""
        cp = self._transition(job_id, "pending", "running")
        cp.total_pages = total_pages
        self._save(cp)

    def complete_page(self, job_id: str, page_id: str) -> None:
        """Record a successfully generated page.

        Buffered: this is the one per-page write, and it is the field no
        reader consults to decide what to generate. See the module docstring
        for the window this leaves.
        """
        live = self._job(job_id)
        cp = live.checkpoint
        if page_id not in live.completed:
            live.completed.add(page_id)
            cp.completed_page_ids.append(page_id)
            cp.completed_pages = len(cp.completed_page_ids)
            cp.total_pages = max(cp.total_pages, cp.completed_pages)
            live.unflushed += 1
        cp.updated_at = _now_iso()
        if live.unflushed >= _FLUSH_EVERY_PAGES:
            self._flush(live)

    def fail_page(self, job_id: str, page_id: str, error: str) -> None:
        """Record a failed page (job stays running).

        Flushed immediately, unlike a completion: failures are rare, and the
        CLI reads this field back off disk to report them after the run.
        """
        cp = self._load(job_id)
        if page_id not in cp.failed_page_ids:
            cp.failed_page_ids.append(page_id)
            cp.failed_pages = len(cp.failed_page_ids)
        cp.updated_at = _now_iso()
        self._save(cp)
        log.warning("Page failed", job_id=job_id, page_id=page_id, error=error)

    def complete_job(self, job_id: str) -> None:
        """Transition job from running → completed."""
        cp = self._transition(job_id, "running", "completed")
        self._save(cp)

    def fail_job(self, job_id: str, error_message: str) -> None:
        """Transition job from running → failed."""
        cp = self._transition(job_id, "running", "failed")
        cp.error_message = error_message
        self._save(cp)

    def pause_job(self, job_id: str) -> None:
        """Transition job from running → paused."""
        cp = self._transition(job_id, "running", "paused")
        self._save(cp)

    def resume_job(self, job_id: str) -> Checkpoint:
        """Transition job from paused → running and return the checkpoint."""
        cp = self._transition(job_id, "paused", "running")
        self._save(cp)
        return cp

    def update_level(self, job_id: str, level: int) -> None:
        """Update the current generation level."""
        cp = self._load(job_id)
        cp.current_level = level
        cp.updated_at = _now_iso()
        self._save(cp)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_checkpoint(self, job_id: str) -> Checkpoint:
        """Load and return the checkpoint for *job_id*."""
        return self._load(job_id)

    def get_completed_page_ids(self, job_id: str) -> set[str]:
        """Return the set of already-completed page IDs for *job_id*."""
        return set(self._load(job_id).completed_page_ids)

    def list_jobs(self) -> list[Checkpoint]:
        """Return all jobs sorted by created_at descending.

        Flushes first: this reads the directory rather than the live state, so
        without it the listing would report the one job this instance is
        writing as behind by up to a flush bound.
        """
        for job_id in list(self._live):
            self.flush(job_id)
        checkpoints: list[Checkpoint] = []
        for json_path in self._jobs_dir.glob("*.json"):
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                checkpoints.append(Checkpoint.from_dict(data))
            except Exception as exc:
                log.warning("Failed to load checkpoint", path=str(json_path), error=str(exc))
        checkpoints.sort(key=lambda c: c.created_at, reverse=True)
        return checkpoints

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _job(self, job_id: str) -> _LiveJob:
        """This instance's live state for *job_id*, read from disk once.

        A second JobSystem over the same directory (the CLI opens one to read
        the failure report back) still sees a current file, because every
        write this one buffers is flushed by the time a run ends.
        """
        live = self._live.get(job_id)
        if live is None:
            checkpoint = self._read(job_id)
            live = _LiveJob(checkpoint, set(checkpoint.completed_page_ids))
            self._live[job_id] = live
        return live

    def _read(self, job_id: str) -> Checkpoint:
        path = self._jobs_dir / f"{job_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Job checkpoint not found: {job_id}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return Checkpoint.from_dict(data)

    def _load(self, job_id: str) -> Checkpoint:
        """The current checkpoint, buffered writes included."""
        return self._job(job_id).checkpoint

    def _flush(self, live: _LiveJob) -> None:
        checkpoint = live.checkpoint
        path = self._jobs_dir / f"{checkpoint.job_id}.json"
        atomic_write_text(
            path,
            json.dumps(dataclasses.asdict(checkpoint), indent=2),
        )
        live.unflushed = 0

    def _save(self, checkpoint: Checkpoint) -> None:
        """Stamp and flush *checkpoint* now. For everything but page counts."""
        checkpoint.updated_at = _now_iso()
        live = self._live.get(checkpoint.job_id)
        if live is None:
            live = _LiveJob(checkpoint, set(checkpoint.completed_page_ids))
            self._live[checkpoint.job_id] = live
        self._flush(live)

    def flush(self, job_id: str) -> None:
        """Make this job's buffered page completions durable.

        Called at every level boundary and on interruption, so the window a
        kill can lose is one level rather than a whole run.
        """
        live = self._live.get(job_id)
        if live is not None and live.unflushed:
            self._flush(live)

    def _transition(
        self,
        job_id: str,
        expected_status: str,
        new_status: str,
    ) -> Checkpoint:
        """Load checkpoint, validate current status, apply transition.

        Raises:
            ValueError: If the current status does not match *expected_status*.
        """
        cp = self._load(job_id)
        if cp.status != expected_status:
            raise ValueError(
                f"Job {job_id}: expected status '{expected_status}', "
                f"got '{cp.status}' (cannot transition to '{new_status}')"
            )
        cp.status = new_status
        return cp
