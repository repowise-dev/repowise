"""Characterisation tests for the call-resolution cascade.

Every strategy in ``CallResolver`` is reachable only through one long chain, and
six of them had no test that triggered them at all. These pin the observable
contract — which strategy fires, at which confidence, under which origin, and
which languages consult a language-specific strategy — so a refactor of the
chain is a refactor and not a rewrite.

The dispatch tests use spies rather than fixtures because the alternative is a
CMake or Cargo workspace per assertion; what is under test is which strategies a
language reaches and in what order, and a spy states that directly.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from repowise.core.ingestion.call_resolver import CallResolver
from repowise.core.ingestion.models import FileInfo, ParsedFile
from repowise.core.ingestion.parser import parse_file


def _file_info(rel: str, abs_: Path, lang: str) -> FileInfo:
    return FileInfo(
        path=rel,
        abs_path=str(abs_),
        language=lang,  # type: ignore[arg-type]
        size_bytes=abs_.stat().st_size,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _parse_all(tmp_path: Path, files: dict[str, tuple[str, str]]) -> dict[str, ParsedFile]:
    out: dict[str, ParsedFile] = {}
    for rel, (lang, content) in files.items():
        abs_ = tmp_path / rel
        abs_.parent.mkdir(parents=True, exist_ok=True)
        abs_.write_text(content)
        out[rel] = parse_file(_file_info(rel, abs_, lang), content.encode("utf-8"))
    return out


def _link_imports(parsed: dict[str, ParsedFile], links: dict[str, dict[str, str]]) -> None:
    """Stand in for the import-resolution phase, which unit tests do not run."""
    for path, module_to_file in links.items():
        for imp in parsed[path].imports:
            target = module_to_file.get(imp.module_path)
            if target is not None:
                imp.resolved_file = target


def _edges(
    parsed: dict[str, ParsedFile],
    tmp_path: Path,
    import_targets: dict[str, set[str]] | None = None,
) -> list[tuple[str, str, float, str]]:
    resolver = CallResolver(
        parsed,
        import_targets if import_targets is not None else {p: set() for p in parsed},
        repo_path=str(tmp_path),
    )
    return [
        (rc.caller_id, rc.callee_id, rc.confidence, rc.origin)
        for path, pf in parsed.items()
        for rc in resolver.resolve_file(path, pf.calls)
    ]


class TestUnpinnedStrategies:
    """One test per strategy that no existing test triggered."""

    def test_receiver_names_a_class_in_the_same_file(self, tmp_path: Path) -> None:
        parsed = _parse_all(
            tmp_path,
            {
                "app.py": (
                    "python",
                    "class User:\n"
                    "    def save(self):\n"
                    "        return 1\n"
                    "\n"
                    "def run():\n"
                    "    return User.save()\n",
                )
            },
        )
        assert (
            "app.py::run",
            "app.py::User::save",
            0.93,
            "receiver_same_file",
        ) in _edges(parsed, tmp_path)

    def test_a_repo_wide_unique_name_resolves_as_a_guess(self, tmp_path: Path) -> None:
        parsed = _parse_all(
            tmp_path,
            {
                "lib.py": ("python", "def only_one_of_these():\n    return 1\n"),
                "caller.py": ("python", "def run():\n    return only_one_of_these()\n"),
            },
        )
        assert (
            "caller.py::run",
            "lib.py::only_one_of_these",
            0.50,
            "global_unique",
        ) in _edges(parsed, tmp_path)

    def test_a_repo_wide_ambiguous_name_resolves_to_nothing(self, tmp_path: Path) -> None:
        parsed = _parse_all(
            tmp_path,
            {
                "a.py": ("python", "def shared():\n    return 1\n"),
                "b.py": ("python", "def shared():\n    return 2\n"),
                "caller.py": ("python", "def run():\n    return shared()\n"),
            },
        )
        assert _edges(parsed, tmp_path) == []

    def test_receiver_is_a_module_alias(self, tmp_path: Path) -> None:
        parsed = _parse_all(
            tmp_path,
            {
                "models.py": ("python", "def build():\n    return 1\n"),
                "caller.py": ("python", "import models\n\ndef run():\n    return models.build()\n"),
            },
        )
        _link_imports(parsed, {"caller.py": {"models": "models.py"}})
        assert (
            "caller.py::run",
            "models.py::build",
            0.88,
            "module_alias",
        ) in _edges(parsed, tmp_path)

    def test_jvm_bare_call_reaches_a_same_package_sibling(self, tmp_path: Path) -> None:
        parsed = _parse_all(
            tmp_path,
            {
                "src/com/example/Helper.java": (
                    "java",
                    "package com.example;\n\npublic class Helper {\n    Helper() {}\n}\n",
                ),
                "src/com/example/Main.java": (
                    "java",
                    "package com.example;\n\n"
                    "public class Main {\n"
                    "    void run() {\n"
                    "        new Helper();\n"
                    "    }\n"
                    "}\n",
                ),
            },
        )
        hits = [e for e in _edges(parsed, tmp_path) if e[3] == "same_package"]
        # The sibling's flat symbol index is last-wins, so the constructor and
        # not the class is what ``Helper`` names by the time the tier reads it.
        assert hits == [
            (
                "src/com/example/Main.java::Main::run",
                "src/com/example/Helper.java::Helper::Helper",
                0.90,
                "same_package",
            )
        ]

    def test_a_target_name_declared_nowhere_resolves_to_nothing(self, tmp_path: Path) -> None:
        """The population the cascade spends most of its budget on."""
        parsed = _parse_all(
            tmp_path,
            {
                "caller.py": (
                    "python",
                    "def run(obj):\n    obj.never_declared_anywhere()\n    "
                    "also_never_declared()\n",
                )
            },
        )
        assert _edges(parsed, tmp_path) == []


class _Spy:
    """Records that a strategy was consulted, and declines to resolve."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def wrap(self, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
        original = getattr(CallResolver, name)

        def spy(inner_self, *args, **kwargs):
            self.calls.append(name)
            return original(inner_self, *args, **kwargs)

        monkeypatch.setattr(CallResolver, name, spy)


