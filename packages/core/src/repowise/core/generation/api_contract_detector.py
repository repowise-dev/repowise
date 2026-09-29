"""Cross-language API-contract detection for ParsedFile objects.

Traverser sets ``FileInfo.is_api_contract = True`` for non-test OpenAPI/Swagger/
proto/GraphQL spec files from extension and filename. That misses framework-defined
HTTP surfaces (FastAPI routers, ASP.NET controllers, etc.) where the contract
is expressed in code, not in a schema file.

This module runs after parsing and flips ``is_api_contract`` for those code
non-test files that declare at least one operation, using small per-language
heuristics that read only the parsed ``Symbol``/``Import``/heritage data — no source re-read, no LLM call.

Adding a new framework: write a ``Detector`` callable and register it in
``_DETECTORS`` keyed by ``LanguageTag``. Keep the heuristic conservative —
false positives push junk through the api_contract template, false negatives
just leave files in their default file_page path.
"""

from __future__ import annotations

from collections.abc import Callable

from repowise.core.ingestion.models import ParsedFile

Detector = Callable[[ParsedFile], bool]


_HTTP_VERBS = frozenset({"get", "post", "put", "patch", "delete", "head", "options", "api_route"})


def _decorator_name(dec: str) -> str:
    """``@router.get('/x')`` -> ``router.get``; ``[HttpGet("x")]`` -> ``HttpGet``."""
    return dec.lstrip("@[").split("(", 1)[0].rstrip("]")


def _python_is_fastapi_router(parsed: ParsedFile) -> bool:
    # The parser sometimes resolves "from fastapi import APIRouter" with
    # module_path = "fastapi" and imported_names = ["APIRouter"], and
    # sometimes with module_path = "fastapi.APIRouter". Cover both.
    imports_fastapi = any(
        imp.module_path == "fastapi"
        or imp.module_path.startswith("fastapi.")
        for imp in parsed.imports
    )
    if not imports_fastapi:
        return False
    # The contract is the operations: a file that only builds the app or mounts
    # routers declares none (``@router.get`` / ``@app.post`` and friends).
    for sym in parsed.symbols:
        for dec in sym.decorators:
            head = _decorator_name(dec)
            if "." in head and head.rsplit(".", 1)[1] in _HTTP_VERBS:
                return True
    return False


_ASPNET_CONTROLLER_BASES = frozenset({"ControllerBase", "Controller"})
_ASPNET_CLASS_ATTRIBUTES = frozenset({"ApiController", "Route"})
_ASPNET_ACTION_ATTRIBUTES = frozenset(
    {"HttpGet", "HttpPost", "HttpPut", "HttpDelete", "HttpPatch", "HttpHead", "HttpOptions"}
)


def _csharp_is_aspnet_controller(parsed: ParsedFile) -> bool:
    controllers = {
        h.child_name for h in parsed.heritage if h.parent_name in _ASPNET_CONTROLLER_BASES
    }
    controllers.update(
        sym.name
        for sym in parsed.symbols
        if sym.kind == "class"
        and any(_decorator_name(d) in _ASPNET_CLASS_ATTRIBUTES for d in sym.decorators)
    )
    # An operation: a verb-attributed method, or a public action on a controller.
    return any(
        sym.kind == "method"
        and (
            any(_decorator_name(d) in _ASPNET_ACTION_ATTRIBUTES for d in sym.decorators)
            or (sym.visibility == "public" and sym.parent_name in controllers)
        )
        for sym in parsed.symbols
    )


_DETECTORS: dict[str, Detector] = {
    "python": _python_is_fastapi_router,
    "csharp": _csharp_is_aspnet_controller,
}


def detect_code_api_contracts(parsed_files: list[ParsedFile]) -> int:
    """Flip ``is_api_contract`` on parsed files that define an HTTP API surface.

    Returns the number of files newly flagged. Files already flagged by the
    traverser (OpenAPI/proto/GraphQL) are left untouched.
    """
    flipped = 0
    for pf in parsed_files:
        # A test that calls an API is not its contract.
        if pf.file_info.is_api_contract or pf.file_info.is_test:
            continue
        detector = _DETECTORS.get(pf.file_info.language)
        if detector is None:
            continue
        try:
            if detector(pf):
                pf.file_info.is_api_contract = True
                flipped += 1
        except Exception:
            # Defensive: a malformed ParsedFile must never crash generation.
            continue
    return flipped
