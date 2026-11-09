"""The in-memory candidate probe answers exactly as ``Path.resolve()`` does.

``TsconfigResolver._probe`` replaced one ``Path.resolve()`` per candidate path
with a once-per-directory listing. These tests hold it to the old answer on
aliases, ``baseUrl``, index files, extension order, case variants, symlinks
and junctions, and paths that leave the repository.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from repowise.core.ingestion.tsconfig_resolver import _INDEX_FILES, _TS_EXTENSIONS, TsconfigResolver


class _ResolveEveryCandidate(TsconfigResolver):
    """The previous code, verbatim: one ``Path.resolve()`` per candidate."""

    def _resolved_dir(self, directory: Path) -> Path:
        return directory.resolve()

    def _try_extensions(self, base: Path) -> str | None:
        rel = self._to_repo_relative(base)
        if rel is not None and rel in self._path_set:
            return rel
        for ext in _TS_EXTENSIONS:
            rel = self._to_repo_relative(Path(str(base) + ext))
            if rel is not None and rel in self._path_set:
                return rel
        for index in _INDEX_FILES:
            rel = self._to_repo_relative(base / index)
            if rel is not None and rel in self._path_set:
                return rel
        return None

    def _to_repo_relative(self, path: Path) -> str | None:
        try:
            return path.resolve().relative_to(self._repo_path).as_posix()
        except ValueError:
            return None


_FILES = (
    "src/a.ts",
    "src/a.tsx",
    "src/b.tsx",
    "src/c.js",
    "src/types.d.ts",
    "src/layout.config.ts",
    "src/dir/index.tsx",
    "src/only_index/index.vue",
    "src/Mixed/Case.ts",
    "src/file.ts",
    "packages/lib/x.mts",
    "packages/lib/sub/index.js",
    "packages/ui/index.ts",
    "fallback/y.ts",
)

_SPECIFIERS = (
    "@/a",
    "@/a.ts",
    "@/b",
    "@/c",
    "@/types.d",
    "@/layout.config",
    "@/dir",
    "@/only_index",
    "@/Mixed/Case",
    "@/mixed/case",
    "@/MIXED/CASE.ts",
    "@/mixed/Case",
    "@/file.ts/x",
    "@/missing/deep/q",
    "@/ghost",
    "@/alias",
    "@/chain",
    "@/dangling",
    "@/loop1",
    "@/a\x00",
    "@/x\x00y/z",
    "@/out",
    "@/out/z",
    "@/a.ts ",
    "@/a.",
    "@/~a",
    "@/./a",
    "@/../packages/lib/x",
    "@/../../outside/z",
    "@lib/x",
    "@lib/sub",
    "@lib/y",
    "@ui",
    "@ext/z",
    "@ext/into/a",
    "@link/x",
    "@link/sub",
    "@junction/x",
    "src/a",
    "packages/ui",
    "nope",
)


def _try_symlink(link: Path, target: Path, *, is_dir: bool) -> bool:
    try:
        link.symlink_to(target, target_is_directory=is_dir)
    except (OSError, NotImplementedError):
        return False
    return True


def _try_junction(link: Path, target: Path) -> bool:
    if os.name != "nt":
        return False
    import _winapi

    try:
        _winapi.CreateJunction(str(target), str(link))
    except OSError:
        return False
    return True


def _build_repo(tmp_path: Path) -> tuple[Path, set[str]]:
    repo = tmp_path / "repo"
    outside = tmp_path / "outside"
    for rel in _FILES:
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text("export {}\n")
    outside.mkdir()
    (outside / "z.ts").write_text("export {}\n")
    (repo / "tsconfig.json").write_text(
        json.dumps(
            {
                "compilerOptions": {
                    "baseUrl": ".",
                    "paths": {
                        "@/*": ["src/*"],
                        "@lib/*": ["packages/lib/*", "fallback/*"],
                        "@ui": ["packages/ui"],
                        "@ext/*": ["../outside/*"],
                        "@link/*": ["linked/*"],
                        "@junction/*": ["junction/*"],
                    },
                }
            }
        )
    )
    # Links: inside -> inside (dir and file), inside -> outside, outside -> inside,
    # dangling, a relative chain, and a loop.
    src = repo / "src"
    _try_symlink(repo / "linked", repo / "packages" / "lib", is_dir=True)
    _try_symlink(src / "alias.ts", src / "a.ts", is_dir=False)
    _try_symlink(src / "out", outside, is_dir=True)
    _try_symlink(outside / "into", src, is_dir=True)
    _try_symlink(src / "dangling.ts", src / "nowhere.ts", is_dir=False)
    _try_symlink(src / "chain.ts", Path("alias.ts"), is_dir=False)
    _try_symlink(src / "loop1.ts", Path("loop2.ts"), is_dir=False)
    _try_symlink(src / "loop2.ts", Path("loop1.ts"), is_dir=False)
    _try_junction(repo / "junction", repo / "packages" / "lib")

    path_set = set(_FILES)
    # Entries a walk would not list but a caller may still pass: a file that
    # is not on disk, a lowercase twin of a mixed-case file, link paths.
    path_set |= {
        "src/ghost.ts",
        "src/mixed/case.ts",
        "src/alias.ts",
        "src/chain.ts",
        "src/dangling.ts",
        "src/nowhere.ts",
        "src/loop1.ts",
        "linked/x.mts",
        "junction/x.mts",
    }
    return repo, path_set


def _short_names(directory: Path) -> set[str]:
    """Windows 8.3 short names of *directory*'s entries, where the volume makes them."""
    if os.name != "nt":
        return set()
    import ctypes

    names: set[str] = set()
    buffer = ctypes.create_unicode_buffer(1024)
    for entry in directory.iterdir():
        if ctypes.windll.kernel32.GetShortPathNameW(str(entry), buffer, 1024):
            short = Path(buffer.value).name
            if short != entry.name:
                names.add(short)
    return names


