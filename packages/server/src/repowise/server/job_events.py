"""In-memory per-job progress event buffers.

The pipeline's ``on_message`` / ``on_phase_start`` callbacks carry the human
narrative of a run ("Parsing files", "3 files skipped: encoding error") that
the job row's page counters cannot express. Each running job gets a bounded
ring buffer here; the SSE stream drains it incrementally and forwards every
entry to the client alongside the polled progress numbers.

Deliberately in-memory: jobs execute in this same process, the stream is a
live view, and a bounded deque avoids both a schema migration and per-message
database writes that would contend with the pipeline's bulk transactions. A
server restart loses the transcript but also fails the job, so nothing
consumers rely on outlives the buffer.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

# Ring size per job: enough for the full narrative of a large run's phase
# starts + warnings without letting a pathological logger grow unbounded.
_MAX_EVENTS_PER_JOB = 500

# Buffers kept after job completion so late SSE subscribers still see the
# tail; evicted oldest-first beyond this many jobs.
_MAX_JOBS = 50


@dataclass
class JobEventBuffer:
    """Bounded event log + current phase label for one job."""

    events: deque = field(default_factory=lambda: deque(maxlen=_MAX_EVENTS_PER_JOB))
    next_seq: int = 0
    phase: str = ""

    def add(self, level: str, text: str) -> None:
        self.events.append(
            {
                "seq": self.next_seq,
                "ts": time.time(),
                "level": level,
                "text": text,
                "phase": self.phase,
            }
        )
        self.next_seq += 1

    def set_phase(self, phase: str, total: int | None) -> None:
        self.phase = phase
        self.add("info", f"Phase started: {phase}" + (f" ({total} items)" if total else ""))

    def since(self, seq: int) -> list[dict]:
        """Return events with ``seq`` >= *seq* (drained ones are simply gone)."""
        return [e for e in self.events if e["seq"] >= seq]


def get_event_buffers(app_state) -> dict[str, JobEventBuffer]:
    buffers = getattr(app_state, "job_events", None)
    if buffers is None:
        buffers = {}
        app_state.job_events = buffers
    return buffers


def create_event_buffer(app_state, job_id: str) -> JobEventBuffer:
    """Create (or return) the buffer for *job_id*, evicting the oldest jobs."""
    buffers = get_event_buffers(app_state)
    if job_id not in buffers:
        while len(buffers) >= _MAX_JOBS:
            buffers.pop(next(iter(buffers)))
        buffers[job_id] = JobEventBuffer()
    return buffers[job_id]
