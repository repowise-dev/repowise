"""Read Gradle build scripts (Groovy and Kotlin DSL) for Maven coordinates.

A dependency is a call to a configuration (``implementation``,
``testRuntimeOnly``, ``kapt``, ...) whose arguments name a coordinate: a
``'group:artifact[:version]'`` string, a ``group:``/``name:`` map, a version
catalog accessor (``libs.foo.bar``, ``libs.bundles.x``) or ``kotlin("x")``.
Records carry the ``maven`` ecosystem and ``group:artifact`` names, the same
package store the ``pom.xml`` reader fills.

Skipped: ``project(':x')`` (a module of this build), ``platform(...)`` and
``enforcedPlatform(...)`` (a BOM, which Maven does not list as a dependency
either), and scripts under a root build-logic directory (``buildSrc``,
``build-logic``), whose dependencies are the build's own classpath.

Versions come from the coordinate, ``$name`` resolved through the
``gradle.properties`` files from the script's directory up to the root, the
``gradle/libs.versions.toml`` catalog, or a ``dependency 'g:a:v'`` entry of the
dependency-management plugin in any script read here or under build logic.

Ceiling: no Gradle runs, so a version a BOM or a plugin supplies (Spring Boot's
managed versions, the Kotlin plugin's stdlib) is left unset; ``constraints {}``
entries count as declarations; a Kotlin Multiplatform ``jvmTest { dependencies {
implementation(...) } }`` is told dev or not by its configuration name only, so
it reads as production; catalogs other than ``gradle/libs.versions.toml``
and coordinates built in code (``"$group:x"``) are not read. Upgrade path: read
Gradle's resolved ``gradle.lockfile`` or a build-scan export when one exists.
"""

from __future__ import annotations

import re
from pathlib import Path

try:
    import tomllib  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover — Python <3.11
    import tomli as tomllib  # type: ignore[no-redef]

from ...code_origin import _TOOLING_ROOT_DIRS
from ...fs_walk import PRUNED_DIRS, iter_glob
from ..resolvers.jvm_gradle import _GRADLE_BLOCK_COMMENT_RE, _GRADLE_LINE_COMMENT_RE
from .base import ExternalSystemRecord
from .classifier import classify, display_name_for
from .io_kind import classify_io_kind

filenames: tuple[str, ...] = ("build.gradle", "build.gradle.kts", "libs.versions.toml")
#: The manifest format. Records carry ``maven``: Gradle resolves Maven coordinates.
ecosystem: str = "gradle"
_RECORD_ECOSYSTEM = "maven"

_CATALOG = Path("gradle") / "libs.versions.toml"
#: Scripts that configure Gradle itself, not a project's dependencies.
_NOT_PROJECT_SCRIPTS = frozenset({"settings.gradle", "settings.gradle.kts", "init.gradle", "init.gradle.kts"})

_PRUNED = PRUNED_DIRS | {"build"}
_CONF_SUFFIXES = (
    "implementation",
    "api",
    "compileonlyapi",
    "compileonly",
    "runtimeonly",
    "annotationprocessor",
    "kapt",
    "ksp",
    "compile",
    "runtime",
)
_DEV_SUFFIXES = ("compileonly", "compileonlyapi", "annotationprocessor", "kapt", "ksp")
# Not ``runtime = '...'``: an assignment is no call.
_CALL_RE = re.compile(r"(?<![\w.$])([a-z][A-Za-z0-9]*)[ \t]*(\(|[ \t](?=[^\s=]))")
_STRING_RE = re.compile(r"""['"]([^'"\n]+)['"]""")
_COORD_RE = re.compile(r"^([\w.\-]+):([\w.\-]+)(?::([^:@]+))?(?::[\w.\-]+)?(?:@\w+)?$")
_MAP_RE = re.compile(
    r"""group\s*[:=]\s*['"]([^'"]+)['"]\s*,\s*name\s*[:=]\s*['"]([^'"]+)['"]"""
    r"""(?:\s*,\s*version\s*[:=]\s*['"]([^'"]+)['"])?"""
)
_ACCESSOR_RE = re.compile(r"(?<![\w.])libs\.([A-Za-z_][\w.]*)")
_KOTLIN_RE = re.compile(r"""(?<![\w.])kotlin\s*\(\s*['"]([\w.\-]+)['"]""")
# ``project.dependencies.enforcedPlatform(...)`` too, so a dot may precede.
_SKIPPED_CALL_RE = re.compile(r"(?<!\w)(?:project|platform|enforcedPlatform|files|fileTree)\s*\(")
_MANAGED_RE = re.compile(r"""(?<![\w.])dependency\s*\(?\s*['"]([\w.\-]+:[\w.\-]+:[^'"\s]+)['"]""")
_PROPERTY_REF_RE = re.compile(r"\$\{?([A-Za-z_][\w.]*)\}?")


def is_script(path: Path) -> bool:
    """A Gradle script that may declare dependencies (``dependencies.gradle`` too)."""
    name = path.name
    return name.endswith((".gradle", ".gradle.kts")) and name not in _NOT_PROJECT_SCRIPTS


