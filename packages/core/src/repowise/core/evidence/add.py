"""Read one evidence file (or one evidence-source batch), join it to the index and store the run.

The steps behind ``repowise evidence add``, without the command line, so a
server that receives the same file (a CI upload) runs the same checks and
stores the same run: :func:`ingest_evidence` takes the file's bytes, an open
session, the repository row and the checkout the clone lives in. A file or
option it will not take raises :class:`EvidenceRefusedError`; a run that reads but
does not map returns ``stored: false`` with a ``reason``, like the command.

The command runs the same steps in two parts (:func:`prepare_evidence` before
it opens the index, :func:`store_evidence` inside it) so a file refused on its
content never opens a store. A profile refused against the index (a checkout
file that is bundled output by its stored ``code_origin``) is refused by
:func:`store_evidence` before anything is written.

A batch from a source on the ``evidence-source`` seam (:mod:`.sources`) is
stored by :func:`ingest_batch` through the same steps, as the
``repowise-evidence-v1`` file a user would upload with the same entries.

One run may come in several profiles: a parent process and its workers each
write their own. :func:`prepare_evidence_files` reads them all and merges
them into one run before anything is joined, so the run is stored, ranked
and superseded as one.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from repowise.core.git_sha import is_full_sha

from .format_v1 import EVIDENCE_FORMAT, EvidenceDocument, EvidenceFormatError, parse_evidence
from .ingest import PRODUCTION_FLAG_REFUSAL, EvidenceCaps, RunProvenance
from .kinds import FLAG_WORKLOAD_CLASSES, LOCATION_JOINS, evidence_kinds

if TYPE_CHECKING:
    from .sources import SourceBatch


class EvidenceRefusedError(Exception):
    """A file or option refused before anything is stored.

    *code* is ``file_too_large``, ``invalid_evidence``, ``invalid_option``, or
    ``no_index`` for a source batch with no index to join to;
    *hint* says what to do; *option* names the ``evidence add`` option at
    fault for an ``invalid_option``.
    """

    def __init__(
        self, code: str, message: str, hint: str | None = None, *, option: str | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint
        self.option = option


def _bad_option(option: str, message: str) -> EvidenceRefusedError:
    return EvidenceRefusedError("invalid_option", message, option=option)


#: Why a profile (or Go output) is refused with no checkout.
_NEEDS_CHECKOUT = "this file is read against the checkout's source; name an existing checkout."


@dataclass(frozen=True)
class PreparedEvidence:
    """The file as read and checked: a v1 document (written or converted), or a profile run still to convert."""

    format: str
    #: The checkout the run used; frames are placed on its source. None for
    #: a source batch of entries with no checkout (:func:`ingest_batch`).
    checkout_root: Path | None
    #: The commit the run measured; None only for a production-only kind
    #: whose file names none (its window spans deploys).
    commit: str | None
    #: ``--value-kind`` as given: the clock of a profile still to convert.
    value_kind: str | None
    #: Whether the tree was dirty, as given or as the v1 file says; None when unknown.
    dirty: bool | None
    text: str | None = None
    doc: EvidenceDocument | None = None
    run: Any = None
    #: How many profile files the run was merged from.
    profiles: int = 1
    #: The repo-relative directory the file (or every file) was read from
    #: (:func:`evidence_report_dir`); ``""`` when unknown or the root.
    report_dir: str = ""


@dataclass(frozen=True)
class StoredEvidence:
    """What :func:`store_evidence` did."""

    #: The result ``evidence add --format json`` prints.
    payload: dict[str, Any]
    #: Rows kept after the per-run cap (also when the run was restamped, writing none).
    rows_kept: int = 0


def check_options(
    workload: str,
    workload_class: str | None,
    environment: str,
    commit: str | None,
    *,
    verified: bool = False,
) -> None:
    """Refuse a usage error in the options before anything is read.

    *workload_class* None leaves the class to the file's kind (a production
    kind needs none; any other kind is refused once read,
    :func:`~.ingest.run_class`). ``production`` passes only when *verified*:
    a host that checked who sent the run (CI with OIDC), never a flag.
    """
    if workload_class == "production" and not verified:
        raise _bad_option("--class", PRODUCTION_FLAG_REFUSAL)
    if workload_class is not None and workload_class not in (
        *FLAG_WORKLOAD_CLASSES,
        *(("production",) if verified else ()),
    ):
        raise _bad_option(
            "--class", f"{workload_class!r} is not one of {', '.join(FLAG_WORKLOAD_CLASSES)}."
        )
    if not workload.strip():
        raise _bad_option("--workload", "name the workload that ran.")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,32}", environment):
        raise _bad_option("--environment", "one word of up to 32 letters, digits, '.', '_' or '-'.")
    if commit is not None and not is_full_sha(commit):
        raise _bad_option(
            "--commit", "the full 40- or 64-character SHA, e.g. $(git rev-parse HEAD)."
        )


#: Share of a single-profile run spent starting or waiting on other processes
#: from which ``evidence add`` warns that their work was not profiled. Ceiling:
#: a fixed floor so one short ``git`` call stays quiet; calibrate it with the
#: other ``evidence.*`` thresholds if it proves noisy.
UNPROFILED_CHILD_SHARE = 0.01

_OS_NAMES = {"win32": "windows", "cygwin": "windows", "darwin": "macos"}


def detect_environment(env: Mapping[str, str] | None = None, platform: str | None = None) -> str:
    """Where this process runs, the tag ``evidence add`` gives a run by default.

    ``<os>-ci`` in a CI job (:func:`~repowise.core.ci.base.in_ci`), else
    ``<os>-local``: ``linux-ci``, ``macos-local``, ``windows-local``. Only
    the caller that ran the profile can say this, so a server storing an
    upload keeps ``unknown`` unless the upload names it. Runtime versions
    are left out: the profiled program's interpreter need not be this one.
    """
    from repowise.core.ci.base import in_ci

    platform = sys.platform if platform is None else platform
    name = _OS_NAMES.get(platform) or (
        "linux" if platform.startswith("linux") else re.sub(r"[^a-z]", "", platform) or "unknown"
    )
    return f"{name}-{'ci' if in_ci(env) else 'local'}"


def evidence_paths(paths: Sequence[Path]) -> list[Path]:
    """*paths* with each directory replaced by the files directly in it, each file once.

    A profiler that writes one file per process (``node --cpu-prof``) is
    pointed at a directory, and the directory is what ``evidence add``
    takes. Hidden files are left out. A glob that names no file as written
    (``'**/target/surefire-reports/*.xml'``, one JUnit directory per module)
    is expanded from the current directory, as ``coverage add`` expands its
    reports, so no shell has to.
    """
    from repowise.core.analysis.health.coverage.discovery import expand_report_patterns

    out: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        if not path.exists() and any(ch in str(path) for ch in "*?["):
            found = expand_report_patterns([path.as_posix()], Path.cwd())
            if not found:
                raise _bad_option("FILE", f"{path} matches no file.")
        elif path.is_dir():
            found = sorted(
                p for p in path.iterdir() if p.is_file() and not p.name.startswith(".")
            )
            if not found:
                raise _bad_option("FILE", f"{path} holds no files to add.")
        else:
            found = [path]
        for file in found:
            key = file.resolve()
            if key not in seen:
                seen.add(key)
                out.append(file)
    return out


def check_file_size(size: int, max_file_mb: float, name: str) -> None:
    """Refuse a file of *size* bytes over ``evidence.max_file_mb``."""
    if size > max_file_mb * 1024 * 1024:
        raise EvidenceRefusedError(
            "file_too_large",
            f"{name} is {size / 1048576:.1f} MB, over evidence.max_file_mb ({max_file_mb:g}).",
            f"Run `repowise config set evidence.max_file_mb {math.ceil(size / 1048576)}`, "
            "then add the file again, or ingest a smaller run.",
        )


def evidence_caps(config: Any) -> tuple[float, EvidenceCaps, list[str]]:
    """``evidence.max_file_mb``, the store caps, and the settings ignored as invalid, from *config*."""
    from repowise.core.settings import resolve

    problems: list[str] = []

    def value(key: str) -> Any:
        resolved = resolve(f"evidence.{key}", config)
        if resolved.problem:
            problems.append(resolved.problem)
        return resolved.value

    max_file_mb = value("max_file_mb")
    caps = EvidenceCaps(
        max_symbols_per_run=value("max_symbols_per_run"),
        max_rows_per_run=value("max_rows_per_run"),
        history_runs=value("history_runs"),
        max_runs=value("max_runs"),
    )
    return max_file_mb, caps, problems


_WS = frozenset(b" \t\r\n")
_V1_VALUE = EVIDENCE_FORMAT.encode()


def _skip_json_ws(data: bytes, idx: int) -> int:
    while idx < len(data) and data[idx] in _WS:
        idx += 1
    return idx


def _read_json_string_bytes(data: bytes, idx: int) -> tuple[bytes | None, int]:
    if idx >= len(data) or data[idx] != 0x22:  # '"'
        return None, idx
    start = idx + 1
    i = start
    while i < len(data):
        ch = data[i]
        if ch == 0x5C:  # '\\'
            i += 2
            continue
        if ch == 0x22:  # '"'
            return data[start:i], i + 1
        i += 1
    return None, len(data)


def _skip_json_container(data: bytes, idx: int) -> int:
    depth = 1
    i = idx + 1
    while i < len(data) and depth > 0:
        cur = data[i]
        if cur == 0x22:
            _, i = _read_json_string_bytes(data, i)
            continue
        if cur in (0x7B, 0x5B):
            depth += 1
        elif cur in (0x7D, 0x5D):
            depth -= 1
        i += 1
    return i


def _skip_json_value(data: bytes, idx: int) -> int:
    idx = _skip_json_ws(data, idx)
    if idx >= len(data):
        return idx
    ch = data[idx]
    if ch == 0x22:  # '"'
        _, end = _read_json_string_bytes(data, idx)
        return end
    if ch in (0x7B, 0x5B):  # '{' or '['
        return _skip_json_container(data, idx)
    while idx < len(data) and data[idx] not in b",}] \t\r\n":
        idx += 1
    return idx


def _is_v1_head(head: bytes) -> bool:
    buf = head.lstrip(b"\xef\xbb\xbf").lstrip()
    if not buf.startswith(b"{"):
        return False
    i = _skip_json_ws(buf, 1)
    while i < len(buf):
        if buf[i] == 0x7D:  # '}'
            return False
        key, after_key = _read_json_string_bytes(buf, i)
        if key is None:
            return False
        i = _skip_json_ws(buf, after_key)
        if i >= len(buf) or buf[i] != 0x3A:  # ':'
            return False
        i = _skip_json_ws(buf, i + 1)
        if key == b"format":
            val, _ = _read_json_string_bytes(buf, i)
            return val == _V1_VALUE
        i = _skip_json_value(buf, i)
        i = _skip_json_ws(buf, i)
        if i >= len(buf) or buf[i] != 0x2C:  # ','
            return False
        i = _skip_json_ws(buf, i + 1)
    return False


def input_format(given: str | None, data: bytes) -> str:
    """*given* checked against the registry, else the format sniffed from *data*.

    A file no profile format claims is read as v1, whose parser says what is
    wrong with it.
    """
    from .profile_formats import SNIFF_BYTES, detect_profile_format, profile_formats

    names = [EVIDENCE_FORMAT, *(name for name, _ in profile_formats.all())]
    if given is not None:
        if given not in names:
            raise _bad_option("--input-format", f"{given!r} is not one of {', '.join(names)}.")
        return given
    head = data[:SNIFF_BYTES]
    if _is_v1_head(head):
        return EVIDENCE_FORMAT
    return detect_profile_format(head) or EVIDENCE_FORMAT


def prepare_evidence(
    data: bytes,
    *,
    name: str | Path,
    checkout_root: Path | None,
    commit: str | None = None,
    dirty: bool | None = None,
    input_format_name: str | None = None,
    value_kind: str | None = None,
    max_file_mb: float | None = None,
) -> PreparedEvidence:
    """*data* read as v1 or a registered format, with its clock and commit checked.

    *name* names the file in messages. A local ``Path`` also lets a format read
    companion metadata. *max_file_mb*, when given, refuses a larger file.
    Raises :class:`EvidenceRefusedError`.
    """
    from .profile_convert import refuse_compiled_positions
    from .profile_formats import profile_formats

    if max_file_mb is not None:
        check_file_size(len(data), max_file_mb, str(name))
    fmt = input_format(input_format_name, data)
    text: str | None = None
    doc: EvidenceDocument | None = None
    run: Any = None
    try:
        if not data.strip():
            # Every reader would name a format the file lacks; the run that writes it never finished.
            raise EvidenceFormatError(
                f"{name} is empty: the profiled run probably crashed or stopped before writing it",
                "run the command again, check its output for a crash, then ingest the file it writes",
            )
        if fmt == EVIDENCE_FORMAT:
            text = data.decode("utf-8", errors="replace")
            doc = parse_evidence(text)
        else:
            reader = profile_formats.get(fmt)
            # A profile's frames, and Go output's packages, are placed on the
            # checkout; the other report formats name their files already.
            if checkout_root is None and (reader.read is not None or reader.needs_checkout):
                raise _bad_option("--checkout-root", _NEEDS_CHECKOUT)
            if reader.to_v1 is not None:
                text = json.dumps(reader.convert_to_v1(data, name, checkout_root), sort_keys=True)
                doc = parse_evidence(text)
            else:
                run = reader.read(data)
                # TypeScript only: what is bundled output needs the stored
                # code_origin, so convert_profile checks it against the index.
                refuse_compiled_positions(run, str(checkout_root))
    except EvidenceFormatError as exc:
        raise EvidenceRefusedError("invalid_evidence", str(exc), exc.hint) from None
    if doc is not None and doc.kind == "test_timing" and doc.entries and all(
        e["outcome"] == "error" for e in doc.entries
    ):
        # A collection or session error fails every test before it runs: no time is a test's.
        raise EvidenceRefusedError(
            "invalid_evidence",
            f"every one of the {len(doc.entries):,} tests in {name} errored, so the run timed no test",
            "read the test run's output for the error that stopped every test, fix it, then run it again",
        )
    _check_value_kind(fmt, doc, run, value_kind)
    return PreparedEvidence(
        format=fmt,
        checkout_root=checkout_root,
        commit=_measured_commit(commit, fmt, doc),
        value_kind=value_kind,
        dirty=dirty if dirty is not None or doc is None else doc.dirty,
        text=text,
        doc=doc,
        run=run,
    )


def prepare_evidence_files(
    files: Sequence[tuple[str | Path, bytes]],
    *,
    checkout_root: Path | None,
    commit: str | None = None,
    dirty: bool | None = None,
    input_format_name: str | None = None,
    value_kind: str | None = None,
    max_file_mb: float | None = None,
    report_dir: str = "",
) -> PreparedEvidence:
    """*files* (display name or local path, bytes) read as one run.

    One file is :func:`prepare_evidence`. Several are profiles of one run
    (a parent and its workers) and merge into one: they share the run's
    options (workload, class, commit, clock), and must share a format, unit
    and tool. A v1 file or benchmark results stand alone, and mixed formats,
    units, tools or clocks are refused with the reason. JUnit XML reports (a
    Maven or Gradle report directory, one file per test class) read as one
    report, and the other files beside them are left out
    (:func:`_junit_reports_as_one`). The merged run does not depend on the
    order the files are named in. *report_dir* is the repo-relative
    directory the files were read from (:func:`evidence_report_dir`).
    """
    from .profile_convert import merge_runs

    if not files:
        raise _bad_option("FILE", "name at least one evidence file.")
    files = _junit_reports_as_one(files, input_format_name)
    digests = [hashlib.sha256(data).hexdigest() for _, data in files]
    if len(set(digests)) < len(digests):
        raise EvidenceRefusedError(
            "invalid_evidence",
            "two of the files are byte-identical, so one profile's samples would count twice",
            "name each profile once; two processes rarely write identical profiles",
        )
    ordered = [f for _, f in sorted(zip(digests, files, strict=True), key=lambda pair: pair[0])]
    parts = [
        prepare_evidence(
            data,
            name=name,
            checkout_root=checkout_root,
            commit=commit,
            dirty=dirty,
            input_format_name=input_format_name,
            value_kind=value_kind,
            max_file_mb=max_file_mb,
        )
        for name, data in ordered
    ]
    if len(parts) == 1:
        return replace(parts[0], report_dir=report_dir)
    formats = sorted({p.format for p in parts})
    if len(formats) > 1:
        raise EvidenceRefusedError(
            "invalid_evidence",
            f"the files are in different formats ({', '.join(formats)}); one run merges "
            "profiles of one format",
            "profile every process with the same profiler, or add each file as its own run",
        )
    if any(p.run is None for p in parts):
        raise EvidenceRefusedError(
            "invalid_evidence",
            f"{formats[0]} files do not merge: only the profiles of one run's processes add up",
            "add each file with its own `repowise evidence add`",
        )
    clocks = sorted({p.value_kind or p.run.value_kind for p in parts})
    if len(clocks) > 1:
        raise EvidenceRefusedError(
            "invalid_evidence",
            f"the profiles sampled different clocks ({', '.join(clocks)}), so their "
            "times do not add up",
            "profile every process with the same clock, or add each file as its own run",
        )
    try:
        run = merge_runs([p.run for p in parts])
    except EvidenceFormatError as exc:
        raise EvidenceRefusedError("invalid_evidence", str(exc), exc.hint) from None
    return replace(parts[0], run=run, profiles=len(parts))


def evidence_report_dir(paths: Sequence[Path], checkout_root: Path) -> str:
    """The repo-relative directory every file in *paths* is in; ``""`` when that is the
    root, when they differ, or when one lies outside *checkout_root*."""
    root = checkout_root.resolve()
    try:
        dirs = {p.resolve().parent.relative_to(root).as_posix() for p in paths}
    except (OSError, ValueError):
        return ""
    return dirs.pop() if len(dirs) == 1 and dirs != {"."} else ""


def _junit_reports_as_one(
    files: Sequence[tuple[str | Path, bytes]], input_format_name: str | None
) -> Sequence[tuple[str | Path, bytes]]:
    """Several files holding JUnit XML reports as one report; *files* as given otherwise.

    The files that are not JUnit XML (Surefire's ``.txt`` summaries,
    ``failsafe-summary.xml``) are left out.
    """
    from .profile_formats import SNIFF_BYTES, junit

    if len(files) < 2 or input_format_name not in (None, "junit"):
        return files
    reports = [(name, data) for name, data in files if junit.sniff(data[:SNIFF_BYTES])]
    if not reports:
        return files
    try:
        combined = junit.combine(data for _, data in reports)
    except EvidenceFormatError as exc:
        raise EvidenceRefusedError("invalid_evidence", str(exc), exc.hint) from None
    name = reports[0][0] if len(reports) == 1 else f"{reports[0][0]} and {len(reports) - 1} more"
    return [(name, combined)]


def _check_value_kind(
    fmt: str, doc: EvidenceDocument | None, run: Any, value_kind: str | None
) -> None:
    """A converted profile needs its clock; a v1 file's own must not be contradicted."""
    if doc is not None:
        if value_kind is not None and doc.value_kind is None:
            raise _bad_option(
                "--value-kind",
                f"a {doc.kind} file records no clock; --value-kind names the clock "
                "of a profile only, so leave it out.",
            )
        if value_kind is not None and doc.value_kind != value_kind:
            raise _bad_option(
                "--value-kind",
                f"the file's value_kind is {doc.value_kind}; --value-kind only names "
                "the clock of a converted profile, so pass the same or none.",
            )
        return
    if value_kind is None and run.value_kind is None:
        raise _bad_option(
            "--value-kind",
            f"a {fmt} profile does not say which clock it sampled: pass "
            "--value-kind cpu for on-CPU sampling, wall for wall-clock time.",
        )