_LANGUAGE_STRATEGIES = (
    "_resolve_go_same_package",
    "_resolve_go_package_call",
    "_resolve_jvm_same_package",
    "_resolve_java_same_package_unique",
    "_resolve_jvm_receiver_same_package",
    "_resolve_cpp_same_target",
)

# Declares the called names elsewhere, so a dispatch test measures dispatch and
# not the reject-early gate. Kotlin so nothing else in these repos can match it.
_DECOY = (
    "kotlin",
    "class Decoy {\n    fun elsewhere() {}\n    fun Elsewhere() {}\n}\n",
)


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> _Spy:
    recorder = _Spy()
    for name in _LANGUAGE_STRATEGIES:
        recorder.wrap(monkeypatch, name)
    return recorder


class TestLanguageDispatch:
    """Which language-specific strategies a file reaches, and which it does not."""

    def test_every_registered_strategy_exists(self) -> None:
        from repowise.core.ingestion.call_resolver import _LANGUAGE_CALL_STRATEGIES

        for strategies in _LANGUAGE_CALL_STRATEGIES.values():
            for name in strategies.free + strategies.member:
                assert callable(getattr(CallResolver, name, None)), name

    @pytest.mark.parametrize(
        ("rel", "lang", "source", "expected"),
        [
            (
                "main.go",
                "go",
                "package main\n\nfunc run() {\n\telsewhere()\n}\n",
                ["_resolve_go_same_package"],
            ),
            (
                "Main.java",
                "java",
                "class Main {\n    void run() {\n        elsewhere();\n    }\n}\n",
                # java gates the package tier on uniqueness, kotlin does not.
                ["_resolve_java_same_package_unique"],
            ),
            (
                "main.cc",
                "cpp",
                "void run() {\n  elsewhere();\n}\n",
                ["_resolve_cpp_same_target"],
            ),
            ("main.py", "python", "def run():\n    elsewhere()\n", []),
            ("main.rs", "rust", "fn run() {\n    elsewhere();\n}\n", []),
        ],
    )
    def test_a_bare_call_consults_only_its_own_languages_strategies(
        self,
        tmp_path: Path,
        spy: _Spy,
        rel: str,
        lang: str,
        source: str,
        expected: list[str],
    ) -> None:
        parsed = _parse_all(tmp_path, {rel: (lang, source), "Decoy.kt": _DECOY})
        _edges(parsed, tmp_path)
        assert spy.calls == expected

    def test_a_member_call_consults_only_its_own_languages_strategies(
        self, tmp_path: Path, spy: _Spy
    ) -> None:
        parsed = _parse_all(
            tmp_path,
            {
                "main.go": ("go", "package main\n\nfunc run() {\n\tpkg.Elsewhere()\n}\n"),
                "main.py": ("python", "def run(pkg):\n    pkg.elsewhere()\n"),
                "Decoy.kt": _DECOY,
            },
        )
        _edges(parsed, tmp_path)
        assert spy.calls == ["_resolve_go_package_call"]

    def test_an_undeclared_name_reaches_no_strategy_at_all(
        self, tmp_path: Path, spy: _Spy
    ) -> None:
        """The reject-early gate: nothing declares it, so nothing can match it."""
        parsed = _parse_all(
            tmp_path,
            {
                "main.go": (
                    "go",
                    "package main\n\nfunc run() {\n\tnowhere()\n\tpkg.Nowhere()\n}\n",
                )
            },
        )
        assert _edges(parsed, tmp_path) == []
        assert spy.calls == []

    def test_a_language_strategy_runs_before_the_import_tiers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Go's package tier owns a name an import tier would also answer."""
        order: list[str] = []
        for name in ("_resolve_go_same_package", "_merged_symbols_for"):
            original = getattr(CallResolver, name)

            def spy(inner_self, *args, _name=name, _original=original, **kwargs):
                order.append(_name)
                return _original(inner_self, *args, **kwargs)

            monkeypatch.setattr(CallResolver, name, spy)

        parsed = _parse_all(
            tmp_path,
            {
                "pkg/helper.go": ("go", "package pkg\n\nfunc Helper() int {\n\treturn 1\n}\n"),
                "pkg/main.go": ("go", "package pkg\n\nfunc run() int {\n\treturn Helper()\n}\n"),
            },
        )
        edges = _edges(parsed, tmp_path, {"pkg/main.go": {"pkg/helper.go"}, "pkg/helper.go": set()})
        assert (
            "pkg/main.go::run",
            "pkg/helper.go::Helper",
            0.90,
            "same_package",
        ) in edges
        assert order == ["_resolve_go_same_package"]