def _importers(repo: Path) -> list[str]:
    return [str(repo / "src" / "a.ts"), str(repo / "packages" / "lib" / "x.mts")]


def _outcome(call, *args):
    """The result, or the exception type: Python <3.13 raises on a symlink loop."""
    try:
        return call(*args)
    except Exception as exc:
        return type(exc)


def test_link_fixtures_were_created(tmp_path: Path) -> None:
    """Say so when this machine cannot make links, rather than pass without them."""
    repo, _ = _build_repo(tmp_path)
    if not (repo / "linked").is_symlink():
        pytest.skip("symlinks are not permitted here; the link parity cases did not run")
    assert (repo / "src" / "alias.ts").is_symlink()
    assert (repo / "src" / "loop1.ts").is_symlink()


def test_resolve_matches_resolving_every_candidate(tmp_path: Path) -> None:
    repo, path_set = _build_repo(tmp_path)
    fast = TsconfigResolver(repo, path_set)
    reference = _ResolveEveryCandidate(repo, path_set)
    for importer in _importers(repo):
        for specifier in _SPECIFIERS:
            assert _outcome(fast.resolve, specifier, importer) == _outcome(
                reference.resolve, specifier, importer
            ), (specifier, importer)


def test_every_candidate_probe_matches_path_resolve(tmp_path: Path) -> None:
    repo, path_set = _build_repo(tmp_path)
    fast = TsconfigResolver(repo, path_set)
    directories = [
        repo,
        repo / "src",
        repo / "SRC",
        repo / "src" / "mixed",
        repo / "src" / "Mixed",
        repo / "src" / "file.ts",
        repo / "src" / "out",
        repo / "src" / "missing",
        repo / "linked",
        repo / "junction",
        repo / "src" / ".." / "packages" / "lib",
        repo / "linked" / ".." / "src",
        repo.parent / "outside" / "into",
        repo.parent / "outside",
    ]
    stems = {"a", "b", "c", "Case", "case", "CASE", "x", "y", "z", "sub", "ghost", "alias"}
    stems |= {"dir", "index", "types.d", "layout.config", "file", "a.ts ", "a.", "..", "~a"}
    stems |= {"chain", "dangling", "nowhere", "loop1", "loop2"}
    names = {stem + ext for stem in stems for ext in ("", *_TS_EXTENSIONS)}
    names |= set(_INDEX_FILES) | {"file.ts::$DATA", "a.ts:x"}
    names |= _short_names(repo / "src") | _short_names(repo / "packages" / "lib")
    for directory in directories:
        for name in sorted(names):
            expected = _outcome(fast._probe_slow, directory / name)
            assert _outcome(fast._probe, directory, name) == expected, (directory, name)


def test_probes_resolve_directories_not_candidates(tmp_path: Path, monkeypatch) -> None:
    repo, path_set = _build_repo(tmp_path)
    resolver = TsconfigResolver(repo, path_set)
    calls = 0
    real_resolve = Path.resolve

    def counting_resolve(self: Path, strict: bool = False) -> Path:
        nonlocal calls
        calls += 1
        return real_resolve(self, strict)

    monkeypatch.setattr(Path, "resolve", counting_resolve)
    importer = str(repo / "src" / "b.tsx")
    specifiers = ("@/nothing", "@lib/none", "@/dir")
    for specifier in specifiers:
        resolver.resolve(specifier, importer)
    first_pass = calls
    # Each directory is resolved once, far fewer than the ~19 candidates a miss probes.
    assert first_pass < len(_TS_EXTENSIONS) + len(_INDEX_FILES)
    for _ in range(20):
        for specifier in specifiers:
            resolver.resolve(specifier, importer)
    assert calls == first_pass


@pytest.mark.skipif(
    os.name != "nt", reason="Windows resolves a case-only variant to the on-disk name"
)
def test_case_variant_resolves_to_on_disk_name_on_windows(tmp_path: Path) -> None:
    repo, _ = _build_repo(tmp_path)
    resolver = TsconfigResolver(repo, set(_FILES))
    assert resolver.resolve("@/mixed/case", str(repo / "src" / "a.ts")) == "src/Mixed/Case.ts"
