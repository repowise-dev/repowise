"""Name-granular reachability for Apex.

Apex has no imports, and the platform enters code no Apex source names (a
trigger on DML, an ``@AuraEnabled`` call from Lightning, a scheduled job). So a
``.cls`` file is live when it is a root (a test, an entry annotation or
``global`` member, a platform interface) or when a live file names one of its
types, compared case-folded, or lands a use edge on it. Every non-Apex file
except docs, access grants and package manifests counts as live, which is how
Lightning, Visualforce, flows and custom metadata reach a class.

A fixpoint like the Swift pass: it only rescues, and two dead classes naming
each other stay dead. Ceiling: a name in a comment or string counts as a use
(strings because ``Type.forName('Foo')`` reaches classes that way).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from repowise.core.fs_walk import PRUNED_DIRS, walk_repo
from repowise.core.ids import file_path_of, is_external
from repowise.core.support_paths import DOC_EXTENSIONS

from ...entry_candidacy import is_reachability_root
from ...ingestion.decorator_names import decorator_base
from ...ingestion.models import REACHABILITY_USE_EDGE_TYPES
from ...ingestion.traverser import is_output_dir
from .constants import never_flag_path
from .contract_methods import APEX_PLATFORM_TYPES
from .file_reachability import has_dependency_importer
from .models import DeadCodeFindingData, DeadCodeKind
from .name_occurrences import IDENTIFIER_RE, MIN_ANSWERABLE_NAME_LEN
from .risk_factors import UNPROVEN_EXPORT_CONFIDENCE

APEX_LANGUAGE = "apex"

#: Folded annotations whose member the platform or the test runner calls.
_ENTRY_ANNOTATIONS: frozenset[str] = frozenset(
    {
        "auraenabled",
        "invocablemethod",
        "invocablevariable",
        "restresource",
        "httpget",
        "httppost",
        "httpput",
        "httppatch",
        "httpdelete",
        "future",
        "remoteaction",
        "readonly",
        "namespaceaccessible",
        "istest",
        "testsetup",
    }
)

#: Opens a private member to tests in other files; a member rule, never a root.
_TEST_VISIBLE = "testvisible"

#: Modifiers that make a member callable from outside the org's source.
_ENTRY_MODIFIERS: frozenset[str] = frozenset({"global", "webservice", "testmethod"})

#: Top-level kinds a name use reaches (a trigger is a ``function``, never judged).
_TYPE_KINDS: frozenset[str] = frozenset({"class", "interface", "enum"})

#: Salesforce CLI state, and metadata types that cannot name an Apex class.
_SKIP_DIRS: frozenset[str] = PRUNED_DIRS | frozenset(
    {
        ".sfdx", ".sf",
        "fields", "layouts", "listViews", "compactLayouts", "recordTypes",
        "objectTranslations", "translations", "reports", "reportTypes", "dashboards",
        "standardValueSets", "standardValueSetTranslations", "globalValueSets",
        "globalValueSetTranslations", "sharingRules", "matchingRules", "duplicateRules",
    }
)

#: Unindexed metadata, markup and project configs that can name an Apex class.
_METADATA_SUFFIXES: tuple[str, ...] = (
    ".xml", ".page", ".component", ".cmp", ".app", ".evt", ".intf", ".design",
    ".flow", ".object", ".email", ".workflow", ".tokens", ".yml", ".yaml", ".json",
)

#: Access grants and package manifests list classes without using them.
_LISTING_SUFFIXES: tuple[str, ...] = (
    ".profile-meta.xml", ".permissionset-meta.xml", ".permissionsetgroup-meta.xml",
    ".mutingpermissionset-meta.xml", ".profile", ".permissionset", ".cls-meta.xml",
    ".trigger-meta.xml",
)
_LISTING_NAMES: frozenset[str] = frozenset({"package.xml"})
_LISTING_PREFIXES: tuple[str, ...] = ("destructivechanges",)

#: Prose whose class names describe rather than use.
_PROSE_SUFFIXES: tuple[str, ...] = tuple(DOC_EXTENSIONS | {".markdown", ".html", ".htm"})

#: Read budget; past it the metadata is unread, so no Apex file is judged.
_PER_FILE_BYTES = 2 * 1024 * 1024
_TOTAL_BYTES = 256 * 1024 * 1024


def _folded(name: str) -> bytes:
    """*name* as the identifier scan yields it, folded."""
    return name.casefold().encode("ascii", "ignore")


def _folded_annotations(decorators: Iterable[str]) -> set[str]:
    """Each annotation base in a modifiers blob, folded (``@IsTest`` -> ``istest``)."""
    return {
        decorator_base(token).casefold()
        for blob in decorators
        for token in str(blob).split("@")
        if token.strip()
    }


def is_apex_entry_member(sym: Mapping[str, Any], *, as_root: bool = False) -> bool:
    """Whether the runtime, a test or a subscriber reaches this Apex member by itself.

    *as_root* asks whether the member also makes its class an entry point,
    which ``@TestVisible`` does not.
    """
    if sym.get("language") != APEX_LANGUAGE:
        return False
    annotations = _folded_annotations(sym.get("decorators") or ())
    if annotations & _ENTRY_ANNOTATIONS or (not as_root and _TEST_VISIBLE in annotations):
        return True
    return not _ENTRY_MODIFIERS.isdisjoint(sym.get("modifiers") or ())


def _is_page_bound(sym: Mapping[str, Any]) -> bool:
    """A Visualforce shape: an action returning ``PageReference``, or an extension
    constructor taking a standard controller.

    Ceiling: until Visualforce pages are parsed, a page naming the class may sit
    outside the repository, so the shape alone keeps the class live. A helper
    that merely returns a ``PageReference`` is kept live with it.
    """
    if sym.get("visibility") != "public":
        return False
    _head, arrow, returns = (sym.get("signature") or "").casefold().rpartition("->")
    if arrow:
        return returns.strip().rsplit(".", 1)[-1] == "pagereference"
    # A constructor's signature has no return arrow.
    return sym.get("kind") == "function" and (
        "standardcontroller" in returns or "standardsetcontroller" in returns
    )


def _is_listing(path: str) -> bool:
    lowered = PurePosixPath(path).name.lower()
    return (
        lowered in _LISTING_NAMES
        or lowered.startswith(_LISTING_PREFIXES)
        or lowered.endswith(_LISTING_SUFFIXES)
    )


def _is_metadata_record(lowered: str) -> bool:
    # mdapi custom metadata records are ``customMetadata/<Type>.<Record>.md``.
    return lowered.endswith(".md") and "/custommetadata/" in f"/{lowered}"


def _is_doc(path: str) -> bool:
    lowered = path.lower()
    return lowered.endswith(_PROSE_SUFFIXES) and not _is_metadata_record(lowered)


def _is_metadata(path: str) -> bool:
    lowered = path.lower()
    return lowered.endswith(_METADATA_SUFFIXES) or _is_metadata_record(lowered)


def _mentions(blob: bytes, names: frozenset[bytes]) -> set[bytes]:
    """The folded Apex type *names* written in *blob*."""
    return {t for m in IDENTIFIER_RE.finditer(blob.lower()) if (t := m.group()) in names}


def _metadata_paths(repo_root: Path, indexed: Mapping[str, bytes]) -> Iterable[str]:
    """Repo-relative paths of unindexed Salesforce metadata, sorted for a stable budget."""
    found: list[str] = []
    for current, _dirs, files in walk_repo(repo_root, prune_dirs=_SKIP_DIRS, prune=is_output_dir):
        rel_dir = current.relative_to(repo_root).as_posix()
        for name in files:
            rel = name if rel_dir == "." else f"{rel_dir}/{name}"
            if rel not in indexed and _is_metadata(rel):
                found.append(rel)
    return sorted(found)


class _ApexFacts:
    """Declared top-level type names and folded name mentions, read once."""

    def __init__(
        self,
        graph: Any,
        files: list[str],
        source_map: Mapping[str, bytes],
        repo_root: Path | None,
        unread: frozenset[bytes],
    ) -> None:
        self.declared: dict[str, set[bytes]] = {}  # apex file -> folded top-level type names
        nested = self._read_declarations(graph, files)
        names = frozenset(nested.union(*self.declared.values()))
        apex = set(files)
        self.users: dict[bytes, set[str]] = {}  # folded name -> apex files writing it
        self.external: set[bytes] = set()  # folded names written outside Apex
        for path, blob in source_map.items():
            if path in apex:
                for name in _mentions(blob, names):
                    self.users.setdefault(name, set()).add(path)
            elif not _is_listing(path) and not _is_doc(path):
                self.external |= _mentions(blob, names)
        # A name in a file ingestion skipped may be a use we could not read.
        self.external |= names & unread
        # Without the repository the metadata is unread, so nothing is judged.
        self.complete = repo_root is not None and self._read_metadata(repo_root, source_map, names)

    def _read_declarations(self, graph: Any, files: list[str]) -> set[bytes]:
        """Fill :attr:`declared`; return the folded nested type names."""
        nested: set[bytes] = set()
        for path in files:
            # The stem names the class, and a script kept as ``.cls`` that a config runs by path.
            self.declared[path] = {_folded(PurePosixPath(path).stem)}
            for data in _defined(graph, path):
                name = _folded(data.get("name") or "")
                if data.get("kind") not in _TYPE_KINDS or not name:
                    continue
                if data.get("parent_name"):
                    nested.add(name)
                else:
                    self.declared[path].add(name)
        return nested

    def _read_metadata(
        self, repo_root: Path, source_map: Mapping[str, bytes], names: frozenset[bytes]
    ) -> bool:
        """Read metadata mentions; False when the budget ran out first."""
        budget = _TOTAL_BYTES
        for rel in _metadata_paths(repo_root, source_map):
            if budget <= 0:
                return False
            if _is_listing(rel):
                continue
            try:
                with open(repo_root / rel, "rb") as handle:
                    blob = handle.read(min(_PER_FILE_BYTES, budget))
            except OSError:
                continue
            budget -= len(blob)
            self.external |= _mentions(blob, names)
        return True


def _defined(graph: Any, path: str) -> Iterable[dict]:
    return (graph.nodes.get(symbol, {}) for symbol in _defined_ids(graph, path))


def _defined_ids(graph: Any, path: str) -> Iterable[str]:
    for _path, symbol, edge in graph.out_edges(path, data=True):
        if edge.get("edge_type") == "defines":
            yield symbol


def _apex_files(graph: Any) -> list[str]:
    return [
        node
        for node, data in graph.nodes(data=True)
        if isinstance(node, str)
        and data.get("node_type", "file") == "file"
        and data.get("language") == APEX_LANGUAGE
    ]


def _extends_platform_type(parsed: Any) -> bool:
    """A class in the parsed file extends or implements a platform-instantiated type."""
    return any(
        (relation.parent_name or "").casefold() in APEX_PLATFORM_TYPES
        for relation in getattr(parsed, "heritage", None) or ()
    )


def _is_root(graph: Any, path: str, parsed_files: Mapping[str, Any]) -> bool:
    node = graph.nodes[path]
    if (
        is_reachability_root(node)
        or node.get("is_test", False)
        or never_flag_path(path)
        or has_dependency_importer(graph, path)
    ):
        return True
    parsed = parsed_files.get(path)
    # Without the parse its heritage is unknown, which is not evidence of death.
    if parsed is None or _extends_platform_type(parsed):
        return True
    return any(
        is_apex_entry_member(data, as_root=True) or _is_page_bound(data)
        for data in _defined(graph, path)
    )


class _Reach:
    """The fixpoint: which candidates a live file reaches."""

    def __init__(self, graph: Any, facts: _ApexFacts, candidates: set[str]) -> None:
        self.graph = graph
        self.facts = facts
        self.candidates = candidates
        self.reached: set[str] = set()

    def run(self) -> frozenset[str]:
        changed = True
        while changed:
            changed = False
            for path in sorted(self.candidates - self.reached):
                if self._named(path) or self._used(path):
                    self.reached.add(path)
                    changed = True
        return frozenset(self.reached)

    def _live(self, path: str) -> bool:
        return path not in self.candidates or path in self.reached

    def _named(self, path: str) -> bool:
        """A live file writes a type name *path* declares (or the name cannot be searched)."""
        facts = self.facts
        for name in facts.declared.get(path, ()):
            if len(name) < MIN_ANSWERABLE_NAME_LEN or name in facts.external:
                return True
            if any(user != path and self._live(user) for user in facts.users.get(name, ())):
                return True
        return False

    def _used(self, path: str) -> bool:
        """A use edge from a live file lands on a symbol *path* declares."""
        for symbol in _defined_ids(self.graph, path):
            for user, _symbol, use in self.graph.in_edges(symbol, data=True):
                if use.get("edge_type") not in REACHABILITY_USE_EDGE_TYPES or is_external(user):
                    continue
                source = file_path_of(user)
                if source != path and self._live(source):
                    return True
        return False


class ApexLiveness:
    """Which Apex files are live, and the folded name facts behind that answer."""

    def __init__(
        self,
        graph: Any,
        source_map: Mapping[str, bytes],
        parsed_files: Mapping[str, Any],
        repo_root: Path | None,
        unread: frozenset[bytes] = frozenset(),
    ) -> None:
        """*unread* holds the folded identifiers of files ingestion skipped."""
        files = _apex_files(graph)
        self.files = frozenset(files)
        self.facts: _ApexFacts | None = None
        self.live = self.files
        # Unread means unjudged: an Apex file, or the metadata, we could not read.
        if not files or any(path not in source_map for path in files):
            return
        facts = _ApexFacts(graph, files, source_map, Path(repo_root) if repo_root else None, unread)
        if not facts.complete:
            return
        self.facts = facts
        candidates = {path for path in files if not _is_root(graph, path, parsed_files)}
        self.live = self.files - candidates | _Reach(graph, facts, candidates).run()

    def named_outside(self, name: str, path: str) -> bool:
        """Whether a live file other than *path*, or any non-Apex file, writes *name*."""
        if self.facts is None:
            return True
        folded = _folded(name)
        if folded in self.facts.external:
            return True
        return any(user != path and user in self.live for user in self.facts.users.get(folded, ()))


def _token_lines(blob: bytes) -> dict[bytes, list[int]]:
    """``{folded identifier: line numbers}`` of one file, lines counted as the parser counts them."""
    lines: dict[bytes, list[int]] = {}
    for lineno, line in enumerate(blob.lower().split(b"\n"), start=1):
        for token in {m.group() for m in IDENTIFIER_RE.finditer(line)}:
            lines.setdefault(token, []).append(lineno)
    return lines


def settle_apex_findings(
    findings: list[DeadCodeFindingData],
    liveness: ApexLiveness,
    source_map: Mapping[str, bytes],
) -> list[DeadCodeFindingData]:
    """Drop Apex symbol findings a case-folded name use answers; returns a new list.

    A top-level type of a live file is used (that is what made the file live).
    Any other type or private member is used when its own file writes its
    name, any case, outside its declaration (an overload's header line is not
    a use), and a nested type also when a live file names it (``Outer.Inner``).
    What survives stays below deletion-ready: with no import statement, no
    importer can show an Apex class unused.
    """
    token_lines: dict[str, dict[bytes, list[int]]] = {}
    headers: dict[tuple[str, str], set[int]] = {}
    for f in findings:
        if f.symbol_name and f.file_path in liveness.files and f.start_line is not None:
            headers.setdefault((f.file_path, f.symbol_name.casefold()), set()).add(f.start_line)

    kept: list[DeadCodeFindingData] = []
    for f in findings:
        if not _is_apex_symbol_finding(f, liveness):
            kept.append(f)
            continue
        if _apex_symbol_used(f, liveness, source_map, headers, token_lines):
            continue
        if f.kind is DeadCodeKind.UNUSED_EXPORT:
            f.confidence = min(f.confidence, UNPROVEN_EXPORT_CONFIDENCE)
            f.safe_to_delete = False
        kept.append(f)
    return kept


def _is_apex_symbol_finding(f: DeadCodeFindingData, liveness: ApexLiveness) -> bool:
    return (
        f.file_path in liveness.files
        and bool(f.symbol_name)
        and f.kind in (DeadCodeKind.UNUSED_EXPORT, DeadCodeKind.UNUSED_INTERNAL)
    )


def _apex_symbol_used(
    f: DeadCodeFindingData,
    liveness: ApexLiveness,
    source_map: Mapping[str, bytes],
    headers: dict[tuple[str, str], set[int]],
    token_lines: dict[str, dict[bytes, list[int]]],
) -> bool:
    assert f.symbol_name is not None
    facts = liveness.facts
    folded = _folded(f.symbol_name)
    if len(folded) < MIN_ANSWERABLE_NAME_LEN:
        return True  # too short for the identifier scan, so unchecked
    top_level = facts is not None and folded in facts.declared.get(f.file_path, ())
    if f.kind is DeadCodeKind.UNUSED_EXPORT and top_level:
        return f.file_path in liveness.live
    blob = source_map.get(f.file_path)
    if blob is None or f.start_line is None or f.end_line is None:
        return True  # unread, so unchecked
    if f.file_path not in token_lines:
        token_lines[f.file_path] = _token_lines(blob)
    own = headers.get((f.file_path, f.symbol_name.casefold()), set())
    # Written outside its own lines and outside a same-named sibling's header.
    if any(
        not f.start_line <= lineno <= f.end_line and lineno not in own
        for lineno in token_lines[f.file_path].get(folded, ())
    ):
        return True
    return f.kind is DeadCodeKind.UNUSED_EXPORT and liveness.named_outside(
        f.symbol_name, f.file_path
    )