class TestFieldTypedReceiver:
    """A receiver typed from the enclosing class rather than the calling body.

    The ordering is the contract: a local of the same name shadows the field,
    so the body's answer has to win outright.
    """

    def test_a_private_field_types_its_receiver(self, tmp_path: Path) -> None:
        parsed = _parse_all(
            tmp_path,
            {
                "Api.cs": (
                    "csharp",
                    "public class Api\n{\n"
                    "    private readonly RouteFinder _finder;\n"
                    "    public int Run() { return _finder.Find(); }\n"
                    "}\n",
                ),
                "RouteFinder.cs": (
                    "csharp",
                    "public class RouteFinder\n{\n    public int Find() { return 1; }\n}\n",
                ),
            },
        )
        assert (
            "Api.cs::Api::Run",
            "RouteFinder.cs::RouteFinder::Find",
            0.75,
            "receiver_field_global",
        ) in _edges(parsed, tmp_path)

    def test_a_local_shadowing_a_field_wins(self, tmp_path: Path) -> None:
        parsed = _parse_all(
            tmp_path,
            {
                "Api.cs": (
                    "csharp",
                    "public class Api\n{\n"
                    "    private readonly RouteFinder _thing;\n"
                    "    public int Run() { OtherFinder _thing = Make(); return _thing.Find(); }\n"
                    "}\n",
                ),
                "RouteFinder.cs": (
                    "csharp",
                    "public class RouteFinder\n{\n    public int Find() { return 1; }\n}\n",
                ),
                "OtherFinder.cs": (
                    "csharp",
                    "public class OtherFinder\n{\n    public int Find() { return 2; }\n}\n",
                ),
            },
        )
        edges = _edges(parsed, tmp_path)
        assert (
            "Api.cs::Api::Run",
            "OtherFinder.cs::OtherFinder::Find",
            0.75,
            "receiver_typed_global",
        ) in edges
        assert not [e for e in edges if e[3].startswith("receiver_field_")]

    def test_a_field_whose_type_lacks_the_method_yields_no_edge(self, tmp_path: Path) -> None:
        """The validator is the whole safety argument for a text scan."""
        parsed = _parse_all(
            tmp_path,
            {
                "Api.cs": (
                    "csharp",
                    "public class Api\n{\n"
                    "    private readonly RouteFinder _finder;\n"
                    "    public int Run() { return _finder.Missing(); }\n"
                    "}\n",
                ),
                "RouteFinder.cs": (
                    "csharp",
                    "public class RouteFinder\n{\n    public int Find() { return 1; }\n}\n",
                ),
            },
        )
        assert not [e for e in _edges(parsed, tmp_path) if e[3].startswith("receiver_field_")]

    def test_a_nested_class_field_types_its_receiver(self, tmp_path: Path) -> None:
        """A nested class's id carries its outer class; its methods' ids do not."""
        parsed = _parse_all(
            tmp_path,
            {
                "p/Token.java": (
                    "java",
                    "package p;\n"
                    "abstract class Token {\n"
                    "    static final class Chars extends Token {\n"
                    "        final TokenData data = new TokenData();\n"
                    "        Chars data(String str) { data.set(str); return this; }\n"
                    "    }\n"
                    "}\n",
                ),
                "p/TokenData.java": (
                    "java",
                    "package p;\nclass TokenData {\n    void set(String str) { }\n}\n",
                ),
                "p/Tag.java": (
                    "java",
                    "package p;\npublic class Tag {\n    public Tag set(int option) { return this; }\n}\n",
                ),
            },
        )
        assert (
            "p/Token.java::Chars::data",
            "p/TokenData.java::TokenData::set",
            0.90,
            "receiver_field_same_package",
        ) in _edges(parsed, tmp_path)

    def test_two_nested_classes_sharing_a_name_type_no_field(self, tmp_path: Path) -> None:
        """Both ``Holder.run`` bodies mint one id, so which class it is stays open."""
        parsed = _parse_all(
            tmp_path,
            {
                "p/Pair.java": (
                    "java",
                    "package p;\n"
                    "class First {\n"
                    "    static class Holder {\n"
                    "        Finder f;\n"
                    "        int run() { return f.find(); }\n"
                    "    }\n"
                    "}\n"
                    "class Second {\n"
                    "    static class Holder {\n"
                    "        Other f;\n"
                    "        int run() { return f.find(); }\n"
                    "    }\n"
                    "}\n",
                ),
                "p/Finder.java": (
                    "java",
                    "package p;\nclass Finder {\n    int find() { return 1; }\n}\n",
                ),
                "p/Other.java": (
                    "java",
                    "package p;\nclass Other {\n    int find() { return 2; }\n}\n",
                ),
            },
        )
        assert not [e for e in _edges(parsed, tmp_path) if e[3].startswith("receiver_field_")]

    def test_a_nested_class_reaches_an_inherited_method(self, tmp_path: Path) -> None:
        """The hierarchy is keyed on the nested class's own id, outer name and all."""
        parsed = _parse_all(
            tmp_path,
            {
                "Token.cs": (
                    "csharp",
                    "class Token\n{\n"
                    "    class Chars : Base\n    {\n"
                    "        void Clear() { this.Reset(); }\n"
                    "    }\n}\n",
                ),
                "Base.cs": ("csharp", "class Base\n{\n    public void Reset() { }\n}\n"),
                # A second declaration keeps the bare-name tiers from answering,
                # so only the hierarchy can.
                "Other.cs": ("csharp", "class Other\n{\n    public void Reset() { }\n}\n"),
            },
        )
        resolver = CallResolver(
            parsed,
            {p: set() for p in parsed},
            repo_path=str(tmp_path),
            heritage_parents={"Token.cs::Token::Chars": {"Base.cs::Base"}},
        )
        edges = [
            (rc.caller_id, rc.callee_id, rc.origin)
            for rc in resolver.resolve_file("Token.cs", parsed["Token.cs"].calls)
        ]
        assert ("Token.cs::Chars::Clear", "Base.cs::Base::Reset", "self_inherited") in edges


