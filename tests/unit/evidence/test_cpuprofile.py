"""V8 ``.cpuprofile`` read and joined by file, line and column."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.evidence.format_v1 import EvidenceFormatError
from repowise.core.evidence.profile_convert import convert_profile
from repowise.core.evidence.profile_formats import detect_profile_format, profile_formats
from repowise.core.frame_resolver import checkout_reader
from repowise.core.workspace.repo_index import IndexedSymbol

# A real `node --cpu-prof --cpu-prof-interval 500 app.js` (Node 22) of the
# fixture, run from D:\src\cpuprofjs.
FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "cpuprofile_js"
ROOT = "D:\\src\\cpuprofjs"
URL = "file:///D:/src/cpuprofjs/"

read = profile_formats.get("cpuprofile").read


def _sym(path: str, qualified: str, start: int, end: int, kind: str) -> IndexedSymbol:
    return IndexedSymbol(
        symbol_id=f"{path}::{qualified}", name=qualified.rsplit(".", 1)[-1],
        qualified_name=qualified, kind=kind, signature="", file_path=path,
        start_line=start, end_line=end, visibility="public",
    )


def _symbols() -> dict[str, list[IndexedSymbol]]:
    """The fixture's symbols as the JavaScript parser indexes them, checked against the source."""
    spans = {
        "app.js": [
            ("app.makeText", 9, 14, "function", "function makeText("),
            ("app.hashAll", 16, 22, "function", "function hashAll("),
            ("app.squareEach", 24, 30, "function", "function squareEach("),
            ("app.main", 32, 39, "function", "function main("),
        ],
        "lib/text.js": [
            ("lib.text.tokenize", 3, 16, "function", "function tokenize("),
            ("lib.text.countWords", 18, 22, "function", "const countWords = "),
            ("lib.text.topN", 24, 26, "function", "function topN("),
        ],
        "lib/matrix.js": [
            ("lib.matrix.Matrix", 3, 28, "class", "class Matrix {"),
            ("lib.matrix.Matrix.constructor", 4, 8, "method", "  constructor(n)"),
            ("lib.matrix.Matrix.multiply", 10, 21, "method", "  multiply(other)"),
            ("lib.matrix.Matrix.trace", 23, 27, "method", "  trace()"),
            ("lib.matrix.stats", 30, 36, "variable", "const stats = {"),
            ("lib.matrix.stats.mean", 31, 35, "method", "  mean(values)"),
        ],
    }
    out: dict[str, list[IndexedSymbol]] = {}
    for path, symbols in spans.items():
        lines = (FIXTURE / path).read_text(encoding="utf-8").splitlines()
        for qualified, start, end, kind, opening in symbols:
            assert lines[start - 1].startswith(opening), (path, qualified)
            out.setdefault(path, []).append(_sym(path, qualified, start, end, kind))
    return out


def _convert(run, symbols=None, root=ROOT, reader=None):
    return convert_profile(
        run,
        root=root,
        symbols_by_file=_symbols() if symbols is None else symbols,
        read_lines=reader or checkout_reader(FIXTURE),
    )


def _profile(nodes: list[tuple[int, str, str, int, int, list[int]]], samples: list[int]) -> bytes:
    """A cpuprofile; each node is (id, functionName, url, 0-based line, 0-based column, children)."""
    doc = {
        "nodes": [
            {"id": i, "callFrame": {"functionName": name, "scriptId": "1", "url": url,
                                    "lineNumber": line, "columnNumber": col},
             "hitCount": 0, "children": kids}
            for i, name, url, line, col, kids in nodes
        ],
        "startTime": 0,
        "endTime": 1000 * (len(samples) + 1),
        "samples": samples,
        "timeDeltas": [1000] * len(samples),
    }
    return json.dumps(doc).encode()


def _real():
    return read((FIXTURE / "app.cpuprofile").read_bytes())


