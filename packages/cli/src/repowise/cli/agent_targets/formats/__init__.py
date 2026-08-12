"""Composable config-format helpers targets build on.

Kept deliberately small and free of per-agent knowledge. The targets differ
enough (JSON versus TOML versus YAML versus marker-delimited markdown, and
different idempotency markers within each) that a shared base class would
force an awkward shape on all of them; composition lets each take only the
mechanics it needs.

The two structured-config writers here, :mod:`toml_merge` and
:mod:`yaml_merge`, are narrow on purpose and in the same way: each edits the
source text in place rather than parsing and reserializing, so the user's
comments, key order and formatting survive everywhere outside the one key
repowise owns. Neither is a general serializer and neither should become one.
"""