_GO_DETECT = (
    "go",
    "package detect\n\n"
    "type Detector struct{}\n\n"
    "type Other struct{}\n\n"
    "func NewDetector() *Detector { return &Detector{} }\n\n"
    "func NewPair() (*Detector, error) { return &Detector{}, nil }\n\n"
    "func NewOther() *Other { return &Other{} }\n\n"
    "func (d *Detector) Child() *Other { return &Other{} }\n\n"
    "func (d *Detector) Scan() int { return 1 }\n\n"
    "func (o *Other) Scan() int { return 2 }\n\n"
    "func (o *Other) Walk() int { return 3 }\n",
)


def _go_method_edges(tmp_path: Path, body: str) -> list[tuple[str, str, float, str]]:
    """Method edges from ``cmd/run.go::run`` whose body is *body*."""
    parsed = _parse_all(
        tmp_path,
        {
            "detect/detect.go": _GO_DETECT,
            "cmd/run.go": (
                "go",
                'package cmd\n\nimport "example.com/app/detect"\n\n'
                f"func run(ok bool) {{\n{body}}}\n",
            ),
        },
    )
    _link_imports(parsed, {"cmd/run.go": {"example.com/app/detect": "detect/detect.go"}})
    edges = _edges(
        parsed, tmp_path, {"cmd/run.go": {"detect/detect.go"}, "detect/detect.go": set()}
    )
    return [e for e in edges if e[0] == "cmd/run.go::run" and e[1].count("::") == 2]


