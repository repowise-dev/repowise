"""Composable config-format helpers targets build on.

Kept deliberately small and free of per-agent knowledge. The targets differ
enough — JSON versus TOML versus marker-delimited markdown, and different
idempotency markers within each — that a shared base class would force an
awkward shape on all of them; composition lets each take only the mechanics it
needs.
"""
