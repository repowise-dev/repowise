"""Agent integration targets — one descriptor per agent repowise can wire up.

The seam that makes adding an agent cost a descriptor file and a registry line.
See :mod:`.types` for the shape a descriptor implements and why the tier is
derived rather than declared, and :mod:`.registry` for how they are looked up.

Import discipline: everything here is lazy at module scope. ``agent_targets`` is
reached from ``init`` and ``doctor``, which already pay for click and rich, but
it must never be reached from a hook — ``cli.agent_adapters`` sits on the
PreToolUse hot path and imports stdlib only. The dependency runs one way:
``agent_targets`` may name ``agent_adapters``, never the reverse.
"""

from .types import (
    AgentTarget,
    Capability,
    DoctorReport,
    DoctorStatus,
    FileAction,
    InstallMethod,
    Registration,
    Scope,
    Tier,
    WriteResult,
    capabilities_of,
    derive_tier,
)

__all__ = [
    "AgentTarget",
    "Capability",
    "DoctorReport",
    "DoctorStatus",
    "FileAction",
    "InstallMethod",
    "Registration",
    "Scope",
    "Tier",
    "WriteResult",
    "capabilities_of",
    "derive_tier",
]