class TestGoCallTypedLocal:
    """``x := pkg.New(..)`` types ``x`` from ``New``'s declared return type."""

    def test_a_constructor_result_types_its_receiver(self, tmp_path: Path) -> None:
        edges = _go_method_edges(tmp_path, "\td := detect.NewDetector()\n\td.Scan()\n")
        assert [e[1] for e in edges] == ["detect/detect.go::Detector::Scan"]
        assert edges[0][3].startswith("receiver_typed_")

    def test_the_first_of_several_results_is_the_type(self, tmp_path: Path) -> None:
        body = "\td, err := detect.NewPair()\n\t_ = err\n\td.Scan()\n"
        edges = _go_method_edges(tmp_path, body)
        assert [e[1] for e in edges] == ["detect/detect.go::Detector::Scan"]

    def test_a_local_typed_from_a_method_on_an_earlier_one(self, tmp_path: Path) -> None:
        body = "\td := detect.NewDetector()\n\tc := d.Child()\n\tc.Walk()\n"
        assert sorted(e[1] for e in _go_method_edges(tmp_path, body)) == [
            "detect/detect.go::Detector::Child",
            "detect/detect.go::Other::Walk",
        ]

    def test_two_blocks_declaring_different_types_type_neither(self, tmp_path: Path) -> None:
        """Each ``:=`` is its own block's variable, and which one a call sees
        is not read from the text, so the name gets no type."""
        body = (
            "\tif ok {\n\t\td := detect.NewDetector()\n\t\td.Scan()\n\t} else {\n"
            "\t\td := detect.NewOther()\n\t\td.Scan()\n\t}\n"
        )
        assert _go_method_edges(tmp_path, body) == []

    def test_a_chained_right_hand_side_is_not_the_head_s_type(self, tmp_path: Path) -> None:
        """``x`` holds ``Child``'s result, never ``NewDetector``'s."""
        body = "\tx := detect.NewDetector().Child()\n\tx.Scan()\n"
        targets = [e[1] for e in _go_method_edges(tmp_path, body)]
        assert "detect/detect.go::Detector::Scan" not in targets


    def test_a_declared_name_keeps_its_declaration_s_type(self, tmp_path: Path) -> None:
        """A call-typed ``:=`` of a name a literal already types adds nothing,
        so the existing reading is neither changed nor refused."""
        body = (
            "\tc := &detect.Other{}\n\tif ok {\n\t\tc := detect.NewOther()\n\t\t_ = c\n\t}\n"
            "\tc.Walk()\n"
        )
        targets = [e[1] for e in _go_method_edges(tmp_path, body)]
        assert targets == ["detect/detect.go::Other::Walk"]


    def test_a_callee_reached_by_a_repo_wide_guess_types_nothing(
        self, tmp_path: Path
    ) -> None:
        """``fs.Open`` on an interface lands on another package's ``Fs`` only
        by name; its return type must not seed a second guess."""
        parsed = _parse_all(
            tmp_path,
            {
                "app/fs.go": (
                    "go",
                    "package app\n\ntype Fs interface {\n\tOpen() File\n}\n\n"
                    "type File interface {\n\tClose() error\n}\n\n"
                    "func Read(fs Fs) {\n\tf := fs.Open()\n\tf.Close()\n}\n",
                ),
                "gcs/fs.go": (
                    "go",
                    "package gcs\n\ntype Fs struct{}\n\ntype GcsFile struct{}\n\n"
                    "func (fs *Fs) Open() *GcsFile { return nil }\n\n"
                    "func (f *GcsFile) Close() error { return nil }\n",
                ),
            },
        )
        edges = _edges(parsed, tmp_path)
        assert "gcs/fs.go::GcsFile::Close" not in [e[1] for e in edges]


    def test_a_method_in_a_sibling_file_of_the_type_s_package(self, tmp_path: Path) -> None:
        """Go puts methods in any file of the package, not only the type's."""
        parsed = _parse_all(
            tmp_path,
            {
                "detect/detect.go": (
                    "go",
                    "package detect\n\ntype Detector struct{}\n\n"
                    "func NewDetector() *Detector { return &Detector{} }\n",
                ),
                "detect/baseline.go": (
                    "go",
                    "package detect\n\nfunc (d *Detector) AddBaseline() error { return nil }\n",
                ),
                "cmd/run.go": (
                    "go",
                    'package cmd\n\nimport "example.com/app/detect"\n\n'
                    "func run() {\n\td := detect.NewDetector()\n\td.AddBaseline()\n}\n",
                ),
            },
        )
        _link_imports(parsed, {"cmd/run.go": {"example.com/app/detect": "detect/detect.go"}})
        edges = _edges(
            parsed,
            tmp_path,
            {
                "cmd/run.go": {"detect/detect.go"},
                "detect/detect.go": set(),
                "detect/baseline.go": set(),
            },
        )
        assert (
            "cmd/run.go::run",
            "detect/baseline.go::Detector::AddBaseline",
            0.88,
            "receiver_typed_import",
        ) in edges


    def test_a_same_named_type_in_another_package_does_not_answer(
        self, tmp_path: Path
    ) -> None:
        """``Open`` returns ``detect``'s own ``Store``; ``other.Store`` shares
        only the name, and the name is all a repo-wide lookup would see."""
        parsed = _parse_all(
            tmp_path,
            {
                "detect/store.go": (
                    "go",
                    "package detect\n\ntype Store interface {\n\tSave() int\n}\n\n"
                    "func Open() Store { return nil }\n",
                ),
                "other/store.go": (
                    "go",
                    "package other\n\ntype Store struct{}\n\n"
                    "func (s *Store) Save() int { return 1 }\n",
                ),
                "cmd/run.go": (
                    "go",
                    'package cmd\n\nimport "example.com/app/detect"\n\n'
                    "func run() {\n\ts := detect.Open()\n\ts.Save()\n}\n",
                ),
            },
        )
        _link_imports(parsed, {"cmd/run.go": {"example.com/app/detect": "detect/store.go"}})
        edges = _edges(
            parsed,
            tmp_path,
            {"cmd/run.go": {"detect/store.go"}, "detect/store.go": set(), "other/store.go": set()},
        )
        assert "other/store.go::Store::Save" not in [e[1] for e in edges]


