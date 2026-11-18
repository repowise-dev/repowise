"""Profile format registry: the profiler outputs ``repowise evidence add`` converts.

:data:`profile_formats` is the one list of profile formats: detection, the
CLI ``--input-format`` choice and user-facing format lists all read it, the
way the coverage ``PARSERS`` list works for coverage reports. A format is a
:class:`ProfileFormat`: a ``sniff`` over the file's first bytes and a
``read`` that turns the whole file into a :class:`ProfileRun` (frames and
weighted stacks, nothing joined). Joining frames to code and writing the
``repowise-evidence-v1`` document is shared (:mod:`..profile_convert`), so a
new format is one module plus one ``register`` call below. A plugin adds
one the same way from its ``register()``::

    from repowise.core.evidence.profile_formats import ProfileFormat, profile_formats

    profile_formats.register("myprof", ProfileFormat(sniff=my_sniff, read=my_read))

Sniffing reads content, never the file name, and takes the first format
that claims the file, built-in formats first. Readers take bytes, so a
binary format (a gzipped protobuf) needs no text decoding first.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from repowise.core.registry.plugins import SeamRegistry

from . import collapsed, pprof, speedscope
from .model import SNIFF_BYTES, ProfileRun, Stack, StackTally, frame_language


@dataclass(frozen=True)
class ProfileFormat:
    #: Whether a file starting with these bytes (BOM included) is this format.
    sniff: Callable[[bytes], bool]
    #: The whole file as a run; raises ``EvidenceFormatError`` when it cannot be read.
    read: Callable[[bytes], ProfileRun]


#: Format name -> format, in the order formats are sniffed and listed.
profile_formats: SeamRegistry[ProfileFormat] = SeamRegistry("profile-format")
profile_formats.register("speedscope", ProfileFormat(speedscope.sniff, speedscope.read))
profile_formats.register("pprof", ProfileFormat(pprof.sniff, pprof.read))
profile_formats.register("collapsed", ProfileFormat(collapsed.sniff, collapsed.read))


def detect_profile_format(head: bytes) -> str | None:
    """The name of the first registered format that claims *head*, else None."""
    sample = head[:SNIFF_BYTES]
    return next((name for name, fmt in profile_formats.all() if fmt.sniff(sample)), None)


__all__ = [
    "SNIFF_BYTES",
    "ProfileFormat",
    "ProfileRun",
    "Stack",
    "StackTally",
    "detect_profile_format",
    "frame_language",
    "profile_formats",
]
