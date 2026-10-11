"""Shared CI plumbing: workflow commands, markdown pieces, the target branch,
SARIF and GitLab Code Quality reports, baselines.

Every CI-facing feature (patch coverage, doc drift, ...) renders its own
content and uses these for the parts CI systems define, so escaping, caps and
base-branch rules are written once and a hosted check can reuse them.
"""

from __future__ import annotations
