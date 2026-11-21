"""Input format registry: the tool outputs ``repowise evidence add`` converts.

:data:`profile_formats` is the one list of input formats, profiles and
benchmark results and test reports alike: detection, the CLI ``--input-format`` choice and
user-facing format lists all read it, the way the coverage ``PARSERS`` list
works for coverage reports. A format is a :class:`ProfileFormat`: a
``sniff`` over the file's first bytes and one reader. A profile's ``read``
turns the whole file into a :class:`ProfileRun` (frames and weighted
stacks, nothing joined); joining frames to code and writing the
``repowise-evidence-v1`` document is shared (:mod:`..profile_convert`). Any
other kind (benchmark results, test reports) has ``to_v1`` instead, which writes the v1
document directly: its entries name their functions, and ingest joins
them. A format whose tool names a package rather than a file (Go) sets
``needs_checkout`` and gets the checkout the run used as a second argument;
every other ``to_v1`` takes the bytes alone. A new format is one module plus one
``register`` call below. A plugin adds one the same way from its
``register()``::

    from repowise.core.evidence.profile_formats import ProfileFormat, profile_formats

    profile_formats.register("myprof", ProfileFormat(sniff=my_sniff, read=my_read))

Sniffing reads content, never the file name, and takes the first format
that claims the file, built-in formats first. Readers take bytes, so a
binary format (a gzipped protobuf) needs no text decoding first.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from repowise.core.registry.plugins import SeamRegistry

from . import collapsed, cpuprofile, go_bench, hyperfine, junit, pprof, pytest_benchmark, speedscope
from .model import SNIFF_BYTES, ProfileRun, Stack, StackTally, frame_language


@dataclass(frozen=True)
class ProfileFormat:
    """A format's sniffer and exactly one of its two readers.

    Both readers raise ``EvidenceFormatError`` when the file cannot be read.
    """

    #: Whether a file starting with these bytes (BOM included) is this format.
    sniff: Callable[[bytes], bool]
    #: A profile: the whole file as a run, converted by :mod:`..profile_convert`.
    read: Callable[[bytes], ProfileRun] | None = None
    #: Any other kind: the whole file as a ``repowise-evidence-v1`` object.
    #: Called ``to_v1(data)``, or ``to_v1(data, checkout_root)`` with *needs_checkout*.
    to_v1: Callable[..., dict[str, Any]] | None = None
    #: ``to_v1`` also reads the checkout the run used (Go output names packages, not files).
    needs_checkout: bool = False

    def __post_init__(self) -> None:
        if (self.read is None) == (self.to_v1 is None):
            raise ValueError("a format has exactly one of read and to_v1")


#: Format name -> format, in the order formats are sniffed and listed.
profile_formats: SeamRegistry[ProfileFormat] = SeamRegistry("profile-format")
profile_formats.register("speedscope", ProfileFormat(speedscope.sniff, speedscope.read))
profile_formats.register("pprof", ProfileFormat(pprof.sniff, pprof.read))
profile_formats.register("cpuprofile", ProfileFormat(cpuprofile.sniff, cpuprofile.read))
profile_formats.register(
    "pytest-benchmark",
    ProfileFormat(pytest_benchmark.sniff, to_v1=pytest_benchmark.to_v1),
)
profile_formats.register(
    "go-bench", ProfileFormat(go_bench.sniff, to_v1=go_bench.to_v1, needs_checkout=True)
)
profile_formats.register("hyperfine", ProfileFormat(hyperfine.sniff, to_v1=hyperfine.to_v1))
profile_formats.register("junit", ProfileFormat(junit.sniff, to_v1=junit.to_v1))
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
