"""Job system for the repowise generation engine.

JobSystem manages long-running generation jobs via JSON checkpoint files.
Each job maps to a single {job_id}.json file in the configured jobs_dir.
The checkpoint records progress (completed/failed pages), current level, and
job status so generation can be resumed after interruption.

Phase 4 will replace this with a full SQLAlchemy-backed job table.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import structlog

log = structlog.get_logger(__name__)

JobStatus = Literal["pending", "running", "completed", "failed", "paused"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    def from_dict(cls, d: dict[str, Any]) -> "Checkpoint":
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
            config_dict: dict[str, object] = dataclasses.asdict(config)
        except TypeError:
            config_dict = {}

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
        """Record a successfully generated page."""
        cp = self._load(job_id)
        if page_id not in cp.completed_page_ids:
            cp.completed_page_ids.append(page_id)
            cp.completed_pages = len(cp.completed_page_ids)
            cp.total_pages = max(cp.total_pages, cp.completed_pages)
        cp.updated_at = _now_iso()
        self._save(cp)

    def fail_page(self, job_id: str, page_id: str, error: str) -> None:
        """Record a failed page (job stays running)."""
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
        """Return all jobs sorted by created_at descending."""
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

    def _load(self, job_id: str) -> Checkpoint:
        path = self._jobs_dir / f"{job_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Job checkpoint not found: {job_id}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return Checkpoint.from_dict(data)

    def _save(self, checkpoint: Checkpoint) -> None:
        checkpoint.updated_at = _now_iso()
        path = self._jobs_dir / f"{checkpoint.job_id}.json"
        path.write_text(
            json.dumps(dataclasses.asdict(checkpoint), indent=2),
            encoding="utf-8",
        )

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
