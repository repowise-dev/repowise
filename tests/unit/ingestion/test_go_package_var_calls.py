"""Go calls through a package variable and its field chain.

``skillsApi.GetTools()`` with ``var skillsApi = v1.ApiGroupApp.SystemApiGroup.SkillsApi``
names no type at the call. The variable's declaration does, and each field's
struct does after it. Every negative removes one proof, and must leave the call
unresolved rather than guessed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.ingestion.languages.go_selector_types import (
    Field,
    VarSpec,
    function_bindings,
    go_file_facts,
)
from tests.unit.ingestion.test_chained_receiver_calls import _edges

_API_V1 = (
    "go",
    'package v1\n\nimport "m/api/v1/system"\n\n'
    "var ApiGroupApp = new(ApiGroup)\n\n"
    "type ApiGroup struct {\n\tSystemApiGroup system.ApiGroup\n}\n",
)
_API_SYSTEM = (
    "go",
    "package system\n\ntype ApiGroup struct {\n\tSkillsApi\n\tUserApi\n}\n",
)
_SKILLS_API = (
    "go",
    "package system\n\ntype SkillsApi struct{}\n\nfunc (s *SkillsApi) GetTools() {}\n",
)
_USER_API = (
    "go",
    "package system\n\ntype UserApi struct{}\n\nfunc (u *UserApi) GetUser() {}\n",
)
_ROUTER_ENTER = (
    "go",
    'package system\n\nimport api "m/api/v1"\n\n'
    "type RouterGroup struct {\n\tSkillsRouter\n}\n\n"
    "var (\n\tskillsApi = api.ApiGroupApp.SystemApiGroup.SkillsApi\n)\n",
)
_ROUTER = (
    "go",
    'package router\n\nimport "m/router/system"\n\n'
    "var RouterGroupApp = new(RouterGroup)\n\n"
    "type RouterGroup struct {\n\tSystem system.RouterGroup\n}\n",
)
_LINKS = {
    "api/v1/enter.go": {"m/api/v1/system": "api/v1/system/enter.go"},
    "router/system/enter.go": {"m/api/v1": "api/v1/enter.go"},
    "router/enter.go": {"m/router/system": "router/system/enter.go"},
    "initialize/router.go": {"m/router": "router/enter.go"},
}

_INIT_SKILLS = "router/system/skills.go::SkillsRouter::InitSkillsRouter"
_GET_TOOLS = "api/v1/system/skills.go::SkillsApi::GetTools"


def _files(skills_router_body: str, extra: dict[str, tuple[str, str]] | None = None) -> dict:
    files = {
        "api/v1/enter.go": _API_V1,
        "api/v1/system/enter.go": _API_SYSTEM,
        "api/v1/system/skills.go": _SKILLS_API,
        "api/v1/system/user.go": _USER_API,
        "router/system/enter.go": _ROUTER_ENTER,
        "router/system/skills.go": (
            "go",
            "package system\n\ntype SkillsRouter struct{}\n\n"
            f"func (s *SkillsRouter) InitSkillsRouter() {{\n{skills_router_body}}}\n",
        ),
        "router/enter.go": _ROUTER,
    }
    files.update(extra or {})
    return files


def _pairs(tmp_path: Path, files: dict, links: dict | None = None) -> set[tuple[str, str, str]]:
    links = {p: m for p, m in (_LINKS if links is None else links).items() if p in files}
    return {(e[0], e[1], e[3]) for e in _edges(tmp_path, files, links)}


class TestResolvedThroughAPackageVariable:
    def test_a_package_variable_from_a_sibling_file_types_the_call(self, tmp_path: Path) -> None:
        """The variable is a field chain through another package's variable and an embed."""
        edges = _pairs(tmp_path, _files("\tskillsApi.GetTools()\n"))
        assert (_INIT_SKILLS, _GET_TOOLS, "receiver_chain_import") in edges

    def test_another_packages_variable_and_its_fields_type_the_call(self, tmp_path: Path) -> None:
        """``router.RouterGroupApp.System.InitSkillsRouter()``: the method is promoted."""
        caller = (
            'package initialize\n\nimport "m/router"\n\n'
            "func Routers() {\n\trouter.RouterGroupApp.System.InitSkillsRouter()\n}\n"
        )
        edges = _pairs(tmp_path, _files("", {"initialize/router.go": ("go", caller)}))
        assert ("initialize/router.go::Routers", _INIT_SKILLS, "receiver_chain_import") in edges

    def test_an_unaliased_import_named_by_its_package_clause(self, tmp_path: Path) -> None:
        """``m/api/v1`` declares ``package v1``: the version-looking element is the name."""
        caller = (
            'package initialize\n\nimport "m/api/v1"\n\n'
            "func Routers() {\n\tv1.ApiGroupApp.SystemApiGroup.SkillsApi.GetTools()\n}\n"
        )
        files = _files("", {"initialize/router.go": ("go", caller)})
        links = {**_LINKS, "initialize/router.go": {"m/api/v1": "api/v1/enter.go"}}
        edges = _pairs(tmp_path, files, links)
        assert ("initialize/router.go::Routers", _GET_TOOLS, "receiver_chain_import") in edges

    def test_a_local_bound_once_from_a_chain_types_the_call(self, tmp_path: Path) -> None:
        caller = (
            'package initialize\n\nimport "m/router"\n\n'
            "func Routers() {\n\tsystemRouter := router.RouterGroupApp.System\n"
            "\tsystemRouter.InitSkillsRouter()\n}\n"
        )
        edges = _pairs(tmp_path, _files("", {"initialize/router.go": ("go", caller)}))
        assert ("initialize/router.go::Routers", _INIT_SKILLS, "receiver_chain_import") in edges

    @pytest.mark.parametrize(
        "declaration",
        [
            "var store = &Store{}",
            "var store = Store{}",
            "var store *Store",
            "var store Store = makeStore()",
        ],
    )
    def test_a_variable_typed_by_its_declaration(self, tmp_path: Path, declaration: str) -> None:
        source = (
            "package app\n\ntype Store struct{}\n\nfunc (s *Store) Save() {}\n\n"
            f"{declaration}\n\nfunc makeStore() Store {{ return Store{{}} }}\n\n"
            "func run() {\n\tstore.Save()\n}\n"
        )
        edges = _pairs(tmp_path, {"app/app.go": ("go", source)}, {})
        assert ("app/app.go::run", "app/app.go::Store::Save", "receiver_chain_import") in edges


