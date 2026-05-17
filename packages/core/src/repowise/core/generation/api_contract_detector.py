"""Cross-language API-contract detection for ParsedFile objects.

Traverser sets ``FileInfo.is_api_contract = True`` for OpenAPI/Swagger/proto/
GraphQL files purely from filename/extension. That misses framework-defined
HTTP surfaces (FastAPI routers, ASP.NET controllers, etc.) where the contract
is expressed in code, not in a schema file.

This module runs after parsing and flips ``is_api_contract`` for those code
files based on small per-language heuristics that read only the parsed
``Symbol``/``Import`` data — no source re-read, no LLM call.

Adding a new framework: write a ``Detector`` callable and register it in
``_DETECTORS`` keyed by ``LanguageTag``. Keep the heuristic conservative —
false positives push junk through the api_contract template, false negatives
just leave files in their default file_page path.
"""

from __future__ import annotations

from collections.abc import Callable

from repowise.core.ingestion.models import ParsedFile

Detector = Callable[[ParsedFile], bool]


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
    fastapi_names = {"APIRouter", "FastAPI"}
    for imp in parsed.imports:
        if set(imp.imported_names) & fastapi_names:
            return True
    # Decorator form: any symbol decorated with @router.get / @app.post / etc.
    for sym in parsed.symbols:
        for dec in sym.decorators:
            head = dec.lstrip("@").split("(", 1)[0]
            if "." in head and head.rsplit(".", 1)[1] in {
                "get", "post", "put", "patch", "delete", "head", "options",
            }:
                return True
    return False


_ASPNET_CONTROLLER_BASES = frozenset({"ControllerBase", "Controller", "ApiController"})
_ASPNET_ATTRIBUTES = frozenset({"ApiController", "Route", "HttpGet", "HttpPost", "HttpPut", "HttpDelete", "HttpPatch"})


def _csharp_is_aspnet_controller(parsed: ParsedFile) -> bool:
    for sym in parsed.symbols:
        if sym.kind != "class":
            continue
        # Inheritance encoded in signature as ": Base" / ", Interface" in C#.
        sig = sym.signature or ""
        if any(base in sig for base in _ASPNET_CONTROLLER_BASES):
            return True
        for dec in sym.decorators:
            attr = dec.lstrip("@").lstrip("[").split("(", 1)[0].rstrip("]")
            if attr in _ASPNET_ATTRIBUTES:
                return True
    return False


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
        if pf.file_info.is_api_contract:
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
