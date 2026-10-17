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

from dataclasses import replace
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

    def test_jvm_bare_call_does_not_reach_a_sibling_classes_member(
        self, tmp_path: Path
    ) -> None:
        """``copy(..)`` in a lambda is its receiver's, not a sibling data class's."""
        parsed = _parse_all(
            tmp_path,
            {
                "src/p/Query.kt": ("kotlin", "package p\n\ndata class Query(val a: Int)\n"),
                "src/p/Other.kt": ("kotlin", "package p\n\ndata class Other(val b: Int)\n"),
                "src/p/Repo.kt": (
                    "kotlin",
                    "package p\n\nfun sync(v: Versions) {\n    v.update { copy(a = 1) }\n}\n",
                ),
            },
        )
        assert not [e for e in _edges(parsed, tmp_path) if e[1].endswith("::copy")]

    def test_jvm_bare_call_reaches_a_top_level_function_a_later_member_shares(
        self, tmp_path: Path
    ) -> None:
        """A sibling's member indexed after its top-level ``load`` does not hide it."""
        parsed = _parse_all(
            tmp_path,
            {
                "src/p/Util.kt": (
                    "kotlin",
                    "package p\n\nfun load() {}\n\nclass Cache {\n    fun load() {}\n}\n",
                ),
                "src/p/Main.kt": ("kotlin", "package p\n\nfun main() {\n    load()\n}\n"),
            },
        )
        assert ("src/p/Main.kt::main", "src/p/Util.kt::load", 0.90, "same_package") in _edges(
            parsed, tmp_path
        )

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

    def test_a_kotlin_constructor_property_types_its_receiver(self, tmp_path: Path) -> None:
        """Hilt's `@Inject constructor(private val api: Api)`, read bare and through `this`."""
        parsed = _parse_all(
            tmp_path,
            {
                "app/Repo.kt": (
                    "kotlin",
                    "package app\n\nclass Repo @Inject constructor(\n"
                    "    private val api: Api,\n    val dao: Dao\n) {\n"
                    "    fun load() {\n        api.fetch()\n        this.dao.read()\n    }\n}\n",
                ),
                "app/Api.kt": (
                    "kotlin",
                    "package app\n\nclass Api {\n    fun fetch() {}\n}\n"
                    "class Dao {\n    fun read() {}\n}\n",
                ),
            },
        )
        edges = _edges(parsed, tmp_path)
        for callee in ("app/Api.kt::Api::fetch", "app/Api.kt::Dao::read"):
            assert ("app/Repo.kt::Repo::load", callee, 0.90, "receiver_field_same_package") in edges

    def test_a_swift_stored_property_types_its_receiver(self, tmp_path: Path) -> None:
        """An init-assigned `let`, read bare and through `self`."""
        parsed = _parse_all(
            tmp_path,
            {
                "Repo.swift": (
                    "swift",
                    "final class Repo {\n    private let api: Api\n    var dao: Dao\n\n"
                    "    init(api: Api, dao: Dao) {\n        self.api = api\n"
                    "        self.dao = dao\n    }\n\n"
                    "    func load() {\n        api.fetch()\n        self.dao.read()\n    }\n}\n",
                ),
                "Api.swift": (
                    "swift",
                    "final class Api {\n    func fetch() {}\n}\n"
                    "final class Dao {\n    func read() {}\n}\n",
                ),
            },
        )
        edges = _edges(parsed, tmp_path)
        for callee in ("Api.swift::Api::fetch", "Api.swift::Dao::read"):
            assert ("Repo.swift::Repo::load", callee, 0.75, "receiver_field_global") in edges

    @pytest.mark.parametrize(
        ("rel", "lang", "source", "method"),
        [
            (
                "Repo.kt",
                "kotlin",
                "class Repo(private val api: Api) {\n"
                "    fun a() { api.nothing() }\n"
                "    fun b(items: List<X>) { items.forEach { api -> api.nothing() } }\n"
                "    fun c() { val api = make()\n        api.nothing() }\n}\n"
                "class Api { fun nothing() {} }\n",
                "nothing",
            ),
            (
                "Repo.swift",
                "swift",
                "final class Repo {\n    private let api: Api\n"
                "    func a() { api.nothing() }\n"
                "    func b(items: [X]) { items.forEach { api in api.nothing() } }\n"
                "    func c() { let api = make()\n        api.nothing() }\n}\n"
                "final class Api { func nothing() {} }\n",
                "nothing",
            ),
            (
                "Repo.java",
                "java",
                "class Repo {\n    private final Api api = new Api();\n"
                "    void a() { api.nothing(); }\n"
                "    void b(List<X> items) { items.forEach(api -> api.nothing()); }\n"
                "    void c() { var api = make(); api.nothing(); }\n}\n"
                "class Api { void nothing() {} }\n",
                "nothing",
            ),
            (
                "Repo.cs",
                "csharp",
                "class Repo {\n    private readonly Api api;\n"
                "    void A() { api.Nothing(); }\n"
                "    void B(List<X> items) { items.ForEach(api => api.Nothing()); }\n"
                "    void C() { var api = Make(); api.Nothing(); }\n}\n"
                "class Api { public void Nothing() {} }\n",
                "Nothing",
            ),
            (
                "lib/repo.dart",
                "dart",
                "class Repo {\n  final Api api;\n  Repo(this.api);\n"
                "  void a() { api.nothing(); }\n"
                "  void b(List<X> items) { items.map((api) => api.nothing()); }\n"
                "  void c() { final api = make();\n    api.nothing(); }\n}\n"
                "class Api { void nothing() {} }\n",
                "nothing",
            ),
        ],
    )
    def test_an_untyped_local_or_lambda_parameter_shadows_the_field(
        self, tmp_path: Path, rel: str, lang: str, source: str, method: str
    ) -> None:
        """`{ api -> api.m() }` names the lambda's parameter, not the field.

        Only the field origin is asserted: Java's same-file name tier still
        binds `api.nothing()` by name alone, which is not this scope's doing.
        """
        edges = _edges(_parse_all(tmp_path, {rel: (lang, source)}), tmp_path)
        callers = {
            e[0].rsplit("::", 1)[-1]
            for e in edges
            if e[1] == f"{rel}::Api::{method}" and e[3].startswith("receiver_field_")
        }
        assert callers == {"a" if lang != "csharp" else "A"}

    def test_a_swift_optional_binding_of_the_field_keeps_it(self, tmp_path: Path) -> None:
        """`guard let client else` unwraps the field itself, not a new local."""
        source = (
            "final class Repo {\n    var client: Api?\n"
            "    func a() { guard let client else { return }\n        client.nothing() }\n"
            "    func b() { if let client = self.client { client.nothing() } }\n}\n"
            "final class Api { func nothing() {} }\n"
        )
        edges = _edges(_parse_all(tmp_path, {"Repo.swift": ("swift", source)}), tmp_path)
        callers = {e[0] for e in edges if e[3].startswith("receiver_field_")}
        assert callers == {"Repo.swift::Repo::a", "Repo.swift::Repo::b"}

    def test_a_dart_field_and_local_type_their_receivers(self, tmp_path: Path) -> None:
        """A field set by an initialising formal, and a `final x = Foo()` local."""
        parsed = _parse_all(
            tmp_path,
            {
                "lib/repo.dart": (
                    "dart",
                    "import 'api.dart';\n\nclass Repo {\n  final Api api;\n  Repo(this.api);\n\n"
                    "  void load() {\n    api.fetch();\n    final store = Store();\n"
                    "    store.get();\n    api.missing();\n  }\n}\n",
                ),
                "lib/api.dart": (
                    "dart",
                    "class Api {\n  void fetch() {}\n}\nclass Store {\n  void get() {}\n}\n",
                ),
            },
        )
        _link_imports(parsed, {"lib/repo.dart": {"api.dart": "lib/api.dart"}})
        edges = _edges(parsed, tmp_path, {"lib/repo.dart": {"lib/api.dart"}, "lib/api.dart": set()})
        caller = "lib/repo.dart::Repo::load"
        assert (caller, "lib/api.dart::Api::fetch", 0.88, "receiver_field_import") in edges
        assert (caller, "lib/api.dart::Store::get", 0.88, "receiver_typed_import") in edges
        assert not [e for e in edges if e[1].endswith("::missing")]

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


