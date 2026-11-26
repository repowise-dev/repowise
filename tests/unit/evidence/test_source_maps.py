"""Bundled profile positions must join the original source, across readers."""

import json

import pytest

from repowise.core.evidence.format_v1 import EvidenceFormatError
from repowise.core.evidence.profile_convert import convert_profile
from repowise.core.evidence.profile_formats import profile_formats
from repowise.core.frame_resolver import checkout_reader

from .test_cpuprofile import _ingest_checkout, _profile, _sym


@pytest.fixture
def bundle(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.ts").write_text("function original() {}\n", encoding="utf-8")
    (tmp_path / "bundle.js").write_text(
        "function a() {}\n//# sourceMappingURL=bundle.js.map\n", encoding="utf-8"
    )
    data = {"version": 3, "sources": ["src/app.ts"], "names": ["original"],
            "mappings": "AAASA"}
    (tmp_path / "bundle.js.map").write_text(json.dumps(data), encoding="utf-8")
    return tmp_path, data


def _run(root, fmt):
    path = (root / "bundle.js").as_uri()
    if fmt == "cpuprofile":
        data = _profile([(1, "(root)", "", -1, -1, [2]),
                         (2, "a", path, 0, 9, [])], [2])
    elif fmt == "speedscope":
        data = json.dumps({"$schema": "https://www.speedscope.app/file-format-schema.json",
                           "shared": {"frames": [{"name": "a", "file": path,
                                                   "line": 1, "col": 10}]},
                           "profiles": [{"type": "sampled", "name": "test", "unit": "none",
                                         "startValue": 0, "endValue": 1,
                                         "samples": [[0]], "weights": [1]}]}).encode()
    else:
        data = f"a ({path}:1) 1\n".encode()
    return profile_formats.get(fmt).read(data)


@pytest.mark.parametrize("fmt", ["cpuprofile", "speedscope", "collapsed"])
def test_profiles_join_the_original_typescript_file(bundle, fmt):
    root, _ = bundle
    result = convert_profile(
        _run(root, fmt), root=str(root), value_kind="cpu",
        symbols_by_file={"src/app.ts": [_sym("src/app.ts", "original", 1, 1, "function")]},
        read_lines=checkout_reader(root),
    )
    assert [s["path"] for s in result.document["symbols"]] == ["src/app.ts"]
    assert result.repo_self == result.total > 0
    assert result.document["symbols"][0]["function"] == "original"


@pytest.mark.parametrize("change", ["missing", "remote", "escape", "invalid", "unmapped"])
def test_bad_maps_are_explicit_refusals(bundle, change):
    root, data = bundle
    if change == "missing":
        (root / "bundle.js.map").unlink()
    elif change == "remote":
        (root / "bundle.js").write_text("//# sourceMappingURL=https://example.com/a.map\n")
    else:
        if change == "escape":
            data["sources"] = ["../outside.ts"]
        elif change == "invalid":
            data["mappings"] = "!"
        else:
            data["mappings"] = "S"
        (root / "bundle.js.map").write_text(json.dumps(data))
    # The refusal names the bundle whose map could not be used.
    with pytest.raises(EvidenceFormatError, match=r"source map.*\(in bundle\.js\)"):
        convert_profile(_run(root, "cpuprofile"), root=str(root), value_kind="cpu",
                        symbols_by_file={}, read_lines=checkout_reader(root))


@pytest.mark.parametrize("source", ["webpack://_N_E/./src/app.ts", "webpack:///src/app.ts"])
def test_webpack_sources_join_the_original_file(bundle, source):
    root, data = bundle
    data["sources"] = [source]
    (root / "bundle.js.map").write_text(json.dumps(data))
    result = convert_profile(
        _run(root, "cpuprofile"), root=str(root), value_kind="cpu",
        symbols_by_file={"src/app.ts": [_sym("src/app.ts", "original", 1, 1, "function")]},
        read_lines=checkout_reader(root),
    )
    assert [s["path"] for s in result.document["symbols"]] == ["src/app.ts"]


@pytest.mark.parametrize(("source", "found"), [
    # Next.js: relative to the app's build context, not to the chunk's map.
    ("webpack://_N_E/./src/x.ts", "apps/web/src/x.ts"),
    # A monorepo package outside the app's context.
    ("webpack://_N_E/../../packages/ui/b.ts", "packages/ui/b.ts"),
    # A module that is not a checkout file reads from the map's directory.
    ("webpack://_N_E/webpack/runtime/load", "apps/web/.next/static/webpack/runtime/load"),
])
def test_webpack_sources_resolve_against_the_build_context(source, found):
    from repowise.core.evidence.source_maps import _decode

    files = {"apps/web/src/x.ts", "packages/ui/b.ts"}
    lines = _decode(json.dumps({"version": 3, "sources": [source], "mappings": "AAAA"}),
                    "apps/web/.next/static/a.js.map", files.__contains__)
    assert lines[0][0].source == found


@pytest.mark.parametrize("source", ["webpack:///../outside.ts", "webpack://ns/C:/x.ts",
                                    "https://example.com/src/x.ts"])
def test_webpack_paths_stay_inside_the_checkout(source):
    from repowise.core.evidence.source_maps import _decode

    with pytest.raises(EvidenceFormatError, match="source map"):
        _decode(json.dumps({"version": 3, "sources": [source], "mappings": "AAAA"}),
                "a.js.map", lambda _: True)


def test_ingestion_loads_the_original_symbols_before_joining(bundle):
    root, _ = bundle
    files = {name: (root / name).read_text(encoding="utf-8")
             for name in ["src/app.ts", "bundle.js", "bundle.js.map"]}
    data = _profile([(1, "(root)", "", -1, -1, [2]),
                     (2, "a", (root / "bundle.js").as_uri(), 0, 9, [])], [2])
    payload = _ingest_checkout(
        root, files, data, {"src/app.ts": [_sym("src/app.ts", "original", 1, 1, "function")]},
        {"bundle.js": "generated"},
    )
    assert payload["stored"] is True
    assert payload["matched"] == 1
    assert payload["conversion"]["repo_self_share"] == 1
    assert payload["conversion"]["library_share"] == 0


@pytest.mark.parametrize("mappings", ["AAASA,CAAA", "SAASA", "A,SAASA"])
def test_columnless_frames_cannot_guess_a_mapping(bundle, mappings):
    root, data = bundle
    data["mappings"] = mappings
    (root / "bundle.js.map").write_text(json.dumps(data))
    with pytest.raises(EvidenceFormatError, match=r"source map.*column"):
        convert_profile(_run(root, "collapsed"), root=str(root), value_kind="cpu",
                        symbols_by_file={}, read_lines=checkout_reader(root))


def test_source_root_and_delta_state_across_lines():
    from repowise.core.evidence.source_maps import _decode

    lines = _decode(json.dumps({"version": 3, "sources": ["a.ts", "b.ts"],
                                "sourceRoot": "../src", "names": ["first", "second"],
                                "mappings": "AAESA;ACGRC;;ADHSD"}), "dist/bundle.js.map")
    assert (lines[0][0].source, lines[0][0].line, lines[0][0].original_column,
            lines[0][0].name) == ("src/a.ts", 3, 10, "first")
    assert (lines[1][0].source, lines[1][0].line, lines[1][0].original_column,
            lines[1][0].name) == ("src/b.ts", 6, 2, "second")
    assert lines[2] == []
    assert (lines[3][0].source, lines[3][0].line, lines[3][0].original_column,
            lines[3][0].name) == ("src/a.ts", 3, 11, "first")


@pytest.mark.parametrize("mappings", ["g", "!", "AA", "AAA", "AAAAAA", "DAAA",
                                      "ACAA", "AADA", "AAAD", "AAAAC", "gggggggggA"])
def test_invalid_vlq_or_indices_are_refused(mappings):
    from repowise.core.evidence.source_maps import _decode

    with pytest.raises(EvidenceFormatError, match="source map"):
        _decode(json.dumps({"version": 3, "sources": ["a.ts"], "mappings": mappings}), "a.map")


@pytest.mark.parametrize("dirty", ["bundle.js", "bundle.js.map"])
def test_dirty_bundle_or_map_is_not_used(bundle, dirty):
    root, _ = bundle
    with pytest.raises(EvidenceFormatError, match="differs from the indexed commit"):
        convert_profile(_run(root, "cpuprofile"), root=str(root), value_kind="cpu",
                        symbols_by_file={}, read_lines=checkout_reader(root), dirty_paths=[dirty])


@pytest.mark.parametrize("problem", ["inline", "remote", "webpack-internal", "indexed", "invalid"])
def test_unusable_generated_maps_remain_library_frames(bundle, problem):
    root, data = bundle
    if problem in ("inline", "remote"):
        url = "data:application/json;base64,e30=" if problem == "inline" else "https://example.com/a.map"
        (root / "bundle.js").write_text(f"//# sourceMappingURL={url}\n")
    else:
        if problem == "webpack-internal":
            data["sources"] = ["webpack-internal:///./src/app.ts"]
        elif problem == "indexed":
            data["sections"] = []
        else:
            data["mappings"] = "!"
        (root / "bundle.js.map").write_text(json.dumps(data))
    run = _run(root, "cpuprofile")
    result = convert_profile(run, root=str(root), value_kind="cpu", symbols_by_file={},
                             origins={"bundle.js": "generated"}, read_lines=checkout_reader(root))
    assert result.libraries == result.total > 0
    assert result.document["symbols"] == []
    # The same unsupported map on maintained code remains an explicit refusal.
    with pytest.raises(EvidenceFormatError, match="source map"):
        convert_profile(run, root=str(root), value_kind="cpu", symbols_by_file={},
                        read_lines=checkout_reader(root))


@pytest.mark.parametrize("dirty", ["bundle.js", "BUNDLE.JS.MAP", r"sub\..\bundle.js"])
def test_windows_dirty_paths_use_the_resolvers_case_and_separator_rules(dirty):
    from repowise.core.evidence.profile_formats.model import ProfileRun, Stack
    from repowise.core.frame_resolver import ProfileFrame

    files = {"bundle.js": ["//# sourceMappingURL=bundle.js.map"],
             "bundle.js.map": [json.dumps({"version": 3, "sources": ["src/app.ts"],
                                           "mappings": "AAAA"})]}
    run = ProfileRun((ProfileFrame("C:/SRC/Bundle.js", 1, "javascript", "a", 1),),
                     (Stack((0,), 1, 1),), "samples", "cpu")
    with pytest.raises(EvidenceFormatError, match="differs from the indexed commit"):
        convert_profile(run, root="C:/src", symbols_by_file={}, dirty_paths=[dirty],
                        read_lines=lambda p: files.get(p.casefold()))
