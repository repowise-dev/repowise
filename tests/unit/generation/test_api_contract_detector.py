"""Tests for the framework-aware api_contract detector."""

from __future__ import annotations

from datetime import datetime

from repowise.core.generation.api_contract_detector import detect_code_api_contracts
from repowise.core.ingestion.models import FileInfo, Import, ParsedFile, Symbol


def _file(path: str, language: str) -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=f"/repo/{path}",
        language=language,  # type: ignore[arg-type]
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _sym(name: str, kind: str = "function", decorators: list[str] | None = None, signature: str = "") -> Symbol:
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
    )


def _imp(module: str, names: list[str]) -> Import:
    return Import(
        raw_statement=f"from {module} import " + ", ".join(names),
        module_path=module,
        imported_names=names,
        is_relative=False,
        resolved_file=None,
    )


def _parsed(path: str, language: str, *, imports=(), symbols=()) -> ParsedFile:
    return ParsedFile(
        file_info=_file(path, language),
        symbols=list(symbols),
        imports=list(imports),
        exports=[],
    )


def test_detects_fastapi_router_via_import():
    pf = _parsed("app/routes.py", "python", imports=[_imp("fastapi", ["APIRouter"])])
    assert detect_code_api_contracts([pf]) == 1
    assert pf.file_info.is_api_contract is True


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


def test_detects_aspnet_controller_via_inheritance():
    pf = _parsed(
        "Controllers/UsersController.cs",
        "csharp",
        symbols=[_sym("UsersController", kind="class", signature="class UsersController : ControllerBase")],
    )
    assert detect_code_api_contracts([pf]) == 1


def test_detects_aspnet_controller_via_attribute():
    pf = _parsed(
        "Controllers/UsersController.cs",
        "csharp",
        symbols=[_sym("UsersController", kind="class", decorators=["[ApiController]"])],
    )
    assert detect_code_api_contracts([pf]) == 1


def test_already_flagged_file_is_untouched():
    pf = _parsed("api/openapi.yaml", "yaml")
    pf.file_info.is_api_contract = True
    assert detect_code_api_contracts([pf]) == 0  # already flagged, not "newly flagged"


def test_unknown_language_skipped():
    pf = _parsed("main.rb", "ruby")
    assert detect_code_api_contracts([pf]) == 0