class TestRepoWideReceiverTier:
    """The global tier answers only for the one type of a name.

    Two projects each declaring a ``MainWindow`` are told apart by nothing
    that tier sees, so a call naming neither one's namespace gets no edge
    rather than whichever file sorts first.
    """

    _CALLER = "peek/Peek.UITests/PeekTests.cs"
    _RESIZER = (
        "csharp",
        "namespace ImageResizer.Views\n{\n    public partial class MainWindow\n    {\n"
        "        public void Show() { }\n    }\n}\n",
    )
    _PEEK = (
        "csharp",
        "namespace Peek.UI\n{\n    public partial class MainWindow\n    {\n"
        "        public void Show() { }\n    }\n}\n",
    )

    @staticmethod
    def _test(usings: str = "") -> tuple[str, str]:
        return (
            "csharp",
            f"{usings}namespace Peek.UITests\n{{\n    public class PeekTests\n    {{\n"
            "        public void Opens()\n        {\n"
            "            var w = new MainWindow();\n            w.Show();\n        }\n    }\n}\n",
        )

    def _resolved(
        self,
        tmp_path: Path,
        files: dict[str, tuple[str, str]],
        method: str = "Show",
        imports: dict[str, dict[str, str]] | None = None,
        **resolver_kwargs: object,
    ) -> list[tuple[str, str]]:
        """``(callee, origin)`` for every edge onto a method named *method*."""
        parsed = _parse_all(tmp_path, files)
        targets: dict[str, set[str]] = {p: set() for p in parsed}
        for path, links in (imports or {}).items():
            _link_imports(parsed, {path: links})
            targets[path] = set(links.values())
        resolver = CallResolver(parsed, targets, repo_path=str(tmp_path), **resolver_kwargs)
        return sorted(
            (rc.callee_id, rc.origin)
            for path, pf in parsed.items()
            for rc in resolver.resolve_file(path, pf.calls)
            if rc.callee_id.endswith(f"::{method}")
        )

    def test_two_same_named_types_declaring_the_method_give_no_edge(
        self, tmp_path: Path
    ) -> None:
        files = {
            "resizer/MainWindow.xaml.cs": self._RESIZER,
            "peek/Peek.UI/MainWindow.xaml.cs": self._PEEK,
            self._CALLER: self._test(),
        }
        assert self._resolved(tmp_path, files) == []

    def test_a_same_named_type_that_only_inherits_the_method_gives_no_edge(
        self, tmp_path: Path
    ) -> None:
        """One declaration is not one candidate: the other ``MainWindow`` may be meant."""
        inherits = (
            "csharp",
            "namespace Peek.UI\n{\n    public partial class MainWindow : Window\n    {\n"
            "        public void Close() { }\n    }\n}\n",
        )
        files = {
            "resizer/MainWindow.xaml.cs": self._RESIZER,
            "peek/Peek.UI/MainWindow.xaml.cs": inherits,
            self._CALLER: self._test(),
        }
        assert self._resolved(tmp_path, files) == []

    def test_the_one_type_of_the_name_keeps_its_edge(self, tmp_path: Path) -> None:
        files = {"resizer/MainWindow.xaml.cs": self._RESIZER, self._CALLER: self._test()}
        assert self._resolved(tmp_path, files) == [
            ("resizer/MainWindow.xaml.cs::MainWindow::Show", "receiver_typed_global")
        ]

    def test_the_fragments_of_one_partial_type_count_once(self, tmp_path: Path) -> None:
        crop = (
            "csharp",
            "namespace ImageResizer.Views\n{\n    public partial class MainWindow\n    {\n"
            "        public void Crop() { }\n    }\n}\n",
        )
        files = {
            "resizer/MainWindow.xaml.cs": self._RESIZER,
            "resizer/MainWindow.Crop.cs": crop,
            self._CALLER: self._test(),
        }
        fragments = ("resizer/MainWindow.Crop.cs", "resizer/MainWindow.xaml.cs")
        assert self._resolved(
            tmp_path, files, partial_fragments={(f, "MainWindow"): fragments for f in fragments}
        ) == [("resizer/MainWindow.xaml.cs::MainWindow::Show", "receiver_typed_global")]

    def test_a_using_that_names_the_namespace_keeps_the_edge(self, tmp_path: Path) -> None:
        files = {
            "resizer/MainWindow.xaml.cs": self._RESIZER,
            "peek/Peek.UI/MainWindow.xaml.cs": self._PEEK,
            self._CALLER: self._test("using ImageResizer.Views;\n"),
        }
        imports = {self._CALLER: {"ImageResizer.Views": "resizer/MainWindow.xaml.cs"}}
        assert self._resolved(tmp_path, files, imports=imports) == [
            ("resizer/MainWindow.xaml.cs::MainWindow::Show", "receiver_typed_import")
        ]

    def test_csharp_arity_tells_a_generic_from_its_plain_twin(self, tmp_path: Path) -> None:
        """``Policy`` and ``Policy<T>`` are two types; the receiver's arity picks one."""
        files = {
            "src/Policy.cs": (
                "csharp",
                "namespace P\n{\n    public partial class Policy\n    {\n"
                "        public static void Handle() { }\n"
                "        public void Show() { }\n    }\n}\n",
            ),
            "src/PolicyT.cs": (
                "csharp",
                "namespace P\n{\n    public partial class Policy<TResult>\n    {\n"
                "        public void Show() { }\n    }\n}\n",
            ),
            "src/Use.cs": (
                "csharp",
                "namespace P\n{\n    public class Use\n    {\n"
                "        public void Run(Policy<int> g, Policy p)\n        {\n"
                "            Policy.Handle();\n            g.Show();\n"
                "            p.Show();\n        }\n    }\n}\n",
            ),
        }
        assert self._resolved(tmp_path, files) == [
            ("src/Policy.cs::Policy::Show", "receiver_typed_global"),
            ("src/PolicyT.cs::Policy::Show", "receiver_typed_global"),
        ]
        assert self._resolved(tmp_path, files, "Handle") == [
            ("src/Policy.cs::Policy::Handle", "receiver_global")
        ]

    def test_python_classes_sharing_a_name_give_no_edge(self, tmp_path: Path) -> None:
        graph = "class DependencyGraph:\n    def add_arc(self, obj):\n        return obj\n"
        files = {
            "run.py": ("python", "def run():\n    graph = DependencyGraph()\n    graph.add_arc(1)\n"),
            "a/graph.py": ("python", graph),
            "b/graph.py": ("python", graph),
        }
        assert self._resolved(tmp_path, files, "add_arc") == []


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

    def test_the_refusal_holds_over_a_bare_name_fallback(self, tmp_path: Path) -> None:
        """A refused receiver is an answer, not a miss: the bare tiers never see it."""
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
        caller = parsed["crates/b/src/lib.rs"]
        caller.calls = [replace(c, bare_name_fallback=True) for c in caller.calls]
        assert [c.receiver_name for c in caller.calls] == ["HashMap"]
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


