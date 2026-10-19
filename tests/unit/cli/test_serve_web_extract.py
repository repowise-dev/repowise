"""The downloaded web UI archive must never write outside the cache directory."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from repowise.cli.commands import serve_cmd
from repowise.cli.commands.serve_cmd import _web_assets


def _make_tar(path: Path, members: list[tuple[str, str | None, bytes]]) -> Path:
    """Write a .tar.gz of (name, symlink_target_or_None, data) entries."""
    with tarfile.open(path, "w:gz") as tar:
        for name, link, data in members:
            info = tarfile.TarInfo(name)
            if link is not None:
                # "hard:" prefix marks a hardlink; otherwise a symlink.
                if link.startswith("hard:"):
                    info.type, info.linkname = tarfile.LNKTYPE, link[5:]
                else:
                    info.type, info.linkname = tarfile.SYMTYPE, link
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return path


def _dest(tmp_path: Path) -> Path:
    dest = tmp_path / "cache" / "web"
    dest.mkdir(parents=True)
    return dest


def test_legit_archive_extracts_as_before(tmp_path: Path) -> None:
    archive = _make_tar(
        tmp_path / "web.tar.gz",
        [
            ("server.js", None, b"console.log(1)"),
            (".next/static/app.js", None, b"x"),
            ("node_modules/.bin/next", "../next/dist/bin/next", b""),
            ("node_modules/next/dist/bin/next", None, b"#!/usr/bin/env node"),
        ],
    )
    dest = _dest(tmp_path)
    serve_cmd._safe_extract_tar(archive, dest)
    assert (dest / "server.js").read_bytes() == b"console.log(1)"
    assert (dest / ".next" / "static" / "app.js").read_bytes() == b"x"
    assert (dest / "node_modules" / "next" / "dist" / "bin" / "next").exists()


@pytest.mark.parametrize(
    ("member", "link", "message"),
    [
        ("../evil.txt", None, "escapes the destination"),
        ("sub/../../evil.txt", None, "escapes the destination"),
        ("/evil.txt", None, "absolute or drive-qualified"),
        ("link", "../../evil_dir", "link escapes"),
        ("link", "/etc", "absolute or drive-qualified"),
        ("C:evil.txt", None, "drive-qualified"),
        ("a:stream", None, "drive-qualified"),
        ("//server/share/evil.txt", None, "drive-qualified"),
        ("link", "C:evil", "drive-qualified"),
        ("hl", "hard:../evil.txt", "link escapes"),
    ],
)
def test_escaping_member_is_rejected(
    tmp_path: Path, member: str, link: str | None, message: str
) -> None:
    archive = _make_tar(
        tmp_path / "web.tar.gz",
        [("server.js", None, b"ok"), (member, link, b"pwned")],
    )
    dest = _dest(tmp_path)
    with pytest.raises(ValueError, match=message):
        serve_cmd._safe_extract_tar(archive, dest)
    # Validation runs before any write: nothing lands anywhere.
    assert list(dest.iterdir()) == []
    assert not (tmp_path / "cache" / "evil.txt").exists()
    assert not (tmp_path / "evil.txt").exists()


def test_download_web_reports_failure_on_tampered_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _make_tar(tmp_path / "web.tar.gz", [("../evil.txt", None, b"pwned")])
    payload = archive.read_bytes()

    class _Resp:
        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self, chunk_size: int) -> list[bytes]:
            return [payload]

    import httpx

    monkeypatch.setattr(httpx, "stream", lambda *_a, **_k: _Resp())
    dest = _dest(tmp_path)
    monkeypatch.setattr(_web_assets, "_WEB_CACHE_DIR", dest)
    monkeypatch.setattr(_web_assets, "_MARKER_FILE", dest / ".version")

    assert serve_cmd._download_web("0.0.0") is False
    assert not (dest / ".version").exists()
    assert not (tmp_path / "cache" / "evil.txt").exists()


def _assert_nothing_written(tmp_path: Path, dest: Path) -> None:
    assert list(dest.iterdir()) == []
    assert not (tmp_path / "cache" / "b").exists()
    assert not (tmp_path / "cache" / "evil").exists()


def test_symlink_chain_is_rejected(tmp_path: Path) -> None:
    archive = _make_tar(tmp_path / "web.tar.gz", [("a", ".", b""), ("a/b", "..", b"")])
    dest = _dest(tmp_path)
    with pytest.raises(ValueError, match="passes through a link"):
        serve_cmd._safe_extract_tar(archive, dest)
    _assert_nothing_written(tmp_path, dest)


def test_hardlink_through_symlink_is_rejected(tmp_path: Path) -> None:
    archive = _make_tar(tmp_path / "web.tar.gz", [("a", ".", b""), ("h", "hard:a", b"")])
    dest = _dest(tmp_path)
    with pytest.raises(ValueError, match="passes through a link"):
        serve_cmd._safe_extract_tar(archive, dest)
    _assert_nothing_written(tmp_path, dest)


@pytest.mark.parametrize("kind", [tarfile.FIFOTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE])
def test_special_file_member_is_rejected(tmp_path: Path, kind: bytes) -> None:
    archive = tmp_path / "web.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("dev")
        info.type = kind
        tar.addfile(info)
    dest = _dest(tmp_path)
    with pytest.raises(ValueError, match="not a file or directory"):
        serve_cmd._safe_extract_tar(archive, dest)
    _assert_nothing_written(tmp_path, dest)


@pytest.mark.parametrize(
    "members",
    [
        [("a", "..", b""), ("a/evil", None, b"pwned")],
        [("../evil", None, b"pwned")],
        [("a", ".", b""), ("a/b", "..", b"")],
    ],
)
def test_fallback_without_data_filter_rejects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, members: list[tuple[str, str | None, bytes]]
) -> None:
    monkeypatch.delattr(tarfile, "data_filter", raising=False)
    archive = _make_tar(tmp_path / "web.tar.gz", members)
    dest = _dest(tmp_path)
    with pytest.raises(ValueError):
        serve_cmd._safe_extract_tar(archive, dest)
    _assert_nothing_written(tmp_path, dest)


def test_fallback_without_data_filter_extracts_legit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delattr(tarfile, "data_filter", raising=False)
    archive = _make_tar(
        tmp_path / "web.tar.gz",
        [("server.js", None, b"ok"), (".next/static/app.js", None, b"x")],
    )
    dest = _dest(tmp_path)
    serve_cmd._safe_extract_tar(archive, dest)
    assert (dest / "server.js").read_bytes() == b"ok"
    assert (dest / ".next" / "static" / "app.js").read_bytes() == b"x"