def _measured_commit(
    commit: str | None, fmt: str, doc: EvidenceDocument | None
) -> str | None:
    """The commit the run measured: *commit* or the file's, which must agree.

    A converted file's commit is what its tool recorded, never taken on its
    own: *commit* is required and the recorded one only cross-checks it. A
    production-only kind needs none: its window spans deploys, and a run that
    does not say which commit ran reads "deployed commit unknown".
    """
    in_file = doc.commit_sha if doc is not None else None
    converted = fmt != EVIDENCE_FORMAT
    if commit is not None and in_file is not None and commit != in_file:
        raise _bad_option(
            "--commit",
            f"the {fmt} file records commit {in_file}, so it did not measure "
            f"{commit}; pass the commit the run measured."
            if converted
            else f"the file says it measured {in_file}; pass the same commit or none.",
        )
    measured = commit or (None if converted else in_file)
    if measured is None and doc is not None and evidence_kinds[doc.kind].production_only:
        return None
    if measured is None:
        raise _bad_option(
            "--commit",
            "say which commit was measured: --commit $(git rev-parse HEAD) at the time "
            "of the run" + ("." if converted else ", or commit_sha in the file."),
        )
    return measured


async def _statement_sites(session: Any, repo_id: str, doc: Any) -> list[Any]:
    """The stored statement sites, when *doc* has entries that join through them."""
    spec = evidence_kinds[doc.kind]
    if spec.join != "statement" and not (
        doc.kind == "query_count" and any(e.get("path") is None for e in doc.entries)
    ):
        return []
    from repowise.core.persistence.crud.statement_sites import list_statement_sites

    return await list_statement_sites(session, repo_id)