class TestInheritedTypedReceiver:
    """A typed receiver whose type lacks the method reaches the one ancestor declaring it."""

    _PKG = "src/org/acme/"
    _BASE = (
        "package org.acme;\npublic class Base {\n"
        "    public Object get(int i) { return null; }\n}\n"
    )
    _CHILD = "package org.acme;\npublic class Child extends Base {}\n"
    _GET = "src/org/acme/Base.java::Base::get"

    @staticmethod
    def _caller(head: str = "package org.acme;\n", cls: str = "Caller", method: str = "") -> str:
        return (
            f"{head}public class {cls} {{\n"
            f"    public {method}Object run(Child c) {{\n        return c.get(0);\n    }}\n}}\n"
        )

    def _targets(
        self,
        tmp_path: Path,
        files: dict[str, str],
        parents: dict[str, set[str]] | None = None,
        language: str = "java",
    ) -> list[str]:
        """Callees of ``Caller``'s calls; *parents* stands in for the heritage phase.

        A Java file with no ``package`` line is put in ``org.acme``.
        """
        if language == "java":
            files = {"Base.java": self._BASE, "Child.java": self._CHILD, **files}
            files = {
                k: v if v.startswith("package") else "package org.acme;\n" + v
                for k, v in files.items()
            }
        parsed = _parse_all(tmp_path, {k: (language, v) for k, v in files.items()})
        _link_imports(parsed, {k: {"org.acme.Child": "Child.java"} for k in parsed})
        resolver = CallResolver(
            parsed,
            {k: set() for k in parsed},
            repo_path=str(tmp_path),
            heritage_parents=parents or {"Child.java::Child": {"Base.java::Base"}},
        )
        caller = next(k for k in files if "Caller" in k)
        return [rc.callee_id for rc in resolver.resolve_file(caller, parsed[caller].calls)]

    def test_an_inherited_method_resolves_to_the_ancestor(self, tmp_path: Path) -> None:
        files = {"Base.java": self._BASE, "Child.java": self._CHILD, "Caller.java": self._caller()}
        parsed = _parse_all(tmp_path, {self._PKG + k: ("java", v) for k, v in files.items()})
        resolver = CallResolver(
            parsed,
            {k: set() for k in parsed},
            repo_path=str(tmp_path),
            heritage_parents={self._PKG + "Child.java::Child": {self._PKG + "Base.java::Base"}},
        )
        caller = self._PKG + "Caller.java"
        assert [
            (rc.caller_id, rc.callee_id, rc.confidence, rc.origin)
            for rc in resolver.resolve_file(caller, parsed[caller].calls)
        ] == [(caller + "::Caller::run", self._GET, 0.75, "receiver_typed_global")]

    def test_the_nearest_superclass_declaration_wins(self, tmp_path: Path) -> None:
        mid = "public class Mid extends Base {\n    public Object get(int i) { return 1; }\n}\n"
        iface = "public interface Getter {\n    Object get(int i);\n}\n"
        child = "public class Child extends Mid implements Getter {}\n"
        parents = {
            "Child.java::Child": {"Mid.java::Mid", "Getter.java::Getter"},
            "Mid.java::Mid": {"Base.java::Base"},
        }
        files = {"Mid.java": mid, "Getter.java": iface, "Child.java": child}
        files["Caller.java"] = self._caller("")
        assert self._targets(tmp_path, files, parents) == ["Mid.java::Mid::get"]

    def test_two_declaring_interfaces_give_no_edge(self, tmp_path: Path) -> None:
        files = {
            "A.java": "public interface A {\n    Object get(int i);\n}\n",
            "B.java": "public interface B {\n    Object get(int i);\n}\n",
            "Child.java": "public interface Child extends A, B {}\n",
            "Caller.java": self._caller(""),
        }
        parents = {"Child.java::Child": {"A.java::A", "B.java::B"}}
        assert self._targets(tmp_path, files, parents) == []

    @pytest.mark.parametrize(
        "head",
        [
            pytest.param("package q;\nimport com.vendor.*;\n", id="vendor-wildcard"),
            pytest.param("package q;\n", id="other-package-no-import"),
        ],
    )
    def test_a_type_the_caller_cannot_see_gives_no_edge(self, tmp_path: Path, head: str) -> None:
        base = "package p;\n" + self._BASE.partition("\n")[2]
        child = "package p;\n" + self._CHILD.partition("\n")[2]
        files = {"Base.java": base, "Child.java": child, "Caller.java": self._caller(head)}
        assert self._targets(tmp_path, files) == []

    def test_a_single_type_import_makes_it_visible(self, tmp_path: Path) -> None:
        files = {"Caller.java": self._caller("package q;\nimport org.acme.Child;\n")}
        assert self._targets(tmp_path, files) == ["Base.java::Base::get"]

    @pytest.mark.parametrize(
        ("cls", "method"),
        [("Caller<Child>", ""), ("Caller", "<Child extends Comparable<Child>> ")],
        ids=["class-parameter", "method-parameter"],
    )
    def test_a_type_parameter_shadows_the_repo_type(
        self, tmp_path: Path, cls: str, method: str
    ) -> None:
        files = {"Caller.java": self._caller("", cls, method)}
        assert self._targets(tmp_path, files) == []

    def test_kotlin_does_not_walk_ancestors(self, tmp_path: Path) -> None:
        files = {
            "Base.kt": "open class Base {\n    fun get(i: Int): Any? = null\n}\n",
            "Child.kt": "class Child : Base()\n",
            "Caller.kt": "fun run(c: Child): Any? {\n    return c.get(0)\n}\n",
        }
        parents = {"Child.kt::Child": {"Base.kt::Base"}}
        assert self._targets(tmp_path, files, parents, language="kotlin") == []


