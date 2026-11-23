"""Blank credential shapes in text that may be shown, logged or stored.

Provider and forge errors quote what they were sent: auth errors echo the key
("Incorrect API key provided: sk-abc..."), an HTTP error carries its URL (a
Google ``?key=`` query) and a git error its remote with credentials.
"""

from __future__ import annotations

import re

_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),  # OpenAI / Anthropic
    re.compile(r"\bAIza[A-Za-z0-9_-]{10,}"),  # Google
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{10,}"),  # GitHub
    re.compile(r"\bgl(?:pat|dt|rt|ptt|oas|imt|cbt)-[\w.-]{10,}"),  # GitLab
    re.compile(r"\bGR1348941[\w-]{10,}"),  # GitLab runner registration
    re.compile(r"\b[0-9A-Za-z]{76}AZDO[0-9A-Za-z]{4}\b"),  # Azure DevOps
)
# A key in a URL query (Google's ``?key=``) or an Authorization header value.
_QUERY_KEY = re.compile(r"([?&](?:api[_-]?)?key=)[^&\s'\"]+", re.IGNORECASE)
_BEARER = re.compile(r"(\bBearer\s+)[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)
# A git remote quoted in a clone or fetch error carries its credentials.
_URL_USERINFO = re.compile(r"://[^\s/@:]+:[^\s/@]+@")


def redact_secrets(text: str) -> str:
    """Blank anything key-shaped in *text*.

    Deliberately conservative: it matches issuer-prefixed shapes rather than
    "any long token", because over-matching would eat the file paths and hashes
    that make these messages useful. A key in an unrecognised format still gets
    through, so this reduces exposure rather than guaranteeing its absence.
    """
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[redacted]", text)
    text = _QUERY_KEY.sub(r"\1[redacted]", text)
    text = _BEARER.sub(r"\1[redacted]", text)
    return _URL_USERINFO.sub("://***@", text)
