"""Suspects: changes merged before a symptom, in the order to read them.

:mod:`.rank` holds the shapes and the fusion, :mod:`.signals` the local
signals on ``SeamRegistry("suspect-signal")``, :mod:`.git_text` what git adds
to a stored Change Record (message body, touched symbol names).
"""

from __future__ import annotations

from repowise.core.suspects import signals as _signals  # noqa: F401  registers the defaults
from repowise.core.suspects.rank import (
    RRF_K,
    Candidate,
    Hit,
    PreparedSymptom,
    SignalRank,
    Suspect,
    SuspectContext,
    SuspectSignal,
    Symptom,
    rank_suspects,
    suspect_signals,
)

__all__ = [
    "RRF_K",
    "Candidate",
    "Hit",
    "PreparedSymptom",
    "SignalRank",
    "Suspect",
    "SuspectContext",
    "SuspectSignal",
    "Symptom",
    "rank_suspects",
    "suspect_signals",
]