class TestJavaReceiverRecall:
    """Java receivers the typed tiers missed: an inherited field, ``super``, a cast."""

    def _targets(
        self, tmp_path: Path, body: str, head: str = "", members: str = "", mid: str | None = None
    ) -> list[tuple[str, str]]:
        """Callees of ``Sub``'s calls; a *mid* body puts ``b.Mid`` between ``Sub`` and ``Base``."""
        parent = "Base" if mid is None else "Mid"
        files = {
            "a/Base.java": "package a;\npublic class Base {\n    protected Reader reader;\n"
            "    protected Doc doc;\n    public void m() {}\n}\n",
            "a/Reader.java": "package a;\npublic class Reader {\n    public void consume() {}\n}\n",
            "a/Node.java": "package a;\npublic class Node {\n    public void append() {}\n}\n",
            "a/Doc.java": "package a;\npublic class Doc extends Node {}\n",
            "b/Reader.java": "package b;\npublic class Reader {\n    public void consume() {}\n}\n",
            "b/Mid.java": f"package b;\nimport a.Base;\npublic class Mid extends Base {{\n{mid}\n}}\n",
            "b/Sub.java": f"package b;\nimport a.Base;\n{head}public class Sub extends {parent} {{\n"
            f"{members}\n    public void m() {{}}\n    void go(Object o) {{\n        {body}\n    }}\n}}\n",
        }
        parsed = _parse_all(tmp_path, {k: ("java", v) for k, v in files.items()})
        _link_imports(parsed, {"b/Sub.java": {"a.Base": "a/Base.java", "a.Node": "a/Node.java"}})
        parents = {"b/Mid.java::Mid": {"a/Base.java::Base"}, "a/Doc.java::Doc": {"a/Node.java::Node"}}
        parents["b/Sub.java::Sub"] = {"a/Base.java::Base" if mid is None else "b/Mid.java::Mid"}
        resolver = CallResolver(
            parsed, {k: set() for k in parsed}, repo_path=str(tmp_path), heritage_parents=parents
        )
        calls = parsed["b/Sub.java"].calls
        return [(rc.callee_id, rc.origin) for rc in resolver.resolve_file("b/Sub.java", calls)]

    def test_an_inherited_field_is_typed_in_the_declaring_file(self, tmp_path: Path) -> None:
        """``Reader`` is ``a.Reader`` where ``Base`` declares it, not the caller's ``b.Reader``."""
        assert self._targets(tmp_path, "reader.consume();") == [
            ("a/Reader.java::Reader::consume", "receiver_field_same_package")
        ]

    def test_an_inherited_field_reaches_its_types_ancestor(self, tmp_path: Path) -> None:
        assert self._targets(tmp_path, "doc.append();") == [
            ("a/Node.java::Node::append", "receiver_field_global")
        ]

    def test_a_local_shadows_an_inherited_field(self, tmp_path: Path) -> None:
        assert self._targets(tmp_path, "Object reader = null; reader.consume();") == []

    def test_an_untyped_lambda_parameter_shadows_an_inherited_field(self, tmp_path: Path) -> None:
        assert self._targets(tmp_path, "run(reader -> reader.consume());") == []

    @pytest.mark.parametrize(
        ("body", "members"),
        [
            ("b.Reader reader = null; reader.consume();", ""),
            ("var reader = 1; reader.consume();", ""),
            ("", "    void f(b.Reader reader) { reader.consume(); }"),
            ("reader.consume();", "    b.Reader reader;"),
            ("reader.consume();", "    java.util.List<String> reader;"),
            ("reader.consume();", "    int[] reader;"),
        ],
        ids=["qualified-local", "var-local", "qualified-parameter", "qualified-field",
             "qualified-generic-field", "primitive-array-field"],
    )
    def test_a_name_declared_with_an_unread_type_shadows_an_inherited_field(
        self, tmp_path: Path, body: str, members: str
    ) -> None:
        assert self._targets(tmp_path, body, members=members) == []

    def test_the_nearest_superclass_field_hides_the_rest(self, tmp_path: Path) -> None:
        """``Mid``'s ``reader`` is the package-``b`` one, wherever ``Base`` declares another."""
        mid = "    protected Reader reader;"
        assert self._targets(tmp_path, "reader.consume();", mid=mid) == [
            ("b/Reader.java::Reader::consume", "receiver_field_same_package")
        ]

    def test_a_nearer_field_with_an_unread_type_stops_the_walk(self, tmp_path: Path) -> None:
        mid = "    protected b.Reader reader;"
        assert self._targets(tmp_path, "reader.consume();", mid=mid) == []

    def test_super_skips_the_callers_own_override(self, tmp_path: Path) -> None:
        assert self._targets(tmp_path, "super.m();") == [("a/Base.java::Base::m", "self_inherited")]

    @pytest.mark.parametrize("cast", ["Node", "Node<String>"])
    def test_a_cast_names_its_receiver_type(self, tmp_path: Path, cast: str) -> None:
        body = f"(({cast}) o).append();"
        assert self._targets(tmp_path, body, "import a.Node;\n") == [
            ("a/Node.java::Node::append", "module_alias")
        ]

    def test_a_scoped_cast_type_is_not_captured(self, tmp_path: Path) -> None:
        source = "class Use {\n    void go(Object o) {\n        ((a.Node) o).append();\n    }\n}\n"
        parsed = _parse_all(tmp_path, {"Use.java": ("java", source)})
        assert [c.receiver_name for c in parsed["Use.java"].calls] == [None]

    def test_an_intersection_cast_names_no_receiver(self, tmp_path: Path) -> None:
        """``(Node & Runnable) o`` names two types; taking either one would guess."""
        source = (
            "class Use {\n    void go(Object o) {\n        ((Node & Runnable) o).append();\n"
            "        ((Node) o).append();\n    }\n}\n"
        )
        parsed = _parse_all(tmp_path, {"Use.java": ("java", source)})
        assert sorted(
            (c.line, c.receiver_name or "") for c in parsed["Use.java"].calls
        ) == [(3, ""), (4, "Node")]

    def test_a_qualified_super_reaches_the_named_superinterface(self, tmp_path: Path) -> None:
        """``I.super.m()`` is ``I``'s default ``m``, not the superclass's."""
        files = {
            "a/Base.java": "package a;\npublic class Base {\n    public void m() {}\n}\n",
            "a/I.java": "package a;\npublic interface I {\n    default void m() {}\n}\n",
            "a/Sub.java": "package a;\npublic class Sub extends Base implements I {\n"
            "    public void m() {\n        I.super.m();\n    }\n}\n",
        }
        parsed = _parse_all(tmp_path, {k: ("java", v) for k, v in files.items()})
        parents = {"a/Sub.java::Sub": {"a/Base.java::Base", "a/I.java::I"}}
        resolver = CallResolver(
            parsed, {k: set() for k in parsed}, repo_path=str(tmp_path), heritage_parents=parents
        )
        calls = parsed["a/Sub.java"].calls
        assert [(rc.callee_id, rc.origin) for rc in resolver.resolve_file("a/Sub.java", calls)] == [
            ("a/I.java::I::m", "self_inherited")
        ]

    def test_a_qualified_super_reaches_the_qualifiers_superclass(self, tmp_path: Path) -> None:
        members = "    class In {\n        void q() { Sub.super.m(); }\n    }"
        assert self._targets(tmp_path, "", members=members) == [
            ("a/Base.java::Base::m", "self_inherited")
        ]


