"""HTTP route contract extraction.

Scans source files for route handler declarations (providers) and HTTP client
calls (consumers). Patterns cover Express, FastAPI, Spring, Laravel, Go,
fetch, axios, requests, and httpx.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

_log = logging.getLogger("repowise.workspace.extractors.http")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BLOCKED_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", "target", "vendor", ".next", ".nuxt",
    ".tox", ".mypy_cache", ".gradle", ".mvn", "out", "bin",
})

_MAX_FILE_SIZE = 512 * 1024  # 512 KB

_PROVIDER_EXTENSIONS = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx", ".java", ".php", ".go",
})

_CONSUMER_EXTENSIONS = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx",
})

_ALL_EXTENSIONS = _PROVIDER_EXTENSIONS | _CONSUMER_EXTENSIONS


# ---------------------------------------------------------------------------
# Path normalization
# ---------------------------------------------------------------------------


def normalize_http_path(path: str) -> str:
    """Normalize an HTTP path for matching.

    Steps:
      1. Strip whitespace
      2. Remove query string
      3. Lowercase
      4. Strip trailing slash (but keep root ``/``)
      5. Unify param styles: ``:param``, ``{param}``, ``[param]`` → ``{param}``
    """
    s = path.strip().split("?")[0].lower()
    if s != "/":
        s = s.rstrip("/")
    # Unify Express :param
    s = re.sub(r":(\w+)", "{param}", s)
    # Unify Spring/FastAPI {name} → {param}
    s = re.sub(r"\{[^}]+\}", "{param}", s)
    # Unify Next.js [name] → {param}
    s = re.sub(r"\[[^\]]+\]", "{param}", s)
    # Unify JS template literal ${expr} → {param}
    s = re.sub(r"\$\{[^}]+\}", "{param}", s)
    return s or "/"


# ---------------------------------------------------------------------------
# Provider patterns
# ---------------------------------------------------------------------------

# Each pattern: (compiled_regex, method_group_or_none, path_group, framework)
# method_group is the capture group index for the HTTP method (1-based), or 0
# if the method is embedded in the regex match differently.

_METHODS = r"get|post|put|delete|patch"
_METHODS_UPPER = r"GET|POST|PUT|DELETE|PATCH"

# Express / Node.js: router.get('/path', ...) or app.post('/path', ...)
# Negative lookbehind for @ to avoid matching FastAPI decorators
_EXPRESS_RE = re.compile(
    rf"""(?<!@)(?:router|app)\.({_METHODS})\s*\(\s*['"]([^'"]+)['"]""",
    re.IGNORECASE,
)

# FastAPI / Python: @app.get('/path') or @router.post('/path')
_FASTAPI_RE = re.compile(
    rf"""@(?:app|router)\.({_METHODS})\s*\(\s*['"]([^'"]+)['"]""",
    re.IGNORECASE,
)

# Spring: @GetMapping("/path"), @PostMapping(value="/path"), etc.
_SPRING_METHOD_RE = re.compile(
    r"""@(Get|Post|Put|Delete|Patch)Mapping\s*\(\s*(?:value\s*=\s*)?['"]([^'"]+)['"]""",
    re.IGNORECASE,
)

# Spring class-level prefix: @RequestMapping("/api/v1")
_SPRING_CLASS_RE = re.compile(
    r"""@RequestMapping\s*\(\s*(?:value\s*=\s*)?['"]([^'"]+)['"]""",
)

# Laravel: Route::get('/path', ...)
_LARAVEL_RE = re.compile(
    rf"""Route::({_METHODS})\s*\(\s*['"]([^'"]+)['"]""",
    re.IGNORECASE,
)

# Go (gin, echo, chi, net/http): r.GET("/path", ...) or .HandleFunc("/path", ...)
_GO_ROUTE_RE = re.compile(
    rf"""\.({_METHODS_UPPER}|Handle|HandleFunc)\s*\(\s*['"]([^'"]+)['"]""",
)

_PROVIDER_PATTERNS = [
    (_EXPRESS_RE, "express"),
    (_FASTAPI_RE, "fastapi"),
    (_SPRING_METHOD_RE, "spring"),
    (_LARAVEL_RE, "laravel"),
    (_GO_ROUTE_RE, "go"),
]

# ---------------------------------------------------------------------------
# Consumer patterns
# ---------------------------------------------------------------------------

# fetch('/api/users') or fetch('/api/users', { method: 'POST' })
_FETCH_RE = re.compile(
    r"""fetch\s*\(\s*['"`]([^'"`]+)['"`]""",
)
_FETCH_METHOD_RE = re.compile(
    r"""fetch\s*\(\s*['"`]([^'"`]+)['"`]\s*,\s*\{[^}]*method\s*:\s*['"](\w+)['"]""",
    re.DOTALL,
)

# axios.get('/api/users')
_AXIOS_RE = re.compile(
    rf"""axios\.({_METHODS})\s*\(\s*['"]([^'"]+)['"]""",
    re.IGNORECASE,
)

# requests.get('http://host/api/users') or httpx.post(...)
_REQUESTS_RE = re.compile(
    rf"""(?:requests|httpx)\.({_METHODS})\s*\(\s*['"]([^'"]+)['"]""",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


def _extract_path_from_url(url: str) -> str:
    """Extract just the path portion from a URL, stripping scheme and host."""
    # If it starts with http:// or https://, strip scheme + authority
    if "://" in url:
        after_scheme = url.split("://", 1)[1]
        slash_idx = after_scheme.find("/")
        if slash_idx >= 0:
            return after_scheme[slash_idx:]
        return "/"
    return url


class HttpExtractor:
    """Extract HTTP route contracts from source files."""

    def extract(self, repo_path: Path, repo_alias: str = "") -> list[Contract]:
        """Scan all source files in *repo_path* and return Contract instances."""
        from repowise.core.workspace.contracts import Contract

        contracts: list[Contract] = []
        repo_root = repo_path.resolve()

        for dirpath, dirnames, filenames in os.walk(repo_root):
            dirnames[:] = [
                d for d in dirnames
                if d not in _BLOCKED_DIRS and not d.startswith(".")
            ]
            for fname in filenames:
                fpath = Path(dirpath) / fname
                suffix = fpath.suffix.lower()
                if suffix not in _ALL_EXTENSIONS:
                    continue
                try:
                    if fpath.stat().st_size > _MAX_FILE_SIZE:
                        continue
                except OSError:
                    continue

                try:
                    content = fpath.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue

                rel_path = fpath.relative_to(repo_root).as_posix()

                # --- Providers ---
                if suffix in _PROVIDER_EXTENSIONS:
                    # Spring: collect all class-level @RequestMapping positions and prefixes
                    # so each method-level annotation gets the prefix from the nearest
                    # preceding class declaration rather than always the first one in the file.
                    spring_class_mappings: list[tuple[int, str]] = []
                    if suffix == ".java":
                        for cm in _SPRING_CLASS_RE.finditer(content):
                            spring_class_mappings.append((cm.start(), cm.group(1).rstrip("/")))

                    for pattern, framework in _PROVIDER_PATTERNS:
                        for match in pattern.finditer(content):
                            method_raw = match.group(1)
                            path_raw = match.group(2)

                            # Go Handle/HandleFunc don't carry a method
                            if method_raw in ("Handle", "HandleFunc"):
                                method = "*"
                            else:
                                method = method_raw.upper()

                            # Apply Spring class-level prefix: pick the nearest preceding
                            # @RequestMapping (highest start position that is still before
                            # this match).
                            if framework == "spring" and spring_class_mappings:
                                match_pos = match.start()
                                spring_prefix = ""
                                for cls_pos, cls_prefix in spring_class_mappings:
                                    if cls_pos < match_pos:
                                        spring_prefix = cls_prefix
                                    else:
                                        break
                                if spring_prefix:
                                    path_raw = spring_prefix + "/" + path_raw.lstrip("/")

                            norm_path = normalize_http_path(path_raw)

                            # Skip paths that look like template variables or empty
                            if not norm_path or norm_path == "/":
                                if not path_raw.strip("/"):
                                    continue

                            contract_id = f"http::{method}::{norm_path}"

                            contracts.append(Contract(
                                repo=repo_alias,
                                contract_id=contract_id,
                                contract_type="http",
                                role="provider",
                                file_path=rel_path,
                                symbol_name=f"{framework}:{method} {path_raw}",
                                confidence=0.85,
                                service=None,
                                meta={
                                    "method": method,
                                    "path": norm_path,
                                    "framework": framework,
                                },
                            ))

                # --- Consumers ---
                if suffix in _CONSUMER_EXTENSIONS:
                    # fetch() with method
                    for match in _FETCH_METHOD_RE.finditer(content):
                        url = match.group(1)
                        method = match.group(2).upper()
                        path = _extract_path_from_url(url)
                        norm_path = normalize_http_path(path)
                        contract_id = f"http::{method}::{norm_path}"
                        contracts.append(Contract(
                            repo=repo_alias,
                            contract_id=contract_id,
                            contract_type="http",
                            role="consumer",
                            file_path=rel_path,
                            symbol_name=f"fetch:{method} {url}",
                            confidence=0.75,
                            service=None,
                            meta={"method": method, "path": norm_path, "client": "fetch"},
                        ))

                    # fetch() without explicit method → GET (but skip if already matched with method)
                    fetch_method_urls = {m.group(1) for m in _FETCH_METHOD_RE.finditer(content)}
                    for match in _FETCH_RE.finditer(content):
                        url = match.group(1)
                        if url in fetch_method_urls:
                            continue
                        path = _extract_path_from_url(url)
                        norm_path = normalize_http_path(path)
                        contract_id = f"http::GET::{norm_path}"
                        contracts.append(Contract(
                            repo=repo_alias,
                            contract_id=contract_id,
                            contract_type="http",
                            role="consumer",
                            file_path=rel_path,
                            symbol_name=f"fetch:GET {url}",
                            confidence=0.75,
                            service=None,
                            meta={"method": "GET", "path": norm_path, "client": "fetch"},
                        ))

                    # axios
                    for match in _AXIOS_RE.finditer(content):
                        method = match.group(1).upper()
                        url = match.group(2)
                        path = _extract_path_from_url(url)
                        norm_path = normalize_http_path(path)
                        contract_id = f"http::{method}::{norm_path}"
                        contracts.append(Contract(
                            repo=repo_alias,
                            contract_id=contract_id,
                            contract_type="http",
                            role="consumer",
                            file_path=rel_path,
                            symbol_name=f"axios:{method} {url}",
                            confidence=0.75,
                            service=None,
                            meta={"method": method, "path": norm_path, "client": "axios"},
                        ))

                    # requests / httpx
                    for match in _REQUESTS_RE.finditer(content):
                        method = match.group(1).upper()
                        url = match.group(2)
                        path = _extract_path_from_url(url)
                        norm_path = normalize_http_path(path)
                        contract_id = f"http::{method}::{norm_path}"
                        contracts.append(Contract(
                            repo=repo_alias,
                            contract_id=contract_id,
                            contract_type="http",
                            role="consumer",
                            file_path=rel_path,
                            symbol_name=f"requests:{method} {url}",
                            confidence=0.75,
                            service=None,
                            meta={"method": method, "path": norm_path, "client": "requests"},
                        ))

        return contracts
