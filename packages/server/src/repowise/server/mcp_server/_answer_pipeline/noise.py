"""Noise demotion: decision records and test file pages sink below real hits."""

from __future__ import annotations

from collections.abc import Mapping

from repowise.server.mcp_server._origin_rank import TEST_QUERY_RE as _TEST_QUERY_RE
from repowise.server.mcp_server._origin_rank import (
    asked_languages,
    demoted_origins,
    is_demoted_row,
    is_off_language_row,
    question_language,
)


def demote_noise_hits(
    hits: list[dict],
    question: str,
    *,
    is_why: bool,
    origins: Mapping[str, str] | None = None,
    languages: Mapping[str, str] | None = None,
) -> list[dict]:
    """Stable-partition noise below real hits before the top-5 cap; nothing is dropped.

    Decision records are noise unless the question is why-shaped; test,
    generated and vendored files (by ``code_origin``, read from *origins* when
    stored, so mocks and fixtures count as tests) unless the question asks for
    that kind of file. A file that defines a symbol the question names is not
    noise: symbol anchoring placed it, and the question asks about it.

    Between real hits and noise sit files in another language than the
    question's (by stored language in *languages*, see
    :func:`question_language`): a ``.proto`` or shell script under a C++ or
    Go question, unless the question asks for that language or file type.
    Code in another language sinks only under a question naming a symbol.
    """
    if not hits:
        return hits
    demoted = demoted_origins(question)

    def _is_noise(h: dict) -> bool:
        pt = h.get("page_type")
        return (pt == "decision_record" and not is_why) or (
            not h.get("_symbol_anchored") and is_demoted_row(h, demoted, origins)
        )

    language, named = question_language(hits, languages) if languages else (None, False)
    asked = asked_languages(question) if language else frozenset()

    def _is_off(h: dict) -> bool:
        return not h.get("_symbol_anchored") and is_off_language_row(
            h, language, asked, languages or {}, named=named
        )

    noise = [h for h in hits if _is_noise(h)]
    rest = [h for h in hits if not _is_noise(h)]
    real = [h for h in rest if not _is_off(h)]
    off = [h for h in rest if _is_off(h)]
    return real + off + noise


__all__ = ["_TEST_QUERY_RE", "demote_noise_hits"]