def _is_configuration(name: str) -> bool:
    """``implementation``, ``testImplementation``, ``kapt``: a suffix at a camelCase boundary."""
    low = name.lower()
    for suffix in _CONF_SUFFIXES:
        if low == suffix:
            return True
        if low.endswith(suffix) and name[-len(suffix)].isupper():
            return True
    return name == "developmentOnly"


def _is_dev(conf: str) -> bool:
    low = conf.lower()
    return (
        "test" in low
        or low.startswith("provided")
        or low == "developmentonly"
        or low.endswith(_DEV_SUFFIXES)
    )


def _strip_comments(text: str) -> str:
    return _GRADLE_LINE_COMMENT_RE.sub("", _GRADLE_BLOCK_COMMENT_RE.sub("", text))


def _scan(text: str, start: int, stop: str) -> int:
    """Index of the first *stop* character at nesting depth zero from *start*,
    outside string literals; ``len(text)`` when there is none. ``(`` nests."""
    depth, quote, i = 0, "", start
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
        elif ch in stop and depth == 0:
            return i
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        i += 1
    return len(text)


def _close(text: str, start: int) -> int:
    """Index just past the parenthesis matching the one before *start*."""
    return min(_scan(text, start, ")") + 1, len(text))


def _without_skipped_calls(args: str) -> str:
    """*args* with every ``project(...)``, ``platform(...)`` and file call removed."""
    out, pos = [], 0
    for m in _SKIPPED_CALL_RE.finditer(args):
        if m.start() < pos:
            continue
        out.append(args[pos : m.start()])
        pos = _close(args, m.end())
    out.append(args[pos:])
    return "".join(out)


def _arguments(text: str, m: re.Match[str]) -> str:
    """The argument text of the configuration call *m*: the parenthesised list,
    or for Groovy's ``implementation 'x', 'y'`` the line and any comma-continued lines."""
    if m.group(2) == "(":
        return text[m.end() : _close(text, m.end()) - 1]
    end = m.end()
    while True:
        nl = text.find("\n", end)
        line_end = len(text) if nl == -1 else nl
        if not text[end:line_end].rstrip().endswith(",") or nl == -1:
            # A closure (``{ exclude ... }``) ends the arguments; ``${v}`` in a string does not.
            return text[m.end() : _scan(text[:line_end], m.end(), "{")]
        end = nl + 1


# -- version context ------------------------------------------------------------


def _catalog(repo_root: Path) -> tuple[dict[str, tuple[str, str | None]], dict[str, list[str]]]:
    """``gradle/libs.versions.toml`` as accessor path to ``(group:artifact, version)``,
    and bundle name to accessor paths. Keys use ``.`` for ``-``/``_``, case-folded."""
    try:
        data = tomllib.loads((repo_root / _CATALOG).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}, {}
    versions = data.get("versions") if isinstance(data.get("versions"), dict) else {}

    def version_of(raw: object) -> str | None:
        if isinstance(raw, str):
            return raw
        if isinstance(raw, dict):  # rich version: strictly / require / prefer
            for k in ("strictly", "require", "prefer"):
                if isinstance(raw.get(k), str):
                    return raw[k]
        return None

    libraries: dict[str, tuple[str, str | None]] = {}
    for alias, entry in (data.get("libraries") or {}).items():
        coord, version = None, None
        if isinstance(entry, str):
            m = _COORD_RE.match(entry)
            if m:
                coord, version = f"{m.group(1)}:{m.group(2)}", m.group(3)
        elif isinstance(entry, dict):
            module = entry.get("module")
            if isinstance(module, str) and ":" in module:
                coord = module
            elif isinstance(entry.get("group"), str) and isinstance(entry.get("name"), str):
                coord = f"{entry['group']}:{entry['name']}"
            ref = entry.get("version.ref") or (
                entry["version"].get("ref") if isinstance(entry.get("version"), dict) else None
            )
            version = version_of(versions.get(ref)) if isinstance(ref, str) else version_of(entry.get("version"))
        if coord:
            libraries[_accessor_key(alias)] = (coord, version)
    bundles = {
        _accessor_key(name): [_accessor_key(a) for a in members if isinstance(a, str)]
        for name, members in (data.get("bundles") or {}).items()
        if isinstance(members, list)
    }
    return libraries, bundles


def _accessor_key(alias: str) -> str:
    return re.sub(r"[-_.]", ".", alias).casefold()


def _build_logic_scripts(repo_root: Path) -> list[Path]:
    out: list[Path] = []
    for child in repo_root.iterdir() if repo_root.is_dir() else ():
        if child.is_dir() and child.name.casefold() in _TOOLING_ROOT_DIRS:
            out.extend(
                p
                for p in iter_glob(child, ("*.gradle", "*.gradle.kts"), prune_dirs=_PRUNED)
                if is_script(p)
            )
    return out