class TestJavaMemberTypes:
    """A member type shadows a same-named import or package type, as Java scopes it.

    ``p.Token`` nests ``Tag`` and ``Comment``; ``p.Tag`` and ``n.Comment`` are
    top-level types of the same simple names.
    """

    _TOKEN = (
        "package p;\nabstract class Token {\n"
        "    static abstract class Tag extends Token {\n"
        "        Tag reset() { return this; }\n        String name() { return null; }\n    }\n"
        "    static final class StartTag extends Tag {}\n"
        "    static final class Comment extends Token {\n"
        "        String getData() { return null; }\n    }\n}\n"
    )

    def _targets(self, tmp_path: Path, source: str) -> list[tuple[str, str]]:
        files = {
            "p/Token.java": self._TOKEN,
            "p/Tag.java": "package p;\npublic class Tag {\n    public String name() { return null; }\n}\n",
            "n/Comment.java": "package n;\npublic class Comment {\n"
            "    public String getData() { return null; }\n}\n",
            "p/Use.java": source,
        }
        parsed = _parse_all(tmp_path, {k: ("java", v) for k, v in files.items()})
        _link_imports(parsed, {"p/Use.java": {"n.Comment": "n/Comment.java"}})
        token, tag = "p/Token.java::Token", "p/Token.java::Token::Tag"
        parents = {tag: {token}, f"{token}::StartTag": {tag}, f"{token}::Comment": {token}}
        if "extends Token" in source:
            parents["p/Use.java::Use"] = {token}
        resolver = CallResolver(
            parsed, {k: set() for k in parsed}, repo_path=str(tmp_path), heritage_parents=parents
        )
        calls = parsed["p/Use.java"].calls
        return sorted((rc.callee_id, rc.origin) for rc in resolver.resolve_file("p/Use.java", calls))

    def test_a_qualified_type_names_the_member_type(self, tmp_path: Path) -> None:
        """``Token.Tag`` is not the package's top-level ``Tag``, nor ``Token.Comment`` the import."""
        source = (
            "package p;\nimport n.Comment;\nclass Use {\n    Token.Tag pending;\n"
            "    void go(Token.Comment c) {\n        pending.name();\n        c.getData();\n    }\n}\n"
        )
        assert self._targets(tmp_path, source) == [
            ("p/Token.java::Comment::getData", "receiver_typed_same_package"),
            ("p/Token.java::Tag::name", "receiver_field_same_package"),
        ]

    def test_an_inherited_member_type_shadows_an_import(self, tmp_path: Path) -> None:
        source = (
            "package p;\nimport n.Comment;\nclass Use extends Token {\n"
            "    void go(Comment c) {\n        c.getData();\n    }\n}\n"
        )
        assert self._targets(tmp_path, source) == [
            ("p/Token.java::Comment::getData", "receiver_typed_same_package")
        ]

    def test_a_return_type_is_read_where_its_method_declares_it(self, tmp_path: Path) -> None:
        """``reset()`` returns ``Tag`` inside ``Token``, so its ``name()`` is the member's."""
        source = (
            "package p;\nclass Use {\n    void go(Token.StartTag t) {\n"
            "        t.reset().name();\n    }\n}\n"
        )
        assert self._targets(tmp_path, source) == [
            ("p/Token.java::Tag::name", "return_type_same_package"),
            ("p/Token.java::Tag::reset", "receiver_typed_same_package"),
        ]

    def test_a_top_level_type_answers_where_no_member_shadows_it(self, tmp_path: Path) -> None:
        source = (
            "package p;\nimport n.Comment;\nclass Use {\n"
            "    void go(Tag t, Comment c) {\n        t.name();\n        c.getData();\n    }\n}\n"
        )
        assert self._targets(tmp_path, source) == [
            ("n/Comment.java::Comment::getData", "receiver_typed_import"),
            ("p/Tag.java::Tag::name", "receiver_typed_same_package"),
        ]

    def test_a_type_parameter_shadows_a_member_type(self, tmp_path: Path) -> None:
        source = (
            "package p;\nimport n.Comment;\nclass Use extends Token {\n"
            "    <Comment> void go(Comment c) {\n        c.getData();\n    }\n}\n"
        )
        assert ("p/Token.java::Comment::getData", "receiver_typed_same_package") not in (
            self._targets(tmp_path, source)
        )

    @pytest.mark.parametrize(
        ("outer", "inner", "returns"),
        [
            ("class Outer", "public static class Inner", "q.Outer.Inner"),
            ("public class Outer", "private static class Inner", "q.Outer.Inner"),
            ("public class Outer", "static class Inner", "Outer.Inner"),
        ],
        ids=["package-private-top-level", "private-member", "package-private-member"],
    )
    def test_a_qualified_type_the_caller_cannot_access_is_not_resolved(
        self, tmp_path: Path, outer: str, inner: str, returns: str
    ) -> None:
        """Java's access rules apply to a qualified spelling as they do to a plain name."""
        files = {
            "q/Outer.java": f"package q;\n{outer} {{\n"
            f"    {inner} {{\n        public void z() {{}}\n    }}\n}}\n",
            "x/Factory.java": "package x;\nimport q.Outer;\npublic class Factory {\n"
            f"    public static {returns} make() {{ return null; }}\n}}\n",
            "a/Use.java": "package a;\nimport x.Factory;\nclass Use {\n"
            "    void go() {\n        Factory.make().z();\n    }\n}\n",
        }
        parsed = _parse_all(tmp_path, {k: ("java", v) for k, v in files.items()})
        _link_imports(parsed, {"a/Use.java": {"x.Factory": "x/Factory.java"}})
        _link_imports(parsed, {"x/Factory.java": {"q.Outer": "q/Outer.java"}})
        resolver = CallResolver(parsed, {k: set() for k in parsed}, repo_path=str(tmp_path))
        calls = parsed["a/Use.java"].calls
        assert "q/Outer.java::Inner::z" not in {
            rc.callee_id for rc in resolver.resolve_file("a/Use.java", calls)
        }

    def test_a_package_qualified_return_type_names_its_package_type(self, tmp_path: Path) -> None:
        """``p.Outer.Inner make()`` is ``p.Outer``'s ``Inner``, not the ``q.Inner`` imported."""
        files = {
            "p/Outer.java": "package p;\npublic class Outer {\n"
            "    public static class Inner {\n        public void z() {}\n    }\n}\n",
            "q/Inner.java": "package q;\npublic class Inner {\n    public void z() {}\n}\n",
            "x/Factory.java": "package x;\nimport q.Inner;\npublic class Factory {\n"
            "    public static p.Outer.Inner make() { return null; }\n}\n",
            "a/Use.java": "package a;\nimport q.Inner;\nimport x.Factory;\nclass Use {\n"
            "    void go() {\n        Factory.make().z();\n    }\n}\n",
        }
        parsed = _parse_all(tmp_path, {k: ("java", v) for k, v in files.items()})
        imports = {"q.Inner": "q/Inner.java", "x.Factory": "x/Factory.java"}
        _link_imports(parsed, {"a/Use.java": imports, "x/Factory.java": imports})
        resolver = CallResolver(parsed, {k: set() for k in parsed}, repo_path=str(tmp_path))
        calls = parsed["a/Use.java"].calls
        assert "p/Outer.java::Inner::z" in {
            rc.callee_id for rc in resolver.resolve_file("a/Use.java", calls)
        }

    @pytest.mark.parametrize(
        ("modifier", "inherited"),
        [("private ", False), ("", False), ("protected ", True)],
        ids=["private", "package-private-elsewhere", "protected"],
    )
    def test_only_an_accessible_superclass_member_type_shadows_an_import(
        self, tmp_path: Path, modifier: str, inherited: bool
    ) -> None:
        """``b.Base``'s nested ``Inner`` is inherited by ``a.Sub`` only if ``a`` may access it."""
        files = {
            "b/Base.java": "package b;\npublic class Base {\n"
            f"    {modifier}static class Inner {{\n        void z() {{}}\n    }}\n}}\n",
            "c/Inner.java": "package c;\npublic class Inner {\n"
            "    public void y() {}\n    public void z() {}\n}\n",
            "a/Sub.java": "package a;\nimport b.Base;\nimport c.Inner;\npublic class Sub extends Base {\n"
            "    void m(Inner i) {\n        i.z();\n        i.y();\n    }\n}\n",
        }
        parsed = _parse_all(tmp_path, {k: ("java", v) for k, v in files.items()})
        _link_imports(parsed, {"a/Sub.java": {"b.Base": "b/Base.java", "c.Inner": "c/Inner.java"}})
        resolver = CallResolver(
            parsed,
            {k: set() for k in parsed},
            repo_path=str(tmp_path),
            heritage_parents={"a/Sub.java::Sub": {"b/Base.java::Base"}},
        )
        calls = parsed["a/Sub.java"].calls
        callees = sorted(rc.callee_id for rc in resolver.resolve_file("a/Sub.java", calls))
        if inherited:
            assert callees == ["b/Base.java::Inner::z"]
        else:
            assert callees == ["c/Inner.java::Inner::y", "c/Inner.java::Inner::z"]


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


