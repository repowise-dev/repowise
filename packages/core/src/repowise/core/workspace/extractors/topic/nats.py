"""NATS publishers and subscribers.

The receiver must carry a NATS-idiomatic name, so an RxJS or EventEmitter
``subscribe``/``publish`` is not read as a subject. A subscription subject
with ``*`` or ``>`` is a pattern the matcher expands.
"""

from __future__ import annotations

import re

from repowise.core.workspace.contracts import TOPIC_KIND_SUBJECT, TOPIC_PATTERN_NATS

from ..langs import GO, JAVA, JS_TS, PYTHON
from ..strings import Arg
from .dialect import TopicCall, TopicDialect

_RECEIVER = r"(?:nc|nats|conn|js|client)\s*\.\s*"
_LANGUAGES = GO | JAVA | JS_TS | PYTHON

NATS = TopicDialect(
    name="nats",
    calls=(
        TopicCall(
            head=re.compile(_RECEIVER + r"(?:Subscribe|subscribe)\s*\("),
            role="consumer",
            label="nc.Subscribe",
            extensions=_LANGUAGES,
            name=Arg(pos=0),
            kind=TOPIC_KIND_SUBJECT,
            pattern=TOPIC_PATTERN_NATS,
        ),
        TopicCall(
            head=re.compile(_RECEIVER + r"(?:Publish|publish)\s*\("),
            role="provider",
            label="nc.Publish",
            extensions=_LANGUAGES,
            name=Arg(pos=0),
            kind=TOPIC_KIND_SUBJECT,
        ),
    ),
)

__all__ = ["NATS"]