class TestPythonTypedReceiver:
    """Python reaches the same strategy through its own declaration shapes."""

    def test_a_constructed_local_types_its_receiver(self, tmp_path: Path) -> None:
        parsed = _parse_all(
            tmp_path,
            {
                "run.py": (
                    "python",
                    "def run():\n    graph = DependencyGraph()\n    graph.add_arc(1)\n",
                ),
                "graph.py": (
                    "python",
                    "class DependencyGraph:\n    def add_arc(self, obj):\n        return obj\n",
                ),
            },
        )
        assert (
            "run.py::run",
            "graph.py::DependencyGraph::add_arc",
            0.75,
            "receiver_typed_global",
        ) in _edges(parsed, tmp_path)

    def test_an_annotated_parameter_types_its_receiver(self, tmp_path: Path) -> None:
        parsed = _parse_all(
            tmp_path,
            {
                "run.py": (
                    "python",
                    "def run(graph: DependencyGraph):\n    graph.add_arc(1)\n",
                ),
                "graph.py": (
                    "python",
                    "class DependencyGraph:\n    def add_arc(self, obj):\n        return obj\n",
                ),
            },
        )
        assert (
            "run.py::run",
            "graph.py::DependencyGraph::add_arc",
            0.75,
            "receiver_typed_global",
        ) in _edges(parsed, tmp_path)

    def test_a_python_class_attribute_never_types_a_bare_receiver(
        self, tmp_path: Path
    ) -> None:
        """A Python field is reached as ``self.graph``, never as ``graph``.

        So a bare receiver naming a class attribute is a different name, and
        binding the two would be a wrong edge rather than a missed one.
        """
        parsed = _parse_all(
            tmp_path,
            {
                "api.py": (
                    "python",
                    "class Api:\n"
                    "    graph: DependencyGraph\n"
                    "    def run(self):\n"
                    "        graph.add_arc(1)\n",
                ),
                "graph.py": (
                    "python",
                    "class DependencyGraph:\n    def add_arc(self, obj):\n        return obj\n",
                ),
            },
        )
        edges = _edges(parsed, tmp_path)
        assert not [e for e in edges if str(e[3]).startswith("receiver_field_")]
        assert not [e for e in edges if str(e[3]).startswith("receiver_typed_")]


