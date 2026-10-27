"""A source-map hit and a disk read must produce the same string.

Every scan that moved onto the source map keeps a disk fallback, so the two
paths run against the same repo in the same build. If they decode differently
the scan sees different text for the same file depending on whether the
pipeline happened to fill the map — which shows up as edges appearing and
disappearing, not as an error.

The failure this pins is not hypothetical: ``Path.read_text`` opens in text
mode and translates newlines, ``bytes.decode`` does not, and CRLF is the
majority line ending in a Windows-authored C# or Swift checkout.
"""

from __future__ import annotations

import pytest

from repowise.core.ingestion.source_text import decode_source, source_text

CASES = [
    ("lf", b"class A {}\nclass B {}\n"),
    ("crlf", b"class A {}\r\nclass B {}\r\n"),
    ("cr", b"class A {}\rclass B {}\r"),
    ("mixed", b"a\r\nb\rc\n"),
    ("bom_lf", b"\xef\xbb\xbfclass A {}\n"),
    ("bom_crlf", b"\xef\xbb\xbfclass A {}\r\n"),
    ("non_ascii", "// café ☕\r\nclass A {}\r\n".encode()),
    ("invalid_utf8", b"class A {}\r\n\xff\xfe\r\n"),
    ("empty", b""),
]


@pytest.mark.parametrize("label,data", CASES, ids=[c[0] for c in CASES])
@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig"])
@pytest.mark.parametrize("errors", ["ignore", "replace"])
def test_map_hit_matches_disk_read(tmp_path, label, data, encoding, errors):
    f = tmp_path / "Sample.cs"
    f.write_bytes(data)

    from_map = source_text(
        "Sample.cs", f, {"Sample.cs": data}, encoding=encoding, errors=errors
    )
    from_disk = source_text("Sample.cs", f, None, encoding=encoding, errors=errors)

    assert from_map == from_disk
    assert from_map == f.read_text(encoding=encoding, errors=errors)


def test_a_miss_falls_back_to_disk():
    """An empty or partial map must not read as an empty file."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "Other.cs"
        f.write_bytes(b"class Other {}\r\n")
        assert source_text("Other.cs", f, {}) == "class Other {}\n"
        assert source_text("Other.cs", f, {"Unrelated.cs": b"x"}) == "class Other {}\n"


def test_unreadable_file_returns_none(tmp_path):
    assert source_text("gone.cs", tmp_path / "gone.cs", None) is None
    assert source_text("gone.cs", tmp_path / "gone.cs", {}) is None


def test_only_utf_8_sig_strips_the_bom():
    """The codecs differ by caller, and only C# asks for the BOM to go."""
    data = b"\xef\xbb\xbfclass A {}\n"
    assert decode_source(data, encoding="utf-8").startswith("﻿")
    assert decode_source(data, encoding="utf-8-sig").startswith("class")