# Each module declares a top-level ``build`` first and a same-named method
# after it, so a flat last-wins lookup answers with the method.
_MODULE_ONLY_ALIAS_CASES = {
    "dart": (
        "lib/log.dart",
        "void build(String m) {}\n\nclass JsonLogger {\n  void build(String m) {}\n}\n",
        "lib/caller.dart",
        "import 'log.dart' as log;\n\nvoid run() {\n  log.build('hi');\n}\n",
    ),
    "python": (
        "models.py",
        "def build():\n    return 1\n\n\nclass Widget:\n    def build(self):\n        return 2\n",
        "caller.py",
        "import models as m\n\ndef run():\n    return m.build()\n",
    ),
    "typescript": (
        "util.ts",
        "export function build(): number { return 1; }\n\n"
        "export class Widget {\n  build(): number { return 2; }\n}\n",
        "caller.ts",
        "import * as util from './util';\n\n"
        "export function run(): number {\n  return util.build();\n}\n",
    ),
    "javascript": (
        "util.js",
        "export function build() { return 1; }\n\nexport class Widget {\n  build() { return 2; }\n}\n",
        "caller.js",
        "import * as util from './util';\n\nexport function run() {\n  return util.build();\n}\n",
    ),
    "go": (
        "util/util.go",
        "package util\n\nfunc Build() int { return 1 }\n\n"
        "type W struct{}\n\nfunc (w W) Build() int { return 2 }\n",
        "main.go",
        'package main\n\nimport "example.com/m/util"\n\nfunc run() int {\n\treturn util.Build()\n}\n',
    ),
    "rust": (
        "helpers/src/lib.rs",
        "pub fn build() -> u8 { 1 }\n\npub struct Widget;\n\n"
        "impl Widget {\n    pub fn build(&self) -> u8 { 2 }\n}\n",
        "src/main.rs",
        "extern crate helpers as h;\n\npub fn run() -> u8 {\n    h::build()\n}\n",
    ),
    "swift": (
        "Util.swift",
        "func build() -> Int { return 1 }\n\nclass Widget {\n    func build() -> Int { return 2 }\n}\n",
        "Caller.swift",
        "import Util\n\nfunc run() -> Int {\n    return Util.build()\n}\n",
    ),
}