class TestImportedTypeThroughAReExport:
    """An import binds the package, not the module that declares the type.

    ``from pkg import Engine`` names ``pkg/__init__.py``, which re-exports
    ``Engine`` and declares no method of it. Treating that as settling the type
    and then refusing costs every method call on it.
    """

    def _repo(self) -> dict[str, tuple[str, str]]:
        return {
            "app.py": (
                "python",
                "from pkg import Engine\n\n"
                "def run():\n"
                "    engine = Engine()\n"
                "    engine.render(1)\n",
            ),
            "pkg/__init__.py": ("python", "from pkg.engine import Engine\n"),
            "pkg/engine.py": (
                "python",
                "class Engine:\n    def render(self, obj):\n        return obj\n",
            ),
        }

    def test_a_re_exported_type_still_resolves_its_method(self, tmp_path: Path) -> None:
        parsed = _parse_all(tmp_path, self._repo())
        _link_imports(
            parsed,
            {
                "app.py": {"pkg": "pkg/__init__.py"},
                "pkg/__init__.py": {"pkg.engine": "pkg/engine.py"},
            },
        )
        assert (
            "app.py::run",
            "pkg/engine.py::Engine::render",
            0.88,
            "receiver_typed_import",
        ) in _edges(parsed, tmp_path)

    def test_a_bound_file_that_declares_the_pair_answers_directly(
        self, tmp_path: Path
    ) -> None:
        """The chain is a fallback, not a first choice.

        This is the shape the import tier always handled, and it had no test —
        so nothing pinned that the chase runs only after a direct hit misses.
        """
        parsed = _parse_all(
            tmp_path,
            {
                "app.py": (
                    "python",
                    "from pkg.engine import Engine\n\n"
                    "def run():\n"
                    "    engine = Engine()\n"
                    "    engine.render(1)\n",
                ),
                "pkg/engine.py": (
                    "python",
                    "class Engine:\n    def render(self, obj):\n        return obj\n",
                ),
            },
        )
        _link_imports(parsed, {"app.py": {"pkg.engine": "pkg/engine.py"}})
        assert (
            "app.py::run",
            "pkg/engine.py::Engine::render",
            0.88,
            "receiver_typed_import",
        ) in _edges(parsed, tmp_path)

    def test_a_re_export_chain_does_not_invent_a_method(self, tmp_path: Path) -> None:
        """Following the chain must not weaken the validator above it."""
        files = self._repo()
        files["app.py"] = (
            "python",
            "from pkg import Engine\n\n"
            "def run():\n"
            "    engine = Engine()\n"
            "    engine.missing(1)\n",
        )
        parsed = _parse_all(tmp_path, files)
        _link_imports(
            parsed,
            {
                "app.py": {"pkg": "pkg/__init__.py"},
                "pkg/__init__.py": {"pkg.engine": "pkg/engine.py"},
            },
        )
        assert not [
            e for e in _edges(parsed, tmp_path) if str(e[3]).startswith("receiver_typed_")
        ]

    def test_an_alias_does_not_read_the_bound_file_s_own_binding(
        self, tmp_path: Path
    ) -> None:
        """The chain is keyed by each file's own local names, so an alias must
        be translated back before it is used as a key.

        Here ``consumer`` calls ``lib.Renderer`` under the name ``Engine``,
        while ``lib`` itself binds that same name to an unrelated class. Asking
        ``lib`` about ``Engine`` answers about the wrong one.
        """
        parsed = _parse_all(
            tmp_path,
            {
                "consumer.py": (
                    "python",
                    "from lib import Renderer as Engine\n\n"
                    "def run():\n"
                    "    engine = Engine()\n"
                    "    engine.render(1)\n",
                ),
                "lib.py": (
                    "python",
                    "from other import Engine\n\nclass Renderer:\n    pass\n",
                ),
                "other.py": (
                    "python",
                    "class Engine:\n    def render(self, obj):\n        return obj\n",
                ),
            },
        )
        _link_imports(
            parsed,
            {"consumer.py": {"lib": "lib.py"}, "lib.py": {"other": "other.py"}},
        )
        assert not [
            e
            for e in _edges(parsed, tmp_path)
            if e[1] == "other.py::Engine::render" and str(e[3]).startswith("receiver_")
        ]

    def test_an_origin_outside_the_repo_is_never_recorded(self, tmp_path: Path) -> None:
        """A re-export map holding an unreadable path makes a reader look safe."""
        parsed = _parse_all(
            tmp_path,
            {
                "app.py": ("python", "from thirdparty import Engine\n\ndef run():\n    pass\n"),
            },
        )
        for imp in parsed["app.py"].imports:
            imp.resolved_file = "external:thirdparty"
        resolver = CallResolver(parsed, {p: set() for p in parsed}, repo_path=str(tmp_path))
        recorded = [
            origin
            for origins in resolver._barrel_origins.values()
            for origin in origins.values()
        ]
        assert not [o for o in recorded if o.startswith("external:")]