class TestRefused:
    def test_a_parameter_of_the_same_name_shadows_the_variable(self, tmp_path: Path) -> None:
        body = "\tfunc(skillsApi Other) { skillsApi.GetTools() }(Other{})\n"
        edges = _pairs(tmp_path, _files(body))
        assert not any(e[1] == _GET_TOOLS for e in edges)

    def test_a_variable_declared_in_two_files_of_the_package(self, tmp_path: Path) -> None:
        """Two build-tagged files each declaring it: which one compiles is not read."""
        twin = (
            "go",
            'package system\n\nimport api "m/api/v1"\n\nvar skillsApi = api.ApiGroupApp\n',
        )
        files = _files("\tskillsApi.GetTools()\n", {"router/system/twin.go": twin})
        links = {**_LINKS, "router/system/twin.go": {"m/api/v1": "api/v1/enter.go"}}
        assert not any(e[1] == _GET_TOOLS for e in _pairs(tmp_path, files, links))

    def test_a_variable_initialized_from_a_call(self, tmp_path: Path) -> None:
        source = (
            "package app\n\ntype Store struct{}\n\nfunc (s *Store) Save() {}\n\n"
            "var store = makeStore()\n\nfunc makeStore() *Store { return nil }\n\n"
            "func run() {\n\tstore.Save()\n}\n"
        )
        edges = _pairs(tmp_path, {"app/app.go": ("go", source)}, {})
        assert not any(e[2] == "receiver_chain_import" for e in edges)

    def test_two_embeds_promoting_the_method_at_one_depth(self, tmp_path: Path) -> None:
        """``UserApi`` also has ``GetTools``: the selector is ambiguous in Go."""
        user = (
            "go",
            "package system\n\ntype UserApi struct{}\n\nfunc (u *UserApi) GetTools() {}\n",
        )
        router_enter = (
            "go",
            'package system\n\nimport api "m/api/v1"\n\n'
            "var group = api.ApiGroupApp.SystemApiGroup\n",
        )
        files = _files(
            "\tgroup.GetTools()\n",
            {"api/v1/system/user.go": user, "router/system/enter.go": router_enter},
        )
        assert not any("GetTools" in e[1] for e in _pairs(tmp_path, files))

    def test_an_embed_from_outside_the_repository_stops_a_deeper_search(
        self, tmp_path: Path
    ) -> None:
        """``gorm.Model`` may promote the method at a shallower depth than the repo type."""
        source = (
            'package app\n\nimport "gorm.io/gorm"\n\n'
            "type Inner struct{}\n\nfunc (i *Inner) Save() {}\n\n"
            "type Middle struct {\n\tInner\n}\n\n"
            "type Outer struct {\n\tgorm.Model\n\tMiddle\n}\n\n"
            "var outer = new(Outer)\n\nfunc run() {\n\touter.Save()\n}\n"
        )
        edges = _pairs(tmp_path, {"app/app.go": ("go", source)}, {})
        assert not any(e[2] == "receiver_chain_import" for e in edges)

    def test_a_type_declared_in_two_files_of_the_package(self, tmp_path: Path) -> None:
        twin = ("go", "package system\n\ntype SkillsApi struct{}\n")
        files = _files("\tskillsApi.GetTools()\n", {"api/v1/system/twin.go": twin})
        assert not any(e[1] == _GET_TOOLS for e in _pairs(tmp_path, files))

    def test_an_interface_typed_variable(self, tmp_path: Path) -> None:
        """``var decoders Decoder`` dispatches at run time: no method spec is the callee."""
        source = (
            "package app\n\ntype Decoder interface {\n\tDecode()\n}\n\n"
            "var decoders Decoder\n\nfunc run() {\n\tdecoders.Decode()\n}\n"
        )
        edges = _pairs(tmp_path, {"app/app.go": ("go", source)}, {})
        assert not any(e[2] == "receiver_chain_import" for e in edges)

    def test_a_local_bound_in_a_block_types_only_calls_inside_it(self, tmp_path: Path) -> None:
        """Inside the ``if`` ``skillsApi`` is a ``UserApi``; after it, the package variable."""
        user = "api.ApiGroupApp.SystemApiGroup.UserApi"
        inside = f"\tif skillsApi := {user}; true {{\n\t\tskillsApi.GetTools()\n\t}}\n"
        after = f"\tif skillsApi := {user}; true {{\n\t}}\n\tskillsApi.GetTools()\n"
        router = 'package system\n\nimport api "m/api/v1"\n\ntype SkillsRouter struct{}\n\n'
        for body, bound in ((inside, False), (after, True)):
            files = _files(
                "",
                {
                    "router/system/skills.go": (
                        "go",
                        router + (f"func (s *SkillsRouter) InitSkillsRouter() {{\n{body}}}\n"),
                    )
                },
            )
            links = {**_LINKS, "router/system/skills.go": {"m/api/v1": "api/v1/enter.go"}}
            edges = _pairs(tmp_path / str(bound), files, links)
            assert ((_INIT_SKILLS, _GET_TOOLS, "receiver_chain_import") in edges) is bound

    def test_a_field_named_like_the_promoted_method(self, tmp_path: Path) -> None:
        """``Run func()`` sits at depth 0 and ``Base.Run`` at depth 1: Go calls the field."""
        source = (
            "package app\n\ntype Base struct{}\n\nfunc (b *Base) Run() {}\n\n"
            "type S struct {\n\tBase\n\tRun func()\n}\n\n"
            "var s = S{}\n\nfunc run() {\n\ts.Run()\n}\n"
        )
        edges = _pairs(tmp_path, {"app/app.go": ("go", source)}, {})
        assert not any(e[2] == "receiver_chain_import" for e in edges)

    def test_an_embedded_interface_stops_a_deeper_search(self, tmp_path: Path) -> None:
        """``Runner`` promotes ``Run`` at depth 1, ahead of ``Inner.Run`` at depth 2."""
        source = (
            "package app\n\ntype Runner interface {\n\tRun()\n}\n\n"
            "type Inner struct{}\n\nfunc (i *Inner) Run() {}\n\n"
            "type Middle struct {\n\tInner\n}\n\n"
            "type Outer struct {\n\tRunner\n\tMiddle\n}\n\n"
            "var outer = new(Outer)\n\nfunc run() {\n\touter.Run()\n}\n"
        )
        edges = _pairs(tmp_path, {"app/app.go": ("go", source)}, {})
        assert ("app/app.go::run", "app/app.go::Inner::Run", "receiver_chain_import") not in edges