async def _stored_routes(session: Any, repo_id: str, doc: Any) -> list[Any]:
    """The stored routes (``http_routes``), when *doc*'s entries join through them."""
    if evidence_kinds[doc.kind].join != "route":
        return []
    from repowise.core.persistence.crud.http_routes import list_http_routes

    return await list_http_routes(session, repo_id)


async def store_evidence(
    session: Any,
    repo: Any,
    prepared: PreparedEvidence,
    *,
    repo_path: Path,
    workload: str,
    workload_class: str | None,
    environment: str = "unknown",
    allow_partial: bool = False,
    caps: EvidenceCaps,
    source: str = "cli",
    verified: bool = False,
    measured_at: datetime | None = None,
) -> StoredEvidence:
    """Join *prepared* to the index of *repo* (a repository row) and store the run.

    *repo_path* is the indexed checkout: its HEAD says whether the run is
    stale, and its settings rank the refreshed read model. *workload_class*
    None takes the class from the kind; *verified* lets a host that verified
    the sender store ``production`` (:func:`~.ingest.run_class`).
    *measured_at* is when the run was measured, when the sender knows. Raises
    :class:`EvidenceRefusedError`, before anything is written, for a profile
    that ran bundled or compiled code of the checkout or a class the kind
    does not take.
    """
    from repowise.core.analysis.health.coverage import live_head_commit
    from repowise.core.analysis.health.coverage.discovery import mostly_unmapped
    from repowise.core.persistence.crud import save_evidence_run

    from .hotness import refresh_symbol_hotness
    from .ingest import (
        cap_rows,
        entry_paths,
        join_entries,
        load_origins,
        load_symbols,
        run_class,
        run_fields,
        run_key,
        stamp_bodies,
        unlisted_count,
    )

    root = prepared.checkout_root
    index_commit = getattr(repo, "head_commit", None)
    head_sha = live_head_commit(repo_path, repo)
    changed, dirty = _tree_state(root, prepared.commit, prepared.dirty)

    text, doc, conversion = prepared.text, prepared.doc, None
    if prepared.run is not None:
        if root is None:  # prepare_evidence refuses this first; kept for the type
            raise _bad_option("--checkout-root", _NEEDS_CHECKOUT)
        conversion = await _convert(
            session, repo.id, prepared.run, root, changed, prepared.value_kind
        )
        text = json.dumps(conversion.document, sort_keys=True)
        doc = parse_evidence(text)

    stored_class, why = run_class(doc.kind, workload_class, verified=verified)
    if stored_class is None:
        raise _bad_option("--class", why or "")
    spec = evidence_kinds[doc.kind]
    doc = await _placed_tests(session, repo.id, doc, prepared.report_dir)
    paths = entry_paths(doc)
    origins = await load_origins(session, repo.id, paths)
    joined = join_entries(
        doc,
        await load_symbols(session, repo.id, paths),
        origins,
        changed or frozenset(),
        await _statement_sites(session, repo.id, doc),
        await _stored_routes(session, repo.id, doc),
    )
    # A benchmark or test keeps its row without its function, and a route or
    # statement names none, so only the kinds that need the join can be
    # mostly unmapped.
    partial = (
        spec.join in LOCATION_JOINS
        and not spec.keeps_unlinked
        and mostly_unmapped(joined.matched, len(doc.entries))
    )
    provenance = RunProvenance(
        workload_name=workload,
        workload_class=stored_class,
        commit_sha=prepared.commit,
        checkout_root=str(root) if root is not None else None,
        dirty=dirty,
        environment=environment,
        source=source,
        tests_failed=prepared.run.tests_failed if prepared.run is not None else None,
    )
    summary = _summary(doc, provenance, joined, partial, index_commit, head_sha)
    summary["input_format"] = prepared.format
    summary["profiles"] = prepared.profiles
    warnings: list[dict[str, Any]] = []
    if conversion is not None:
        summary["conversion"] = conversion.summary()
        warning = _unprofiled_children(summary["conversion"], prepared.profiles)
        if warning is not None:
            warnings.append(warning)
    if stored_class == "production":
        summary["window"] = _window(doc)
        if prepared.commit is None:
            warnings.append(_DEPLOYED_COMMIT_UNKNOWN)
    if warnings:
        summary["warnings"] = warnings
    if partial and not allow_partial:
        return StoredEvidence({**summary, "stored": False, "reason": "mostly_unmapped"})
    if not joined.rows:
        return StoredEvidence({**summary, "stored": False, "reason": "nothing_matched"})

    rows, truncated = cap_rows(doc.kind, joined.rows, caps)
    if doc.kind == "profile":
        await stamp_bodies(
            session,
            repo.id,
            rows,
            index_at_measured_commit=index_commit is not None and index_commit == prepared.commit,
        )
    fields = run_fields(
        doc,
        provenance,
        joined,
        key=run_key(text, provenance, doc.kind, index_commit, prepared.report_dir),
        truncated=truncated,
        mapping_partial=partial,
        unlisted=unlisted_count(joined.rows, origins) if doc.kind == "profile" else None,
    )
    if measured_at is not None:
        # Stored times are UTC (SQLite keeps no zone); a naive time is read as UTC.
        fields["measured_at"] = (
            measured_at.astimezone(UTC) if measured_at.tzinfo is not None else measured_at
        )
    write = await save_evidence_run(
        session,
        repo.id,
        fields,
        rows,
        history_runs=caps.history_runs,
        max_runs=caps.max_runs,
    )
    if doc.kind == "profile":
        # Profiles are what the read model is built from, and the save may
        # have pruned an older one. Best effort: the run stays stored.
        await refresh_symbol_hotness(
            session, repo.id, head_commit=index_commit, repo_path=repo_path
        )
    payload = {
        **summary,
        "stored": True,
        "run_id": write.run_id,
        "restamped": write.restamped,
        "rows_written": write.rows_written,
        "truncated": truncated,
        "runs_pruned": write.runs_pruned,
    }
    return StoredEvidence(payload, rows_kept=len(rows))