class TestForeignReceiverTypes:
    """The repo-wide receiver tier must not answer a call on a type we do not own.

    Both tests write the *same* call. Only the import differs, which is the
    whole claim: the name cannot decide this and the caller's import list can.
    """

    @staticmethod
    def _workspace(tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text(
            '[workspace]\nmembers = ["crates/a", "crates/b"]\n'
        )
        for name in ("a", "b"):
            crate = tmp_path / "crates" / name
            crate.mkdir(parents=True, exist_ok=True)
            (crate / "Cargo.toml").write_text(
                f'[package]\nname = "{name}"\nversion = "0.1.0"\n'
            )

    _DECLARES = (
        "rust",
        "pub struct HashMap;\n\nimpl HashMap {\n    pub fn new() -> Self {\n"
        "        HashMap\n    }\n}\n",
    )

    def test_a_std_named_receiver_gets_no_repo_wide_guess(self, tmp_path: Path) -> None:
        self._workspace(tmp_path)
        parsed = _parse_all(
            tmp_path,
            {
                "crates/a/src/lib.rs": self._DECLARES,
                "crates/b/src/lib.rs": (
                    "rust",
                    "use std::collections::HashMap;\n\npub fn run() {\n"
                    "    let m = HashMap::new();\n}\n",
                ),
            },
        )
        assert _edges(parsed, tmp_path) == []

    def test_the_same_call_resolves_when_the_file_rebinds_the_name(
        self, tmp_path: Path
    ) -> None:
        self._workspace(tmp_path)
        parsed = _parse_all(
            tmp_path,
            {
                "crates/a/src/lib.rs": self._DECLARES,
                "crates/b/src/lib.rs": (
                    "rust",
                    "use a::HashMap;\n\npub fn run() {\n"
                    "    let m = HashMap::new();\n}\n",
                ),
            },
        )
        assert (
            "crates/b/src/lib.rs::run",
            "crates/a/src/lib.rs::HashMap::new",
            0.75,
            "receiver_global",
        ) in _edges(parsed, tmp_path)

    def test_no_other_language_carries_the_set(self) -> None:
        """The refusal is a no-op elsewhere by construction, not by measurement."""
        from repowise.core.ingestion.language_data import get_external_receiver_types
        from repowise.core.ingestion.languages.registry import REGISTRY

        populated = {
            spec.tag
            for spec in REGISTRY.all_specs()
            if get_external_receiver_types(spec.tag)
        }
        assert populated == {"rust"}


class TestOwnerAwareModuleTier:
    """The module and import-name tiers pick an owner, not just a file.

    Both end in a lookup on ``_file_symbols``, which is flat and last-wins. A
    file declaring ``new`` on several types answered every ``Type::new()`` with
    whichever came last: the right file, the wrong owner.
    """

    def test_a_scoped_call_reaches_its_own_type_not_the_files_last(
        self, tmp_path: Path
    ) -> None:
        """Without the owner lookup this lands on ``Second::new``."""
        parsed = _parse_all(
            tmp_path,
            {
                "shapes.rs": (
                    "rust",
                    "pub struct First;\n"
                    "pub struct Second;\n"
                    "\n"
                    "impl First {\n"
                    "    pub fn new() -> First { First }\n"
                    "}\n"
                    "\n"
                    "impl Second {\n"
                    "    pub fn new() -> Second { Second }\n"
                    "}\n",
                ),
                "main.rs": (
                    "rust",
                    "use crate::shapes::First;\n"
                    "\n"
                    "pub fn run() -> First {\n"
                    "    First::new()\n"
                    "}\n",
                ),
            },
        )
        _link_imports(parsed, {"main.rs": {"crate::shapes::First": "shapes.rs"}})
        assert (
            "main.rs::run",
            "shapes.rs::First::new",
            0.88,
            "module_alias",
        ) in _edges(parsed, tmp_path)

    def test_a_module_qualifier_still_reaches_the_free_function(
        self, tmp_path: Path
    ) -> None:
        """``util::helper()`` names a module, and a module owns nothing."""
        parsed = _parse_all(
            tmp_path,
            {
                "util.rs": (
                    "rust",
                    "pub struct Thing;\n"
                    "\n"
                    "impl Thing {\n"
                    "    pub fn helper() -> u8 { 1 }\n"
                    "}\n"
                    "\n"
                    "pub fn helper() -> u8 { 2 }\n",
                ),
                "main.rs": (
                    "rust",
                    "use crate::util;\n"
                    "\n"
                    "pub fn run() -> u8 {\n"
                    "    util::helper()\n"
                    "}\n",
                ),
            },
        )
        _link_imports(parsed, {"main.rs": {"crate::util": "util.rs"}})
        assert (
            "main.rs::run",
            "util.rs::helper",
            0.88,
            "module_alias",
        ) in _edges(parsed, tmp_path)

    def test_a_python_module_alias_is_not_hijacked_by_a_method_of_that_name(
        self, tmp_path: Path
    ) -> None:
        """Strategy 1 carries the same lookup, and a real alias owns nothing."""
        parsed = _parse_all(
            tmp_path,
            {
                "models.py": (
                    "python",
                    "class Widget:\n"
                    "    def build(self):\n"
                    "        return 2\n"
                    "\n"
                    "\n"
                    "def build():\n"
                    "    return 1\n",
                ),
                "caller.py": (
                    "python",
                    "import models\n\ndef run():\n    return models.build()\n",
                ),
            },
        )
        _link_imports(parsed, {"caller.py": {"models": "models.py"}})
        assert (
            "caller.py::run",
            "models.py::build",
            0.88,
            "module_alias",
        ) in _edges(parsed, tmp_path)