def test_a_cpuprofile_is_sniffed_by_content() -> None:
    data = (FIXTURE / "app.cpuprofile").read_bytes()
    assert detect_profile_format(data) == "cpuprofile"
    pretty = json.dumps(json.loads(data), indent=2).encode()
    assert detect_profile_format(b"\xef\xbb\xbf" + pretty) == "cpuprofile"
    assert detect_profile_format(b'{"nodes": [1, 2, 3]}') is None
    assert detect_profile_format(b'{"benchmarks": [{"callFrame": {}}]}') != "cpuprofile"


def test_a_real_profile_reads_wall_time_with_one_based_positions() -> None:
    run = _real()
    doc = json.loads((FIXTURE / "app.cpuprofile").read_bytes())
    assert (run.unit, run.value_kind, run.tool_name) == ("us", "wall", "v8")
    assert sum(s.samples or 0 for s in run.stacks) == len(doc["samples"])
    # Every sample weighs the time until the next, the last until endTime.
    first = doc["startTime"] + doc["timeDeltas"][0]
    assert sum(s.weight for s in run.stacks) == pytest.approx(doc["endTime"] - first)
    assert all(f.function != "(root)" for f in run.frames)
    main = next(f for f in run.frames if f.function == "main")
    # V8 writes 31:13, 0-based: line 32, the column of the parameter list.
    assert (main.path, main.line, main.column, main.language) == (
        f"{URL}app.js", 32, 14, "javascript",
    )
    assert (FIXTURE / "app.js").read_text().splitlines()[31][13] == "("


def test_a_real_profile_joins_every_repo_frame_by_line_and_column() -> None:
    conv = _convert(_real())
    got = {e["function"]: e for e in conv.document["symbols"]}
    assert set(got) == {
        "app.hashAll", "app.makeText", "lib.text.tokenize", "lib.text.countWords", "app.main",
        "app.squareEach", "lib.matrix.Matrix.multiply", "lib.matrix.stats.mean", "lib.text.topN",
    }
    # main is on every stack of the program's own work.
    assert got["app.main"]["total_samples"] == 1003
    # The forEach arrow folds into countWords; square (passed to the vendored
    # mapAll) folds into squareEach, which also takes mapAll's own time.
    assert got["lib.text.countWords"]["self_samples"] == 116
    assert got["app.squareEach"]["self_samples"] == 53
    assert conv.document["skipped"] == {"outside_repo": 102, "vendored": 1}
    assert conv.libraries > 0
    assert conv.skip_rate == pytest.approx(0.0162, abs=1e-4)
    assert conv.explained_share == pytest.approx(0.6924, abs=1e-4)


def test_frames_join_by_position_never_by_name() -> None:
    # V8 names a function by its own name; the caller's name for it is not
    # what the profile says. Position decides either way.
    data = _profile(
        [
            (1, "(root)", "", -1, -1, [2]),
            (2, "contentType", f"{URL}app.js", 8, 17, [3]),  # makeText's position
            # makeText's name, but the column of its body brace: not where it starts.
            (3, "makeText", f"{URL}app.js", 8, 25, []),
        ],
        [2, 3],
    )
    conv = _convert(read(data))
    assert [e["function"] for e in conv.document["symbols"]] == ["app.makeText"]
    assert conv.document["skipped"] == {"second_signal_mismatch": 1}


def test_node_internals_and_node_modules_are_charged_to_the_repo_caller() -> None:
    data = _profile(
        [
            (1, "(root)", "", -1, -1, [2]),
            (2, "hashAll", f"{URL}app.js", 15, 16, [3, 4]),
            (3, "digest", "node:internal/crypto/hash", 149, 39, []),
            (4, "mapAll", f"{URL}node_modules/tinydep/index.js", 2, 15, []),
        ],
        [3, 3, 4],
    )
    conv = _convert(read(data))
    entry = conv.document["symbols"][0]
    assert (entry["function"], entry["self_samples"]) == ("app.hashAll", 3)
    assert conv.document["skipped"] == {"outside_repo": 1, "vendored": 1}
    assert conv.share(conv.libraries) == pytest.approx(1 / 3, abs=1e-3)


def test_typescript_frames_of_the_checkout_are_refused() -> None:
    data = _profile(
        [(1, "(root)", "", -1, -1, [2]), (2, "main", f"{URL}src/app.ts", 4, 9, [])], [2]
    )
    with pytest.raises(EvidenceFormatError, match="TypeScript"):
        _convert(read(data))