#: The warning a production run with no deployed commit gets.
_DEPLOYED_COMMIT_UNKNOWN = {
    "code": "deployed_commit_unknown",
    "message": (
        "The file does not say which commit was deployed, so its rows read as "
        "measured at an unknown commit and never rank against code that changed since."
    ),
    "remedy": (
        "Pass --commit with the deployed commit, or set commit_sha (the newest "
        "deployed) and base_commit_sha (the oldest) in the file."
    ),
}


def _window(doc: EvidenceDocument) -> dict[str, Any]:
    """The window a production run covers, as the add result names it."""
    return {
        "start": doc.window_start.isoformat() if doc.window_start else None,
        "end": doc.window_end.isoformat() if doc.window_end else None,
        "base_commit_sha": doc.base_commit_sha,
    }


def _unprofiled_children(conversion: Mapping[str, Any], profiles: int) -> dict[str, Any] | None:
    """A warning when a single profile spent time starting or waiting on other processes.

    Read from the profile's own frames (multiprocessing, process pools and
    subprocess in Python, os/exec in Go, child_process, cluster and worker
    threads in Node): the parent's side is all a profile of one process can
    show. With several profiles the caller has passed the workers, and which
    process a file sampled is not recorded, so no warning is given.
    """
    share = conversion.get("child_process_share")
    if profiles > 1 or share is None or share < UNPROFILED_CHILD_SHARE:
        return None
    return {
        "code": "unprofiled_child_processes",
        "child_process_share": share,
        "message": (
            f"{share:.0%} of this run went to starting or waiting on other processes "
            "(worker pools, subprocesses); their own work is not in this profile."
        ),
        "remedy": (
            "If those processes run repo code, profile each one and pass every profile "
            "to one `repowise evidence add` (several files, or the directory holding them)."
        ),
    }


