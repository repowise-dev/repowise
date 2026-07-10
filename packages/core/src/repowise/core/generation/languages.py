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