class TestModuleAliasReachesTopLevelOnly:
    """A call through an alias that names a module reaches its top-level names.

    ``import 'log.dart' as log; log.message()`` calls log.dart's ``message``,
    never ``_JsonLogger.message`` declared later in the same file.
    """

    @pytest.mark.parametrize("lang", sorted(_MODULE_ONLY_ALIAS_CASES))
    def test_the_top_level_function_wins_over_a_later_method(
        self, tmp_path: Path, lang: str
    ) -> None:
        module, module_src, caller, caller_src = _MODULE_ONLY_ALIAS_CASES[lang]
        parsed = _parse_all(tmp_path, {module: (lang, module_src), caller: (lang, caller_src)})
        links = {imp.module_path: module for imp in parsed[caller].imports}
        _link_imports(parsed, {caller: links})
        import_targets = {caller: {module}, module: set()}
        caller_id = f"{caller}::run"
        callees = {
            callee
            for src, callee, _, _ in _edges(parsed, tmp_path, import_targets)
            if src == caller_id
        }
        assert callees == {f"{module}::{'Build' if lang == 'go' else 'build'}"}

    def test_a_csharp_type_alias_still_reaches_the_type_s_members(
        self, tmp_path: Path
    ) -> None:
        """``using A = N.T;`` may alias a type, so C# keeps the member lookup."""
        parsed = _parse_all(
            tmp_path,
            {
                "Shapes.cs": (
                    "csharp",
                    "namespace N {\n"
                    "    public class Shape {\n"
                    "        public static int Make() { return 1; }\n"
                    "    }\n"
                    "}\n",
                ),
                "Caller.cs": (
                    "csharp",
                    "using S = N.Shape;\n"
                    "\n"
                    "public class Caller {\n"
                    "    public int Run() { return S.Make(); }\n"
                    "}\n",
                ),
            },
        )
        _link_imports(parsed, {"Caller.cs": {"S": "Shapes.cs"}})
        import_targets = {"Caller.cs": {"Shapes.cs"}, "Shapes.cs": set()}
        assert (
            "Caller.cs::Caller::Run",
            "Shapes.cs::Shape::Make",
            0.88,
            "module_alias",
        ) in _edges(parsed, tmp_path, import_targets)

    def test_rebound_alias_is_not_redirected_to_original_definition(
        self, tmp_path: Path
    ) -> None:
        parsed = _parse_all(
            tmp_path,
            {
                "mod.py": (
                    "python",
                    "def _impl():\n"
                    "    pass\n"
                    "\n"
                    "wrapped = _impl\n"
                    "wrapped = cache(wrapped)\n"
                    "\n"
                    "def use_it():\n"
                    "    wrapped()\n",
                ),
            },
        )

        edges = _edges(parsed, tmp_path)

        assert (
            "mod.py::use_it",
            "mod.py::wrapped",
            0.95,
            "same_file",
        ) in edges

        assert (
            "mod.py::use_it",
            "mod.py::_impl",
            0.95,
            "same_file",
        ) not in edges

    
    def test_chained_alias_redirects_to_final_definition(
        self, tmp_path: Path
    ) -> None:
        parsed = _parse_all(
            tmp_path,
            {
                "mod.py": (
                    "python",
                    "def real():\n"
                    "    pass\n"
                    "\n"
                    "widget = real\n"
                    "s = widget\n"
                    "\n"
                    "def use_it():\n"
                    "    s()\n",
                ),
            },
        )

        edges = _edges(parsed, tmp_path)

        assert (
            "mod.py::use_it",
            "mod.py::real",
            0.95,
            "same_file",
        ) in edges
        assert (
            "mod.py::use_it",
            "mod.py::widget",
            0.95,
            "same_file",
        ) not in edges
        assert (
            "mod.py::use_it",
            "mod.py::s",
            0.95,
            "same_file",
        ) not in edges


class TestExternalReceiverType:
    """A Java receiver declared with a type the repo does not own is refused.

    Every case declares a same-package ``TagSet`` with ``get`` and ``add``, the
    names a name-only tier would otherwise hand the call to.
    """

    _PKG = "src/org/acme/"
    _TAGSET = (
        "package org.acme;\npublic class TagSet {\n"
        "    public Object get(int i) { return null; }\n"
        "    public boolean add(Object o) { return true; }\n}\n"
    )

    def _caller_edges(
        self, tmp_path: Path, caller: str, **others: str
    ) -> list[tuple[str, str, float, str]]:
        files = {"TagSet.java": self._TAGSET, **others, "Caller.java": caller}
        parsed = _parse_all(tmp_path, {self._PKG + k: ("java", v) for k, v in files.items()})
        return [e for e in _edges(parsed, tmp_path) if "Caller.java" in e[0]]

    @staticmethod
    def _caller(imports: str, body: str, members: str = "") -> str:
        return (
            f"package org.acme;\n{imports}public class Caller {{\n{members}"
            f"    public Object run({body}\n    }}\n}}\n"
        )

    def test_an_imported_jdk_type_is_refused(self, tmp_path: Path) -> None:
        caller = self._caller(
            "import java.util.ArrayList;\n",
            "ArrayList<Object> stack) {\n        return stack.get(0);",
        )
        assert self._caller_edges(tmp_path, caller) == []

    def test_a_java_lang_type_needs_no_import_to_be_refused(self, tmp_path: Path) -> None:
        caller = self._caller(
            "", ") {\n        StringBuilder sb = new StringBuilder();\n        return sb.add(1);"
        )
        assert self._caller_edges(tmp_path, caller) == []

    def test_a_field_of_a_jdk_type_is_refused(self, tmp_path: Path) -> None:
        caller = self._caller(
            "import java.util.List;\n",
            ") {\n        return items.get(0);",
            members="    private final List<Object> items = null;\n",
        )
        assert self._caller_edges(tmp_path, caller) == []

    def test_a_member_type_of_a_jdk_type_is_refused(self, tmp_path: Path) -> None:
        caller = self._caller(
            "import java.util.Map;\n",
            "Map.Entry<String, Object> e) {\n        return e.get(0);",
        )
        assert self._caller_edges(tmp_path, caller) == []

    def test_a_wildcard_imported_jdk_type_is_refused(self, tmp_path: Path) -> None:
        caller = self._caller("import java.util.*;\n", "List<Object> l) {\n        return l.get(0);")
        assert self._caller_edges(tmp_path, caller) == []

    def test_a_type_imported_from_outside_the_repo_is_refused(self, tmp_path: Path) -> None:
        caller = self._caller(
            "import com.vendor.Multiset;\n",
            "Multiset<Object> m) {\n        return m.add(1);",
        )
        assert self._caller_edges(tmp_path, caller) == []

    def test_the_static_type_decides_over_a_repo_subclass(self, tmp_path: Path) -> None:
        """``ArrayList l = new Elements()`` calls ``ArrayList.get``; the override is dispatch."""
        elements = (
            "package org.acme;\nimport java.util.ArrayList;\n"
            "public class Elements extends ArrayList<Object> {\n"
            "    public Object get(int i) { return null; }\n}\n"
        )
        caller = self._caller(
            "import java.util.ArrayList;\n",
            ") {\n        ArrayList<Object> l = new Elements();\n        return l.get(0);",
        )
        edges = self._caller_edges(tmp_path, caller, **{"Elements.java": elements})
        assert not [e for e in edges if e[1].endswith("::get")]

    def test_a_repo_subclass_of_a_jdk_type_resolves(self, tmp_path: Path) -> None:
        elements = (
            "package org.acme;\nimport java.util.ArrayList;\n"
            "public class Elements extends ArrayList<Object> {\n"
            "    public Object get(int i) { return null; }\n}\n"
        )
        caller = self._caller("", "Elements l) {\n        return l.get(0);")
        assert (
            "src/org/acme/Caller.java::Caller::run",
            "src/org/acme/Elements.java::Elements::get",
            0.90,
            "receiver_typed_same_package",
        ) in self._caller_edges(tmp_path, caller, **{"Elements.java": elements})

    def test_a_repo_class_shadowing_a_jdk_name_resolves(self, tmp_path: Path) -> None:
        shadow = (
            "package org.acme;\npublic class List {\n"
            "    public Object get(int i) { return null; }\n}\n"
        )
        caller = self._caller("", "List l) {\n        return l.get(0);")
        assert (
            "src/org/acme/Caller.java::Caller::run",
            "src/org/acme/List.java::List::get",
            0.90,
            "receiver_typed_same_package",
        ) in self._caller_edges(tmp_path, caller, **{"List.java": shadow})

    def test_a_builtin_local_leaves_this_field_to_the_field(self, tmp_path: Path) -> None:
        """``this.data.get()`` reaches the resolver as ``data.get()``, beside a ``String data``."""
        caller = self._caller(
            "",
            ") {\n        String data = null;\n        return this.data.get(0);",
            members="    private final TagSet data = new TagSet();\n",
        )
        assert (
            "src/org/acme/Caller.java::Caller::run",
            "src/org/acme/TagSet.java::TagSet::get",
            0.90,
            "receiver_field_same_package",
        ) in self._caller_edges(tmp_path, caller)

    def test_an_inner_repo_class_resolves(self, tmp_path: Path) -> None:
        outer = (
            "package org.acme;\npublic class Outer {\n    public static class Inner {\n"
            "        public Object get(int i) { return null; }\n    }\n}\n"
        )
        caller = self._caller("", "Outer.Inner x) {\n        return x.get(0);")
        assert (
            "src/org/acme/Caller.java::Caller::run",
            "src/org/acme/Outer.java::Inner::get",
            0.90,
            "receiver_typed_same_package",
        ) in self._caller_edges(tmp_path, caller, **{"Outer.java": outer})

    # Unknown rather than external, so not refused; a Java member call still
    # has no bare-name reading to fall back to.
    def test_a_generic_parameter_gets_no_bare_name_edge(self, tmp_path: Path) -> None:
        caller = self._caller(
            "",
            "Object o) {\n        return run2(o);\n    }\n"
            "    public <T> Object run2(T x) {\n        return x.get(0);",
        )
        assert not [e for e in self._caller_edges(tmp_path, caller) if e[1].endswith("::get")]

    def test_a_wildcard_imported_unknown_type_gets_no_bare_name_edge(
        self, tmp_path: Path
    ) -> None:
        caller = self._caller("import com.vendor.*;\n", "Widget w) {\n        return w.get(0);")
        assert self._caller_edges(tmp_path, caller) == []

    def test_an_unresolved_import_of_a_repo_type_gets_no_bare_name_edge(
        self, tmp_path: Path
    ) -> None:
        """No import-resolution phase runs here, so the import reads as external."""
        caller = self._caller(
            "import org.acme.TagSet;\n", "TagSet t) {\n        return t.get(0);"
        )
        assert self._caller_edges(tmp_path, caller) == []

    def test_only_java_records_builtin_declarations(self) -> None:
        from repowise.core.ingestion.languages.receiver_types import (
            EXTERNAL_TYPE_LANGUAGES,
            scan_declarations,
        )

        assert frozenset({"java"}) == EXTERNAL_TYPE_LANGUAGES
        # Each name is a builtin of its own language.
        assert [d.type_name for d in scan_declarations("Task<Foo> t;\n", "csharp")] == []
        assert [d.type_name for d in scan_declarations("List<Foo> xs;\n", "java")] == [
            "external:List"
        ]