async def _placed_tests(
    session: Any, repo_id: str, doc: EvidenceDocument, report_dir: str = ""
) -> EvidenceDocument:
    """*doc* with each test placed on an indexed file: one named from *report_dir*, the
    directory its report was read from (:func:`~.ingest.rebase_test_entries`), and one
    named only by its classname (:func:`~.ingest.place_test_entries`).

    The file list is read only when a test needs it.
    """
    rebase = bool(report_dir) and doc.kind == "test_timing"
    if not rebase and not any(entry.get("classname") for entry in doc.entries):
        return doc
    from repowise.core.persistence.crud.analysis.health.metrics import health_file_paths

    from .ingest import place_test_entries, rebase_test_entries

    files = await health_file_paths(session, repo_id)
    return place_test_entries(rebase_test_entries(doc, files, report_dir), files)


async def ingest_evidence(
    session: Any,
    repo: Any,
    data: bytes | Sequence[tuple[str, bytes]],
    *,
    repo_path: Path,
    workload: str,
    workload_class: str | None,
    name: str = "the evidence file",
    checkout_root: Path | None = None,
    commit: str | None = None,
    dirty: bool | None = None,
    environment: str = "unknown",
    input_format_name: str | None = None,
    value_kind: str | None = None,
    allow_partial: bool = False,
    max_file_mb: float | None = None,
    caps: EvidenceCaps | None = None,
    source: str = "cli",
    verified: bool = False,
    measured_at: datetime | None = None,
    expect_kind: str | None = None,
    require_checkout: bool = True,
) -> dict[str, Any]:
    """Read *data*, join it to the index of *repo* and store the run; what ``evidence add`` does.

    *data* is one file's bytes (named *name* in messages), or ``(name,
    bytes)`` for each profile of one run (:func:`prepare_evidence_files`).
    *repo* is the repository row (None reads as ``no_index``), *repo_path*
    the checkout its index was built from; *checkout_root* is the checkout
    the run used (default *repo_path*), whose source places a profile's
    frames. The other options
    are ``evidence add``'s; *max_file_mb* and *caps* default to the
    repository's ``evidence`` settings. Returns the result ``evidence add
    --format json`` prints. Raises :class:`EvidenceRefusedError` for a file or
    option it will not take, and for a file that is not of *expect_kind* when given.
    *require_checkout* False takes a checkout that is not a directory as none
    (stored as no ``checkout_root``): only for v1 entries, which read no source.
    """
    commit = commit.lower() if commit is not None else None
    check_options(workload, workload_class, environment, commit, verified=verified)
    root: Path | None = (checkout_root or repo_path).resolve()
    if not root.is_dir():
        if require_checkout:
            raise _bad_option("--checkout-root", f"{root} is not a directory.")
        root = None
    if repo is None:
        return {"stored": False, "reason": "no_index"}
    if max_file_mb is None or caps is None:
        from repowise.core.repo_config import load_repo_config

        default_mb, default_caps, _ = evidence_caps(load_repo_config(repo_path))
        max_file_mb = default_mb if max_file_mb is None else max_file_mb
        caps = default_caps if caps is None else caps
    files = [(name, bytes(data))] if isinstance(data, bytes | bytearray) else list(data)
    prepared = prepare_evidence_files(
        files,
        checkout_root=root,
        commit=commit,
        dirty=dirty,
        input_format_name=input_format_name,
        value_kind=value_kind,
        max_file_mb=max_file_mb,
    )
    read_kind = prepared.doc.kind if prepared.doc is not None else "profile"
    if expect_kind is not None and read_kind != expect_kind:
        raise EvidenceRefusedError(
            "invalid_evidence", f"{name} holds a {read_kind} run, not a {expect_kind} run"
        )
    stored = await store_evidence(
        session,
        repo,
        prepared,
        repo_path=repo_path,
        workload=workload.strip(),
        workload_class=workload_class,
        environment=environment,
        allow_partial=allow_partial,
        caps=caps,
        source=source,
        verified=verified,
        measured_at=measured_at,
    )
    return stored.payload


