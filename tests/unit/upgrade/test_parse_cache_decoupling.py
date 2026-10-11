"""The parse-cache fingerprint must be decoupled from the package version.

An ordinary release bumps ``repowise.core.__version__`` but ships no parser
change; that must NOT invalidate a user's parse cache. A real parser change
bumps ``PARSER_SCHEMA_VERSION``, which MUST invalidate it.
"""

from __future__ import annotations

import repowise.core.ingestion.parse_cache as pc
import repowise.core.upgrade.version as ver


def _fresh_fingerprint() -> str:
    pc.parser_fingerprint.cache_clear()
    return pc.parser_fingerprint()


def test_package_version_change_keeps_cache_warm(monkeypatch):
    before = _fresh_fingerprint()
    monkeypatch.setattr("repowise.core.__version__", "999.0.0", raising=False)
    after = _fresh_fingerprint()
    assert before == after, "package version must not affect the parse fingerprint"


def test_parser_schema_version_change_invalidates_cache(monkeypatch):
    before = _fresh_fingerprint()
    monkeypatch.setattr(ver, "PARSER_SCHEMA_VERSION", ver.PARSER_SCHEMA_VERSION + 1)
    after = _fresh_fingerprint()
    assert before != after, "parser schema bump must change the fingerprint"


def test_fingerprint_is_stable_across_calls():
    pc.parser_fingerprint.cache_clear()
    assert pc.parser_fingerprint() == pc.parser_fingerprint()
