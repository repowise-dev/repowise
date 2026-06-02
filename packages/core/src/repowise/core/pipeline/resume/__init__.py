"""Crash-resume for the indexing pipeline.

Makes ``repowise init --resume`` honor the "safe to Ctrl-C" promise: a run
interrupted partway through skips the phases a prior run already finished and
rehydrates their outputs from the database instead of recomputing them.

Modules
-------
``phases``      Phase identifiers + ordering (the resume vocabulary).
``ledger``      Durable "which phases completed" record over the SqlJobStore.
``rehydrate``   Reconstruct phase *inputs* (graph, git metadata) from the DB.
``controller``  The single object the orchestrator consults at phase
                boundaries — decides skip-vs-run, persists on completion,
                and serves rehydrated inputs. A ``None`` controller means
                "no resume" and the pipeline behaves exactly as before.
"""

from .controller import ResumeController
from .ledger import ResumeLedger
from .phases import ResumePhase
from .rehydrate import rehydrate_git_meta_map

__all__ = [
    "ResumeController",
    "ResumeLedger",
    "ResumePhase",
    "rehydrate_git_meta_map",
]