async def ingest_batch(
    session: Any,
    repo: Any,
    batch: SourceBatch,
    *,
    repo_path: Path,
    checkout_root: Path | None = None,
    caps: EvidenceCaps | None = None,
) -> dict[str, Any]:
    """Store one :class:`~.sources.SourceBatch` the way :func:`ingest_evidence` stores a file.

    The batch's entries become the ``repowise-evidence-v1`` file a user would
    upload (:func:`~.sources.batch_v1`), or its bytes are read by their
    ``profile-format``; then the same read, join, store and refresh run, so the
    stored rows match an upload of the same entries. A source is verified by
    the host that registered it, so its batch may carry ``production`` where
    the kind allows it; a typed ``--class`` never can. Everything is written in
    one savepoint: a batch is stored whole or not at all. Raises
    :class:`EvidenceRefusedError` like :func:`ingest_evidence`.

    Only profile bytes and Go output need the checkout (their frames and
    packages are placed on the source). A batch of entries, or of bytes in a
    format that names its files (v1, benchmark and test reports), reads no
    source, so a host with no checkout (hosted) passes one that does not
    exist and the run stores none.
    """
    from .sources import SOURCE_COLUMN, batch_v1

    if repo is None:
        # Refused, not ``stored: false``: the fetch keeps its cursor, so the
        # batch is fetched again once the repository is indexed.
        raise EvidenceRefusedError(
            "no_index",
            "the repository has no index to join the batch to",
            "run `repowise init`, then fetch again",
        )
    if (batch.entries is None) == (batch.data is None):
        raise EvidenceRefusedError(
            "invalid_evidence", "a batch carries exactly one of entries and data"
        )
    if batch.data is not None:
        data, input_format_name = batch.data, batch.format
    else:
        try:
            data, input_format_name = batch_v1(batch), EVIDENCE_FORMAT
        except ValueError as exc:
            raise EvidenceRefusedError("invalid_evidence", str(exc)) from None
    try:
        async with session.begin_nested():
            return await ingest_evidence(
                session,
                repo,
                data,
                repo_path=repo_path,
                workload=batch.workload,
                workload_class=batch.workload_class,
                name=f"the {batch.kind} batch",
                checkout_root=checkout_root,
                commit=batch.commit,
                environment=batch.environment or _batch_environment(batch),
                input_format_name=input_format_name,
                value_kind=batch.value_kind,
                # ``evidence.max_file_mb`` bounds a file someone hands over; a
                # batch is in memory already, and the per-run row caps still apply.
                # Refusing it would stop the cursor on it for good.
                max_file_mb=math.inf,
                caps=caps,
                source=SOURCE_COLUMN,
                verified=True,
                measured_at=batch.measured_at,
                expect_kind=batch.kind,
                # prepare_evidence refuses profile bytes with no checkout itself.
                require_checkout=False,
            )
    except EvidenceRefusedError as exc:
        field = _BATCH_FIELDS.get(exc.option or "")
        if field is None:
            raise
        # Name the batch field at fault, not the ``evidence add`` option it stands in for.
        raise EvidenceRefusedError(exc.code, f"batch {field}: {exc}", exc.hint) from None


