"""Whole-repo source scans run once when the analyzer is built."""

from __future__ import annotations

import re
from pathlib import Path

from .dynamic_markers import read_source_text


def _find_jsx_namespace_files(
    parsed_files: dict,
    source_map: dict[str, bytes] | None = None,
) -> set[str]:
    """Repo-relative TS/TSX files that declare ``namespace JSX``.

    Types there are used implicitly by every JSX expression, never imported
    by name. A substring check is enough.
    """
    matches: set[str] = set()
    for path, pf in parsed_files.items():
        try:
            file_info = getattr(pf, "file_info", None)
            if file_info is None:
                continue
            src_path = Path(file_info.abs_path)
            if src_path.suffix not in (".ts", ".tsx", ".d.ts"):
                continue
            source = read_source_text(path, file_info.abs_path, source_map)
            if "namespace JSX" in source:
                matches.add(path)
        except Exception:
            continue
    return matches


_BUNDLER_CONFIG_STEMS = ("vite.config", "webpack.config", "rollup.config", "rspack.config")

# Any quoted slash-containing path-ish string. Bundler configs reference
# alias targets both as ``'./src/shims/x.ts'`` and as bare segments fed to
# ``path.resolve(here, 'src/shims/x.ts')``; the parsed-files membership
# probe below keeps loose matches harmless.
_RELATIVE_PATH_STRING_RE = re.compile(r"""['"]((?:\.\.?/)?[\w@.-]+(?:/[\w@.-]+)+)['"]""")

_TS_PROBE_SUFFIXES = ("", ".ts", ".tsx", ".js", ".jsx", ".mts", ".cts", ".mjs", ".cjs")


def _find_bundler_alias_targets(
    parsed_files: dict,
    source_map: dict[str, bytes] | None = None,
) -> set[str]:
    """Repo-relative paths referenced from bundler config files.

    A ``resolve.alias`` entry substitutes a package import with a local shim
    that only the config names. Any relative path string in a bundler config
    that resolves to an indexed file marks that file reachable.
    """
    targets: set[str] = set()
    configs = [
        (path, pf)
        for path, pf in parsed_files.items()
        if Path(path).name.startswith(_BUNDLER_CONFIG_STEMS)
    ]
    for path, pf in configs:
        try:
            file_info = getattr(pf, "file_info", None)
            if file_info is None:
                continue
            source = read_source_text(path, file_info.abs_path, source_map)
        except Exception:
            continue
        config_dir = Path(path).parent
        for match in _RELATIVE_PATH_STRING_RE.finditer(source):
            base = _posix_normpath(config_dir, match.group(1))
            for suffix in _TS_PROBE_SUFFIXES:
                candidate = base + suffix
                if candidate in parsed_files:
                    targets.add(candidate)
                    break
    return targets


def _posix_normpath(config_dir: Path, raw: str) -> str:
    """Join *raw* onto *config_dir* and normalise ``.``/``..`` as POSIX."""
    import posixpath

    return posixpath.normpath(posixpath.join(config_dir.as_posix(), raw))


def _find_ts_export_aliases(parsed_files: dict) -> dict[str, dict[str, str]]:
    """Per-file ``{local_name: exported_alias}`` maps for TS/JS alias exports.

    ``export { Local as Alias }`` publishes the symbol under the alias, so
    importers name only ``Alias``. Inverted from the parser's record of the
    clause rather than scanned again.
    """
    aliases: dict[str, dict[str, str]] = {}
    for path, pf in parsed_files.items():
        published = getattr(pf, "export_aliases", None)
        if not published:
            continue
        aliases[path] = {local: exported for exported, local in published.items()}
    return aliases
