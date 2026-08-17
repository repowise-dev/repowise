"""One reader for file text the ingestion pipeline has already read.

The pipeline reads every indexed file, hands the bytes to
``GraphBuilder.set_source_map``, and they reach the resolvers as
``ResolverContext.source_map``. The scans that run during ``build()`` used to
re-open the repo anyway. This is the single place that prefers those bytes.

Two things a caller must not get wrong, which is why they live here rather than
at each site:

* **The codec is the caller's, not a default.** JVM and Swift scans decode
  utf-8, C# decodes utf-8-sig — only C# strips a BOM — and a couple of the
  .NET readers pass ``errors="replace"`` where the rest pass ``"ignore"``.
  Decoding a caller's bytes with another caller's codec silently changes what
  its regexes match, and therefore which edges it emits.
* **``Path.read_text`` translates newlines and ``bytes.decode`` does not.**
  Text mode maps ``\\r\\n`` and lone ``\\r`` to ``\\n``; the raw bytes in the
  source map still carry the ``\\r``. Every CRLF file — which on Windows is
  most of a C# checkout — would decode to different text than the disk read it
  replaces. :func:`decode_source` normalises, so a map hit and a disk fallback
  produce the same string for the same file.

The fallback is not optional. ``pipeline/incremental.py`` fills the map only
when its caller asks for sources, so it can be partial or absent entirely.
"""

from __future__ import annotations

from pathlib import Path


def decode_source(data: bytes, *, encoding: str = "utf-8", errors: str = "ignore") -> str:
    """Decode *data* the way ``Path.read_text(encoding, errors)`` would."""
    text = data.decode(encoding, errors=errors)
    if "\r" in text:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def source_text(
    rel_path: str | None,
    abs_path: str | Path,
    source_map: dict[str, bytes] | None,
    *,
    encoding: str = "utf-8",
    errors: str = "ignore",
) -> str | None:
    """Text of one file, from *source_map* if it holds it, else from disk.

    Returns None when the file is neither in the map nor readable.
    """
    if source_map is not None and rel_path is not None:
        data = source_map.get(rel_path)
        if data is not None:
            return decode_source(data, encoding=encoding, errors=errors)
    try:
        return Path(abs_path).read_text(encoding=encoding, errors=errors)
    except OSError:
        return None
