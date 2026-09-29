"""Tests for the framework-aware api_contract detector."""

from __future__ import annotations

from datetime import datetime

import pytest

from repowise.core.generation.api_contract_detector import detect_code_api_contracts
from repowise.core.ingestion.models import FileInfo, HeritageRelation, Import, ParsedFile, Symbol


def _file(path: str, language: str, is_test: bool = False) -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=f"/repo/{path}",
        language=language,  # type: ignore[arg-type]
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=is_test,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _sym(
    name: str,
    kind: str = "function",
    decorators: list[str] | None = None,
    signature: str = "",
    parent: str | None = None,
    visibility: str = "public",
) -> Symbol:
    return Symbol(
        id=f"x::{name}",
        name=name,
        qualified_name=name,
        kind=kind,  # type: ignore[arg-type]
        signature=signature,
        start_line=1,
        end_line=2,
        docstring=None,
        decorators=decorators or [],
        parent_name=parent,
        visibility=visibility,  # type: ignore[arg-type]
    )


def _imp(module: str, names: list[str]) -> Import:
    return Import(
        raw_statement=f"from {module} import " + ", ".join(names),
        module_path=module,
        imported_names=names,
        is_relative=False,
        resolved_file=None,
    )


def _parsed(
    path: str, language: str, *, imports=(), symbols=(), heritage=(), is_test=False
) -> ParsedFile:
    return ParsedFile(
        file_info=_file(path, language, is_test),
        symbols=list(symbols),
        imports=list(imports),
        exports=[],
        heritage=list(heritage),
    )


def _extends(child: str, parent: str) -> HeritageRelation:
    return HeritageRelation(child_name=child, parent_name=parent, kind="extends", line=1)


def test_fastapi_import_without_an_operation_is_not_a_contract():
    """Building the app or mounting routers declares no endpoint."""
    pf = _parsed("app/main.py", "python", imports=[_imp("fastapi", ["APIRouter", "FastAPI"])])
    assert detect_code_api_contracts([pf]) == 0
    assert pf.file_info.is_api_contract is False


def test_detects_fastapi_router_with_an_operation():
    pf = _parsed(
        "app/routes.py",
        "python",
        imports=[_imp("fastapi", ["APIRouter"])],
        symbols=[_sym("create", decorators=["@router.post('/items')"])],
    )
    assert detect_code_api_contracts([pf]) == 1
    assert pf.file_info.is_api_contract is True


def test_a_test_file_is_never_a_contract():
    pf = _parsed(
        "tests/test_routes.py",
        "python",
        imports=[_imp("fastapi", ["FastAPI"])],
        symbols=[_sym("read", decorators=["@app.get('/')"])],
        is_test=True,
    )
    assert detect_code_api_contracts([pf]) == 0


def test_detects_fastapi_via_method_decorator():
    pf = _parsed(
        "app/routes.py",
        "python",
        imports=[_imp("fastapi", ["Depends"])],
        symbols=[_sym("list_items", decorators=["@router.get('/items')"])],
    )
    assert detect_code_api_contracts([pf]) == 1


def test_skips_python_file_without_fastapi():
    pf = _parsed("app/utils.py", "python", imports=[_imp("os", ["path"])])
    assert detect_code_api_contracts([pf]) == 0
    assert pf.file_info.is_api_contract is False


@pytest.mark.parametrize("base", ["ControllerBase", "Controller", "ApiController"])
def test_detects_aspnet_controller_via_inheritance(base):
    pf = _parsed(
        "Controllers/UsersController.cs",
        "csharp",
        symbols=[
            _sym("UsersController", kind="class", signature="class UsersController"),
            _sym("Get", kind="method", parent="UsersController"),
        ],
        heritage=[_extends("UsersController", base)],
    )
    assert detect_code_api_contracts([pf]) == 1


def test_detects_aspnet_controller_via_attribute():
    pf = _parsed(
        "Controllers/UsersController.cs",
        "csharp",
        symbols=[
            _sym("UsersController", kind="class", decorators=["ApiController"]),
            _sym("List", kind="method", parent="UsersController"),
        ],
    )
    assert detect_code_api_contracts([pf]) == 1


def test_detects_aspnet_action_on_a_custom_base_controller():
    pf = _parsed(
        "Controllers/UsersController.cs",
        "csharp",
        symbols=[
            _sym("UsersController", kind="class"),
            _sym("Get", kind="method", parent="UsersController", decorators=['HttpGet("{id}")']),
        ],
        heritage=[_extends("UsersController", "AppControllerBase")],
    )
    assert detect_code_api_contracts([pf]) == 1


def test_aspnet_controller_without_a_public_action_is_not_a_contract():
    pf = _parsed(
        "Controllers/UsersController.cs",
        "csharp",
        symbols=[
            _sym("UsersController", kind="class"),
            _sym("Helper", kind="method", parent="UsersController", visibility="private"),
        ],
        heritage=[_extends("UsersController", "ControllerBase")],
    )
    assert detect_code_api_contracts([pf]) == 0


def test_a_class_named_controller_is_not_a_contract():
    """A name is not a base class: a game ``InputController`` serves no HTTP."""
    pf = _parsed(
        "Input/InputController.cs",
        "csharp",
        symbols=[
            _sym("InputController", kind="class", signature="class InputController"),
            _sym("Update", kind="method", parent="InputController"),
        ],
        heritage=[_extends("InputController", "MonoBehaviour")],
    )
    assert detect_code_api_contracts([pf]) == 0


def test_already_flagged_file_is_untouched():
    pf = _parsed("api/openapi.yaml", "yaml")
    pf.file_info.is_api_contract = True
    assert detect_code_api_contracts([pf]) == 0  # already flagged, not "newly flagged"


def test_unknown_language_skipped():
    pf = _parsed("main.rb", "ruby")
    assert detect_code_api_contracts([pf]) == 0