class TestFacts:
    def test_imports_variables_and_struct_fields(self) -> None:
        facts = go_file_facts(
            'package p\n\nimport (\n\tapi "m/api/v1"\n\t"m/router"\n\t_ "m/side"\n)\n\n'
            "var (\n\ta = api.X.Y\n\tb, c int\n\td = f()\n\te = new(T)\n)\n\nvar a = 1\n\n"
            'type T struct {\n\t*Base\n\tpkg.Embedded\n\tF, G *pkg.T `json:"f"`\n\tH []T\n}\n'
        )
        assert facts.imports == {"api": "m/api/v1", "router": "m/router"}
        major = go_file_facts('package p\n\nimport (\n\t"x/foo/v2"\n\t"gopkg.in/yaml.v3"\n)\n')
        assert major.imports == {
            "v2": "x/foo/v2",
            "foo": "x/foo/v2",
            "yaml.v3": "gopkg.in/yaml.v3",
            "yaml": "gopkg.in/yaml.v3",
        }
        assert major.guessed_imports == {"v2", "foo", "yaml.v3", "yaml"}
        assert facts.variables == {
            "a": None,  # declared twice
            "b": VarSpec(written=(None, "int")),  # no repository type: no hop follows it
            "c": VarSpec(written=(None, "int")),
            "d": None,
            "e": VarSpec(written=(None, "T")),
        }
        assert facts.structs["T"] == (
            Field("Base", (None, "Base"), True),
            Field("Embedded", ("pkg", "Embedded"), True),
            Field("F", ("pkg", "T"), False),
            Field("G", ("pkg", "T"), False),
            Field("H", None, False),
        )

    def test_every_binding_form_in_a_function_counts(self) -> None:
        source = (
            "package p\n\nfunc (s *S) F(a int, b ...string) (r error) {\n"
            "\tx := router.App.System\n\tfor k, v := range m {}\n"
            "\tswitch t := y.(type) {}\n\tselect {\n\tcase q := <-ch:\n\t}\n"
            "\tgo func(w int) {}(1)\n\tif e := f(); e != nil {}\n}\n"
        )
        ((first, last, bound),) = function_bindings(source)
        assert (first, last) == (3, 12)
        assert [b.spec for b in bound["x"]] == [VarSpec(chain=("router", "App", "System"))]
        assert bound["e"][0].scope == (11, 11)  # the ``if`` it is bound in
        assert set(bound) == {"s", "a", "b", "r", "x", "k", "v", "t", "q", "w", "e"}