class TestPythonNameAliasAssignment:
    """A plain-name alias must redirect onto the real definition, not its own line (#2791)."""

    def test_a_non_chained_alias_resolves_to_the_real_definition(self, tmp_path: Path) -> None:
        parsed = _parse_all(
            tmp_path,
            {
                "mod.py": (
                    "python",
                    "def widget():\n"
                    "    pass\n"
                    "\n"
                    "s = widget\n"
                    "\n"
                    "def use_it():\n"
                    "    s()\n",
                )
            },
        )
        assert _edges(parsed, tmp_path) == [("mod.py::use_it", "mod.py::widget", 0.95, "same_file")]

    def test_a_chained_alias_resolves_every_target_and_the_middle_name_exists(
        self, tmp_path: Path
    ) -> None:
        parsed = _parse_all(
            tmp_path,
            {
                "mod.py": (
                    "python",
                    "def widget():\n"
                    "    pass\n"
                    "\n"
                    "s = attributes = widget\n"
                    "\n"
                    "def use_it():\n"
                    "    s()\n"
                    "    attributes()\n"
                    "    widget()\n",
                )
            },
        )
        names = {sym.name for sym in parsed["mod.py"].symbols}
        assert "attributes" in names  # the query alone drops this today

        edges = _edges(parsed, tmp_path)
        assert edges == [
            ("mod.py::use_it", "mod.py::widget", 0.95, "same_file"),
            ("mod.py::use_it", "mod.py::widget", 0.95, "same_file"),
            ("mod.py::use_it", "mod.py::widget", 0.95, "same_file"),
        ]

    def test_a_non_name_right_hand_side_is_unaffected(self, tmp_path: Path) -> None:
        """A call or an attribute access is a real value, not an alias of a name."""
        parsed = _parse_all(
            tmp_path,
            {
                "mod.py": (
                    "python",
                    "def widget():\n"
                    "    pass\n"
                    "\n"
                    "s = widget()\n"
                    "\n"
                    "def use_it():\n"
                    "    s()\n",
                ),
                "attr.py": (
                    "python",
                    "class C:\n"
                    "    widget = None\n"
                    "\n"
                    "s = C.widget\n"
                    "\n"
                    "def use_it():\n"
                    "    s()\n",
                ),
            },
        )
        mod_symbol = next(sym for sym in parsed["mod.py"].symbols if sym.name == "s")
        attr_symbol = next(sym for sym in parsed["attr.py"].symbols if sym.name == "s")
        assert mod_symbol.alias_of is None
        assert attr_symbol.alias_of is None

        edges = _edges(parsed, tmp_path)
        assert ("mod.py::use_it", "mod.py::s", 0.95, "same_file") in edges
        assert ("attr.py::use_it", "attr.py::s", 0.95, "same_file") in edges

    def test_chained_alias_does_not_overwrite_real_definition(
        self, tmp_path: Path
    ) -> None:
        parsed = _parse_all(
            tmp_path,
            {
                "mod.py": (
                    "python",
                    "def attributes():\n"
                    "    pass\n"
                    "\n"
                    "def widget():\n"
                    "    pass\n"
                    "\n"
                    "s = attributes = widget\n"
                    "\n"
                    "def use_attributes():\n"
                    "    attributes()\n"
                    "\n"
                    "def use_alias():\n"
                    "    s()\n",
                )
            },
        )

        edges = _edges(parsed, tmp_path)

        assert (
            "mod.py::use_attributes",
            "mod.py::widget",
            0.95,
            "same_file",
        ) in edges

        assert (
            "mod.py::use_alias",
            "mod.py::widget",
            0.95,
            "same_file",
        ) in edges

        attributes_symbols = [
            symbol
            for symbol in parsed["mod.py"].symbols
            if symbol.name == "attributes"
        ]

        assert len(attributes_symbols) == 2
        assert len({symbol.id for symbol in attributes_symbols}) == 2