def _batch_environment(batch: SourceBatch) -> str:
    """Where a batch that names no environment ran: production ran where users are."""
    spec = evidence_kinds.get(batch.kind)
    production = batch.workload_class == "production" or (
        spec is not None and spec.production_only and batch.workload_class is None
    )
    return "production" if production else "unknown"


#: ``evidence add`` options and the :class:`~.sources.SourceBatch` field each one is.
_BATCH_FIELDS = {
    "--class": "workload_class",
    "--commit": "commit",
    "--environment": "environment",
    "--input-format": "format",
    "--value-kind": "value_kind",
    "--workload": "workload",
}


async def _convert(
    session: Any,
    repo_id: str,
    run: Any,
    root: Path,
    changed: frozenset[str] | None,
    value_kind: str | None,
) -> Any:
    """*run*'s frames joined to the index at *root*, as a v1 document (or a refusal)."""
    from repowise.core.frame_resolver import (
        checkout_reader,
        frame_paths,
        load_frame_symbols,
        load_jvm_source_paths,
        place_jvm_frames,
    )

    from .profile_convert import convert_profile
    from .source_maps import remap_profile

    reader = checkout_reader(root)
    if any(f.language in ("java", "kotlin") for f in run.frames):
        # A JVM profile names classes, not files: put each frame on its class's file.
        files = await load_jvm_source_paths(session, repo_id)
        run = replace(run, frames=place_jvm_frames(run.frames, root=str(root), files=files))
    symbols, origins = await load_frame_symbols(
        session,
        repo_id,
        frame_paths(run.frames, root=str(root)),
        fold_case=sys.platform == "win32",
    )
    try:
        mapped = remap_profile(run, root=str(root), read_lines=reader,
                               origins=origins, dirty_paths=changed or ())
        # Load original sources as well as the bundle paths in the profile.
        extra = frame_paths(mapped.frames, root=str(root)) - frame_paths(run.frames, root=str(root))
        if extra:
            original_symbols, original_origins = await load_frame_symbols(
                session, repo_id, extra, fold_case=sys.platform == "win32",
            )
            symbols.update(original_symbols)
            origins.update(original_origins)
        return convert_profile(
            mapped,
            root=str(root),
            symbols_by_file=symbols,
            dirty_paths=changed or (),
            origins=origins,
            read_lines=reader,
            value_kind=value_kind,
            map_sources=False,
        )
    except EvidenceFormatError as exc:
        raise EvidenceRefusedError("invalid_evidence", str(exc), exc.hint) from None