def _is_build_logic(path: Path, repo_root: Path) -> bool:
    try:
        first = path.relative_to(repo_root).parts[0]
    except (ValueError, IndexError):
        return False
    return first.casefold() in _TOOLING_ROOT_DIRS


def _read(path: Path) -> str:
    try:
        return _strip_comments(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return ""


def _properties(directory: Path, repo_root: Path, cache: dict[Path, dict[str, str]]) -> dict[str, str]:
    """``gradle.properties`` values seen from *directory*: nearer files win."""
    if directory in cache:
        return cache[directory]
    parent = directory.parent
    inherited = (
        _properties(parent, repo_root, cache)
        if directory != repo_root and repo_root in directory.parents
        else {}
    )
    values = dict(inherited)
    try:
        text = (directory / "gradle.properties").read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith(("#", "!")) and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    cache[directory] = values
    return values


def _resolve_version(raw: str | None, props: dict[str, str]) -> str | None:
    if not raw:
        return None
    if "$" not in raw:
        return raw
    unresolved = False

    def sub(m: re.Match[str]) -> str:
        nonlocal unresolved
        value = props.get(m.group(1))
        if value is None:
            unresolved = True
            return m.group(0)
        return value

    resolved = _PROPERTY_REF_RE.sub(sub, raw)
    return None if unresolved else resolved


# -- the parser -----------------------------------------------------------------


def _declarations(
    text: str,
    libraries: dict[str, tuple[str, str | None]],
    bundles: dict[str, list[str]],
) -> list[tuple[str, str | None, str]]:
    """``(group:artifact, raw version, configuration)`` for each declaration in *text*."""
    found: list[tuple[str, str | None, str]] = []
    for m in _CALL_RE.finditer(text):
        conf = m.group(1)
        if not _is_configuration(conf):
            continue
        args = _without_skipped_calls(_arguments(text, m))
        for s in _STRING_RE.findall(args):
            coord = _COORD_RE.match(s.strip())
            if coord:
                found.append((f"{coord.group(1)}:{coord.group(2)}", coord.group(3), conf))
        for group, name, version in _MAP_RE.findall(args):
            found.append((f"{group}:{name}", version or None, conf))
        for name in _KOTLIN_RE.findall(args):
            found.append((f"org.jetbrains.kotlin:kotlin-{name}", None, conf))
        for path in _ACCESSOR_RE.findall(args):
            key = _accessor_key(path.removesuffix(".get"))
            if key.startswith("bundles."):
                aliases = bundles.get(key[len("bundles.") :], [])
            elif key.startswith(("plugins.", "versions.")):
                continue
            else:
                aliases = [key]
            for alias in aliases:
                if alias in libraries:
                    coord, version = libraries[alias]
                    found.append((coord, version, conf))
    return found


def parse_many(
    manifest_paths: list[Path] | tuple[Path, ...],
    repo_root: Path,
) -> list[ExternalSystemRecord]:
    """Declared dependencies of several Gradle scripts, read with one version context."""
    repo_root = Path(repo_root)
    scripts = [p for p in manifest_paths if is_script(p) and not _is_build_logic(p, repo_root)]
    if not scripts:
        return []
    libraries, bundles = _catalog(repo_root)
    texts = {p: _read(p) for p in scripts}
    managed: dict[str, str] = {}
    for text in [*texts.values(), *(_read(p) for p in _build_logic_scripts(repo_root))]:
        for coord in _MANAGED_RE.findall(text):
            group, artifact, version = coord.split(":", 2)
            managed.setdefault(f"{group}:{artifact}", version)

    props_cache: dict[Path, dict[str, str]] = {}
    records: list[ExternalSystemRecord] = []
    for path, text in texts.items():
        declared_in = path.relative_to(repo_root).as_posix()
        props = _properties(path.parent, repo_root, props_cache)
        by_coord: dict[str, tuple[str | None, list[str]]] = {}
        for coord, raw_version, conf in _declarations(text, libraries, bundles):
            version = _resolve_version(raw_version, props) or managed.get(coord)
            prev_version, confs = by_coord.setdefault(coord, (version, []))
            confs.append(conf)
            if prev_version is None and version is not None:
                by_coord[coord] = (version, confs)
        for coord, (version, confs) in by_coord.items():
            artifact = coord.split(":", 1)[1]
            records.append(
                ExternalSystemRecord(
                    name=coord,
                    ecosystem=_RECORD_ECOSYSTEM,
                    declared_in=declared_in,
                    version=version,
                    display_name=display_name_for(artifact),
                    category=classify(artifact),
                    io_kind=classify_io_kind(artifact),
                    # Dev only when every configuration naming it is.
                    is_dev_dep=all(_is_dev(c) for c in confs),
                    extras={"configuration": ",".join(dict.fromkeys(confs))},
                )
            )
    return records


def parse(manifest_path: Path, repo_root: Path) -> list[ExternalSystemRecord]:
    """Declared dependencies of one Gradle script."""
    return parse_many([manifest_path], repo_root)
