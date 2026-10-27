"""Node/npm detection and web UI bundle resolution (cache, download, local build)."""

from __future__ import annotations

import os
import posixpath
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath

from repowise.cli.helpers import console

_GITHUB_REPO = "repowise-dev/repowise"
_WEB_CACHE_DIR = Path.home() / ".repowise" / "web"
_MARKER_FILE = _WEB_CACHE_DIR / ".version"


_NODE_MIN_MAJOR = 20  # Next.js 15 (see packages/web/package.json engines.node)


def _node_available() -> str | None:
    """Return the path to node binary, or None."""
    return shutil.which("node")


def _node_major_version(node: str) -> int | None:
    """Return the major version of the *node* binary, or None if it can't be read.

    A failure to invoke or parse is treated as "unknown" rather than fatal — the
    caller decides whether to proceed or fall back.
    """
    try:
        result = subprocess.run(
            [node, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    raw = result.stdout.strip().lstrip("v")
    head = raw.split(".", 1)[0]
    try:
        return int(head)
    except ValueError:
        return None


def _npm_available() -> str | None:
    """Return the path to npm binary, or None."""
    return shutil.which("npm")


def _web_is_cached(version: str) -> bool:
    """Check if the web frontend is cached and matches the current version."""
    server_js = _WEB_CACHE_DIR / "server.js"
    if not server_js.exists():
        return False
    return _MARKER_FILE.exists() and _MARKER_FILE.read_text().strip() == version


def _find_local_web() -> Path | None:
    """Check if running from the repo with packages/web available."""
    # Check from both __file__ (source installs) and cwd (pip-installed runs)
    roots = [Path(__file__).resolve(), Path.cwd().resolve()]
    for start in roots:
        candidate = start
        for _ in range(10):
            candidate = candidate.parent
            pkg_web = candidate / "packages" / "web"
            if (pkg_web / "package.json").exists():
                return pkg_web
    return None


def _local_build_is_stale(web_dir: Path) -> bool:
    """True if the local .next/standalone bundle is older than any input source.

    Compares the standalone server.js mtime against the newest mtime under the
    source roots that get compiled into the bundle (web/ui/types packages plus
    web's config files). Skips node_modules / .next / .turbo. Used so that a
    cloned monorepo with stale build artifacts falls back to the released
    tarball instead of serving a bundle behind the user's source tree.
    """
    standalone = web_dir / ".next" / "standalone" / "packages" / "web" / "server.js"
    if not standalone.exists():
        return True
    build_mtime = standalone.stat().st_mtime

    repo_root = web_dir.parent.parent  # packages/web → repo root
    skip_dirs = {"node_modules", ".next", ".turbo", "dist", ".git"}

    file_inputs: list[Path] = [
        web_dir / "package.json",
        web_dir / "next.config.ts",
        web_dir / "next.config.js",
        web_dir / "tsconfig.json",
    ]
    dir_inputs: list[Path] = [
        web_dir / "src",
        web_dir / "app",
        web_dir / "components",
        web_dir / "lib",
        web_dir / "public",
        repo_root / "packages" / "ui" / "src",
        repo_root / "packages" / "types" / "src",
    ]

    for f in file_inputs:
        if f.exists() and f.is_file() and f.stat().st_mtime > build_mtime:
            return True

    for root in dir_inputs:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if any(part in skip_dirs for part in path.relative_to(root).parts):
                continue
            try:
                if path.is_file() and path.stat().st_mtime > build_mtime:
                    return True
            except OSError:
                continue
    return False


def _safe_extract_tar(archive: Path, dest: Path) -> None:
    """Extract a .tar.gz into ``dest``, refusing any member that would land outside it."""
    root = dest.resolve()

    def _inside(target: Path) -> bool:
        return target == root or target.is_relative_to(root)

    def _check_name(raw: str, member: str, what: str) -> str:
        name = raw.replace("\\", "/")
        # A colon covers drive-relative names (C:x) and NTFS alternate data streams.
        if (
            name.startswith("/")
            or Path(name).is_absolute()
            or PureWindowsPath(name).drive
            or ":" in name
        ):
            raise ValueError(f"archive {what} is absolute or drive-qualified: {member}")
        return name

    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        symlinks = {posixpath.normpath(m.name.replace("\\", "/")) for m in members if m.issym()}
        for m in members:
            name = _check_name(m.name, m.name, "member path")
            target = (root / name).resolve()
            if not _inside(target):
                raise ValueError(f"archive member escapes the destination: {m.name}")
            # Static resolution cannot follow links the archive itself creates, so no
            # path may pass through one (blocks chains like a -> . then a/b -> ..).
            parts = PurePosixPath(posixpath.normpath(name)).parts
            if any("/".join(parts[:i]) in symlinks for i in range(1, len(parts))):
                raise ValueError(f"archive member passes through a link: {m.name}")
            if m.issym() or m.islnk():
                link = _check_name(m.linkname, m.name, "link target")
                # Symlinks resolve from the member's directory, hardlinks from the root.
                base = target.parent if m.issym() else root
                if not _inside((base / link).resolve()):
                    raise ValueError(f"archive link escapes the destination: {m.name}")
                if m.islnk():
                    lparts = PurePosixPath(posixpath.normpath(link)).parts
                    if any("/".join(lparts[:i]) in symlinks for i in range(1, len(lparts) + 1)):
                        raise ValueError(f"archive link passes through a link: {m.name}")
            elif not (m.isfile() or m.isdir()):
                raise ValueError(f"archive member is not a file or directory: {m.name}")
        # The stdlib data filter (3.11.4+) re-checks the same rules at write time.
        if hasattr(tarfile, "data_filter"):
            tar.extractall(root, members=members, filter="data")
        else:
            tar.extractall(root, members=members)


def _download_web(version: str) -> bool:
    """Download pre-built web frontend from GitHub releases."""
    import httpx

    tag = f"v{version}"
    url = f"https://github.com/{_GITHUB_REPO}/releases/download/{tag}/repowise-web.tar.gz"

    console.print(f"[dim]Downloading web UI ({url})...[/dim]")
    try:
        tmp_path = None
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            tmp_path = tmp.name
            with httpx.stream("GET", url, follow_redirects=True, timeout=120) as resp:
                resp.raise_for_status()
                for chunk in resp.iter_bytes(chunk_size=65536):
                    tmp.write(chunk)
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        console.print(f"[yellow]Could not download web UI: {exc}[/yellow]")
        if tmp_path:
            os.unlink(tmp_path)
        return False

    try:
        _WEB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        # Clean old cache
        for item in _WEB_CACHE_DIR.iterdir():
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()

        _safe_extract_tar(Path(tmp_path), _WEB_CACHE_DIR)

        _MARKER_FILE.write_text(version)
        console.print("[green]Web UI downloaded and cached.[/green]")
        return True
    except Exception as exc:
        console.print(f"[yellow]Failed to extract web UI: {exc}[/yellow]")
        return False
    finally:
        os.unlink(tmp_path)


def _build_local_web(web_dir: Path, npm: str) -> bool:
    """Build the Next.js frontend from source."""
    console.print("[dim]Building web UI (first time only)...[/dim]")
    try:
        # Install deps if needed
        if not (web_dir / "node_modules").exists():
            subprocess.run(
                [npm, "install"],
                cwd=str(web_dir),
                check=True,
                capture_output=True,
            )
        # Build
        subprocess.run(
            [npm, "run", "build"],
            cwd=str(web_dir),
            check=True,
            capture_output=True,
        )
        return True
    except subprocess.CalledProcessError as exc:
        console.print(f"[yellow]Web UI build failed: {exc}[/yellow]")
        return False