def _tree_state(
    root: Path | None, commit: str | None, dirty: bool | None
) -> tuple[Any, bool | None]:
    """Files that differ from *commit*, and *dirty* filled in from them.

    Files that differ from the commit are skipped: their lines are not the
    ones measured. Known only when the commit is the checkout's HEAD and git
    can say; otherwise (and with no checkout) the tree's state stays unknown.
    """
    from repowise.core.analysis.health.coverage import live_head_commit

    from .ingest import uncommitted_paths

    if root is None:
        return None, dirty
    at_head = commit is not None and live_head_commit(root, None) == commit
    changed = uncommitted_paths(root) if at_head else None
    if dirty is None and changed is not None:
        dirty = bool(changed)
    return changed, dirty


def _summary(
    doc: Any,
    provenance: RunProvenance,
    joined: Any,
    partial: bool,
    index_commit: str | None,
    head_sha: str | None,
) -> dict[str, Any]:
    """What the run held and how it mapped, stored or not."""
    from collections import Counter

    from .status import low_signal

    commit = provenance.commit_sha
    return {
        "kind": doc.kind,
        "workload": provenance.workload_name,
        "class": provenance.workload_class,
        "commit_sha": commit,
        "environment": provenance.environment,
        "index_commit": index_commit,
        "stale": head_sha is not None and commit is not None and commit != head_sha,
        "dirty": provenance.dirty,
        "entries": len(doc.entries),
        "matched": joined.matched,
        "skipped": dict(sorted((Counter(doc.skipped) + joined.skipped).items())),
        "unlinked": dict(sorted(joined.unlinked.items())),
        "mapping_partial": partial,
        "unmatched_sample": joined.unmatched_sample,
        "tests_failed": provenance.tests_failed,
        "low_signal": low_signal(doc),
    }


__all__ = [
    "EvidenceRefusedError",
    "PreparedEvidence",
    "StoredEvidence",
    "check_file_size",
    "check_options",
    "detect_environment",
    "evidence_caps",
    "evidence_paths",
    "evidence_report_dir",
    "ingest_batch",
    "ingest_evidence",
    "input_format",
    "prepare_evidence",
    "prepare_evidence_files",
    "store_evidence",
]
