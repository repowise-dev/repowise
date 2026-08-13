"""Supported output languages for generated wiki content.

Kept in a dependency-free leaf module so the CLI can import the map at
startup (for ``--language`` validation and the interactive init prompt)
without pulling the heavy page-generator stack.
"""

from __future__ import annotations

# code → English name. The page generator validates the configured code
# against this map and falls back to English on an unknown code.
SUPPORTED_LANGUAGES = {
    "en": "English",
    "ru": "Russian",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "it": "Italian",
    "pt": "Portuguese",
    "nl": "Dutch",
    "pl": "Polish",
    "tr": "Turkish",
    "ar": "Arabic",
    "hi": "Hindi",
}


def sanitize_language_code(language: str | None) -> str:
    """Return *language* lowered, stripped and reduced to ``[a-z0-9_]``.

    The configured code reaches a system prompt and a label lookup, so it is
    scrubbed before either sees it: without this a config value could inject
    newlines and extra instructions into the prompt. Validation against
    :data:`SUPPORTED_LANGUAGES` is the caller's, because the two callers
    differ on what an unknown code means: the prompt path warns, the label
    path silently falls back to English.
    """
    raw = (language or "en").lower().strip()
    return "".join(ch for ch in raw if ch.isalnum() or ch == "_")
