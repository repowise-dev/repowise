"""Shared platform primitives usable from both the CLI and the server layers.

Lives in ``repowise-core`` (the lowest layer) so ``repowise.server`` can emit
anonymous telemetry without importing ``repowise.cli`` (which it must never do).
"""
