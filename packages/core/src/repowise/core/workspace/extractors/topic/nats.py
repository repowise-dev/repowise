"""NATS publishers and subscribers.

The receiver must carry a NATS-idiomatic name, so an RxJS or EventEmitter
``subscribe``/``publish`` is not read as a subject.
"""

from __future__ import annotations

import re

from ..langs import GO, JAVA, JS_TS, PYTHON
from .dialect import KIND_SUBJECT, Arg, TopicCall, TopicDialect

_RECEIVER = r"(?:nc|nats|conn|js|sub|client)\s*\.\s*"
_LANGUAGES = GO | JAVA | JS_TS | PYTHON

NATS = TopicDialect(
    name="nats",
    broker="nats",
    calls=(
        TopicCall(
            head=re.compile(_RECEIVER + r"(?:Subscribe|subscribe)\s*\("),
            role="consumer",
            label="nc.Subscribe",
            extensions=_LANGUAGES,
            name=Arg(pos=0),
            kind=KIND_SUBJECT,
        ),
        TopicCall(
            head=re.compile(_RECEIVER + r"(?:Publish|publish)\s*\("),
            role="provider",
            label="nc.Publish",
            extensions=_LANGUAGES,
            name=Arg(pos=0),
            kind=KIND_SUBJECT,
        ),
    ),
)

__all__ = ["NATS"]