def test_bundled_or_compiled_javascript_with_a_source_map_is_refused(tmp_path: Path) -> None:
    (tmp_path / "dist").mkdir()
    # Blank lines after the comment do not hide it.
    (tmp_path / "dist" / "main.js").write_text(
        "function a() {}\n//# sourceMappingURL=main.js.map\n" + "\n" * 6, encoding="utf-8"
    )
    dep = tmp_path / "node_modules" / "dep"
    dep.mkdir(parents=True)
    (dep / "index.js").write_text("function b() {}\n//# sourceMappingURL=index.js.map\n")
    url = tmp_path.as_uri() + "/"
    dependency_only = _profile(
        [(1, "(root)", "", -1, -1, [2]), (2, "b", f"{url}node_modules/dep/index.js", 0, 10, [])],
        [2],
    )
    reader = checkout_reader(tmp_path)
    # A dependency's own build output is a library frame, never a refusal.
    _convert(read(dependency_only), symbols={}, root=str(tmp_path), reader=reader)
    bundled = _profile(
        [(1, "(root)", "", -1, -1, [2]), (2, "a", f"{url}dist/main.js", 0, 10, [])], [2]
    )
    with pytest.raises(EvidenceFormatError, match="source map"):
        _convert(read(bundled), symbols={}, root=str(tmp_path), reader=reader)
    # The stored code_origin decides what is a dependency, as it does for joining.
    convert_profile(read(bundled), root=str(tmp_path), symbols_by_file={},
                    origins={"dist/main.js": "generated"}, read_lines=reader)


def test_a_sample_on_an_unknown_node_is_counted_and_a_file_without_deltas_refused() -> None:
    data = _profile(
        [(1, "(root)", "", -1, -1, [2]), (2, "main", f"{URL}app.js", 31, 13, [])], [2, 99]
    )
    run = read(data)
    assert run.skipped == {"bad_sample": 1}
    assert sum(s.samples or 0 for s in run.stacks) == 1
    doc = json.loads(data)
    # JSON's NaN and Infinity weigh nothing, never poison the run's total.
    nan = read(json.dumps({**doc, "timeDeltas": [float("nan"), 1000],
                           "endTime": float("inf")}).encode())
    assert sum(st.weight for st in nan.stacks) == 1000
    del doc["timeDeltas"]
    with pytest.raises(EvidenceFormatError, match="time delta"):
        read(json.dumps(doc).encode())
    with pytest.raises(EvidenceFormatError, match="not a V8 CPU profile"):
        read(b'{"nodes": 1}')


def _ingest_checkout(
    root: Path,
    files: dict[str, str],
    data: bytes,
    symbols: dict[str, list[IndexedSymbol]],
    origins: dict[str, str] | None = None,
) -> dict:
    """*files* committed at *root*, indexed with *symbols*, and *data* ingested against it."""
    import asyncio

    import git as gitpython

    from repowise.core.evidence.add import ingest_evidence
    from repowise.core.persistence import create_session_factory, get_session
    from repowise.core.persistence.database import create_engine, init_db
    from repowise.core.persistence.models import HealthFileMetric, Repository, WikiSymbol

    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    repo = gitpython.Repo.init(root)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Alice")
        cw.set_value("user", "email", "alice@example.com")
    repo.index.add(list(files))
    head = repo.index.commit("init").hexsha
    repo.close()

    async def run() -> dict:
        (root / ".repowise").mkdir()
        engine = create_engine(f"sqlite+aiosqlite:///{root / '.repowise' / 'wiki.db'}")
        try:
            await init_db(engine)
            async with get_session(create_session_factory(engine)) as session:
                session.add(Repository(id="r1", name="t", local_path=str(root), url=""))
                await session.flush()
                for path in files:
                    session.add(HealthFileMetric(
                        repository_id="r1", file_path=path,
                        code_origin=(origins or {}).get(path),
                    ))
                    for s in symbols.get(path, ()):
                        session.add(WikiSymbol(
                            repository_id="r1", file_path=path, symbol_id=s.symbol_id,
                            name=s.name, qualified_name=s.qualified_name, kind=s.kind,
                            start_line=s.start_line, end_line=s.end_line,
                        ))
            async with get_session(create_session_factory(engine)) as session:
                row = await session.get(Repository, "r1")
                return await ingest_evidence(
                    session, row, data, repo_path=root, workload="node app.js",
                    workload_class="entry_point", commit=head,
                )
        finally:
            await engine.dispose()

    return asyncio.run(run())


def test_a_real_profile_is_ingested_against_an_index(tmp_path: Path) -> None:
    files = ["app.js", "lib/text.js", "lib/matrix.js", "vendor/tinydep/index.js"]
    # The profile as if node had run in this checkout.
    data = (FIXTURE / "app.cpuprofile").read_bytes().replace(
        URL.encode(), (tmp_path.as_uri() + "/").encode()
    )
    payload = _ingest_checkout(
        tmp_path, {rel: (FIXTURE / rel).read_text(encoding="utf-8") for rel in files},
        data, _symbols(),
    )
    assert payload["stored"] is True
    assert (payload["input_format"], payload["matched"]) == ("cpuprofile", 9)
    assert payload["conversion"]["skipped_share_by_reason"] == {"vendored": 0.0162}


@pytest.mark.parametrize(("origin", "refused"), [(None, True), ("generated", False)])
def test_bundled_code_is_judged_by_its_stored_origin_on_ingest(
    tmp_path: Path, origin: str | None, refused: bool
) -> None:
    from repowise.core.evidence.add import EvidenceRefusedError
    from tests.unit.cli.test_evidence_cmd import _stored

    url = tmp_path.as_uri() + "/"
    files = {
        "app.js": "function main() {\n  return run();\n}\n",
        # By path a production file; the index may know it is generated.
        "lib/run.js": "function run() {}\n//# sourceMappingURL=run.js.map\n",
    }
    data = _profile(
        [
            (1, "(root)", "", -1, -1, [2]),
            (2, "main", f"{url}app.js", 0, 13, [3]),
            (3, "run", f"{url}lib/run.js", 0, 12, []),
        ],
        [3, 3],
    )
    symbols = {"app.js": [_sym("app.js", "app.main", 1, 3, "function")]}
    origins = {"lib/run.js": origin} if origin else None
    if refused:
        with pytest.raises(EvidenceRefusedError) as exc:
            _ingest_checkout(tmp_path, files, data, symbols, origins)
        assert exc.value.code == "invalid_evidence" and "source map" in str(exc.value)
        assert _stored(tmp_path) == ([], [])
    else:
        payload = _ingest_checkout(tmp_path, files, data, symbols, origins)
        assert payload["stored"] is True
        # The generated file is a library frame: its time goes to main.
        assert payload["conversion"]["library_share"] == 1.0


def test_the_profiles_of_one_runs_processes_merge_into_one_run() -> None:
    from repowise.core.evidence.add import prepare_evidence_files

    # --cpu-prof writes one file per process and worker thread.
    parent = _profile(
        [(1, "(root)", "", -1, -1, [2]), (2, "main", f"{URL}app.js", 31, 13, [3]),
         (3, "makeText", f"{URL}app.js", 8, 17, [])],
        [3, 3, 2],
    )
    worker = _profile(
        [(1, "(root)", "", -1, -1, [2]), (2, "tokenize", f"{URL}lib/text.js", 2, 17, [])],
        [2, 2],
    )
    prepared = prepare_evidence_files(
        [("parent.cpuprofile", parent), ("worker.cpuprofile", worker)],
        checkout_root=FIXTURE,
        commit="a" * 40,
    )
    assert (prepared.format, prepared.profiles) == ("cpuprofile", 2)
    got = {e["function"]: e for e in _convert(prepared.run).document["symbols"]}
    assert got["app.makeText"]["self_samples"] == 2
    assert got["app.main"]["total_samples"] == 3
    assert got["lib.text.tokenize"]["self_samples"] == 2
