"""gRPC contract extraction.

Scans ``.proto`` files for service/rpc declarations (providers) and
language-specific source files for gRPC server registrations (providers) and
client stubs (consumers).
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

_log = logging.getLogger("repowise.workspace.extractors.grpc")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BLOCKED_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", "target", "vendor", ".next", ".nuxt",
    ".tox", ".mypy_cache", ".gradle", ".mvn", "out", "bin",
})

_MAX_FILE_SIZE = 512 * 1024

_PROTO_EXTENSIONS = frozenset({".proto"})

_SOURCE_EXTENSIONS = frozenset({
    ".go", ".java", ".py", ".ts", ".tsx", ".js", ".jsx",
})

_ALL_EXTENSIONS = _PROTO_EXTENSIONS | _SOURCE_EXTENSIONS


# ---------------------------------------------------------------------------
# Proto parsing (brace-depth counter)
# ---------------------------------------------------------------------------


def _extract_service_blocks(content: str) -> list[tuple[str, str]]:
    """Extract ``(service_name, body)`` pairs from a ``.proto`` file.

    Uses brace-depth counting so nested braces in comments, options, or
    message bodies don't break parsing.
    """
    results: list[tuple[str, str]] = []
    header_re = re.compile(r"service\s+(\w+)\s*\{")

    for header_match in header_re.finditer(content):
        service_name = header_match.group(1)
        body_start = header_match.end()
        depth = 1
        pos = body_start
        in_line_comment = False
        in_block_comment = False
        while pos < len(content) and depth > 0:
            ch = content[pos]
            # Track comment state to ignore braces inside comments
            if in_line_comment:
                if ch == "\n":
                    in_line_comment = False
            elif in_block_comment:
                if ch == "*" and pos + 1 < len(content) and content[pos + 1] == "/":
                    in_block_comment = False
                    pos += 1  # skip the '/'
            elif ch == "/" and pos + 1 < len(content):
                if content[pos + 1] == "/":
                    in_line_comment = True
                elif content[pos + 1] == "*":
                    in_block_comment = True
                    pos += 1  # skip the '*'
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            pos += 1
        if depth != 0:
            continue  # incomplete/malformed service block
        body = content[body_start : pos - 1]
        results.append((service_name, body))

    return results


def _parse_proto_file(content: str) -> tuple[str, list[tuple[str, str, list[str]]]]:
    """Parse a proto file, returning ``(package, services)``.

    Each service is ``(service_name, full_service_path, [method_names])``.
    """
    # Extract package
    pkg_match = re.search(r"^\s*package\s+([\w.]+)\s*;", content, re.MULTILINE)
    package = pkg_match.group(1) if pkg_match else ""

    services: list[tuple[str, str, list[str]]] = []
    rpc_re = re.compile(r"rpc\s+(\w+)\s*\(")

    for svc_name, body in _extract_service_blocks(content):
        full_path = f"{package}.{svc_name}" if package else svc_name
        methods = [m.group(1) for m in rpc_re.finditer(body)]
        services.append((svc_name, full_path, methods))

    return package, services


# ---------------------------------------------------------------------------
# Language-specific patterns
# ---------------------------------------------------------------------------

# Go providers: pb.RegisterAuthServiceServer(grpcServer, &impl{})
_GO_PROVIDER_RE = re.compile(r"\.Register(\w+)Server\s*\(")
# Go consumers: pb.NewAuthServiceClient(conn)
_GO_CONSUMER_RE = re.compile(r"\.New(\w+)Client\s*\(")

# Java providers: extends AuthServiceGrpc.AuthServiceImplBase
_JAVA_PROVIDER_RE = re.compile(r"extends\s+(\w+)Grpc\.(\w+)ImplBase")
# Java @GrpcService annotation
_JAVA_GRPC_SERVICE_RE = re.compile(r"@GrpcService")
# Java consumers: AuthServiceGrpc.newBlockingStub(channel)
_JAVA_CONSUMER_RE = re.compile(r"(\w+)Grpc\.new(?:Blocking|Future)?Stub\s*\(")

# Python providers: add_AuthServiceServicer_to_server(servicer, server)
_PY_PROVIDER_RE = re.compile(r"add_(\w+?)Servicer_to_server\s*\(")
# Python consumers: AuthServiceStub(channel)
_PY_CONSUMER_RE = re.compile(r"(\w+)Stub\s*\(")

# TypeScript providers: @GrpcMethod('AuthService', 'Login')
_TS_PROVIDER_RE = re.compile(r"@GrpcMethod\s*\(\s*'(\w+)'\s*,\s*'(\w+)'\s*\)")


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


class GrpcExtractor:
    """Extract gRPC contracts from proto files and language-specific source."""

    def extract(self, repo_path: Path, repo_alias: str = "") -> list[Contract]:
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

                # --- Proto files → providers ---
                if suffix in _PROTO_EXTENSIONS:
                    _package, services = _parse_proto_file(content)
                    for _svc_name, full_path, methods in services:
                        for method in methods:
                            contract_id = f"grpc::{full_path}/{method}"
                            contracts.append(Contract(
                                repo=repo_alias,
                                contract_id=contract_id,
                                contract_type="grpc",
                                role="provider",
                                file_path=rel_path,
                                symbol_name=f"{full_path}/{method}",
                                confidence=0.85,
                                service=None,
                                meta={
                                    "package": _package,
                                    "service": _svc_name,
                                    "method": method,
                                    "source": "proto",
                                },
                            ))

                # --- Go ---
                elif suffix == ".go":
                    for m in _GO_PROVIDER_RE.finditer(content):
                        svc = m.group(1)
                        contract_id = f"grpc::{svc}/*"
                        contracts.append(Contract(
                            repo=repo_alias,
                            contract_id=contract_id,
                            contract_type="grpc",
                            role="provider",
                            file_path=rel_path,
                            symbol_name=f"go:Register{svc}Server",
                            confidence=0.8,
                            service=None,
                            meta={"service": svc, "source": "go_register"},
                        ))

                    for m in _GO_CONSUMER_RE.finditer(content):
                        svc = m.group(1)
                        contract_id = f"grpc::{svc}/*"
                        contracts.append(Contract(
                            repo=repo_alias,
                            contract_id=contract_id,
                            contract_type="grpc",
                            role="consumer",
                            file_path=rel_path,
                            symbol_name=f"go:New{svc}Client",
                            confidence=0.7,
                            service=None,
                            meta={"service": svc, "source": "go_client"},
                        ))

                # --- Java ---
                elif suffix == ".java":
                    for m in _JAVA_PROVIDER_RE.finditer(content):
                        svc = m.group(1)
                        contract_id = f"grpc::{svc}/*"
                        contracts.append(Contract(
                            repo=repo_alias,
                            contract_id=contract_id,
                            contract_type="grpc",
                            role="provider",
                            file_path=rel_path,
                            symbol_name=f"java:extends {svc}Grpc.ImplBase",
                            confidence=0.8,
                            service=None,
                            meta={"service": svc, "source": "java_extends"},
                        ))

                    for m in _JAVA_CONSUMER_RE.finditer(content):
                        svc = m.group(1)
                        contract_id = f"grpc::{svc}/*"
                        contracts.append(Contract(
                            repo=repo_alias,
                            contract_id=contract_id,
                            contract_type="grpc",
                            role="consumer",
                            file_path=rel_path,
                            symbol_name=f"java:{svc}Grpc.newStub",
                            confidence=0.7,
                            service=None,
                            meta={"service": svc, "source": "java_stub"},
                        ))

                # --- Python ---
                elif suffix == ".py":
                    for m in _PY_PROVIDER_RE.finditer(content):
                        svc = m.group(1)
                        contract_id = f"grpc::{svc}/*"
                        contracts.append(Contract(
                            repo=repo_alias,
                            contract_id=contract_id,
                            contract_type="grpc",
                            role="provider",
                            file_path=rel_path,
                            symbol_name=f"py:add_{svc}Servicer_to_server",
                            confidence=0.8,
                            service=None,
                            meta={"service": svc, "source": "py_servicer"},
                        ))

                    for m in _PY_CONSUMER_RE.finditer(content):
                        svc = m.group(1)
                        # Filter common false positives (Mock*, Test*, Fake* prefixes)
                        svc_lower = svc.lower()
                        if any(svc_lower.startswith(p) for p in ("mock", "test", "fake")):
                            continue
                        contract_id = f"grpc::{svc}/*"
                        contracts.append(Contract(
                            repo=repo_alias,
                            contract_id=contract_id,
                            contract_type="grpc",
                            role="consumer",
                            file_path=rel_path,
                            symbol_name=f"py:{svc}Stub",
                            confidence=0.7,
                            service=None,
                            meta={"service": svc, "source": "py_stub"},
                        ))

                # --- TypeScript ---
                elif suffix in (".ts", ".tsx", ".js", ".jsx"):
                    for m in _TS_PROVIDER_RE.finditer(content):
                        svc = m.group(1)
                        method = m.group(2)
                        contract_id = f"grpc::{svc}/{method}"
                        contracts.append(Contract(
                            repo=repo_alias,
                            contract_id=contract_id,
                            contract_type="grpc",
                            role="provider",
                            file_path=rel_path,
                            symbol_name=f"ts:@GrpcMethod('{svc}', '{method}')",
                            confidence=0.8,
                            service=None,
                            meta={
                                "service": svc,
                                "method": method,
                                "source": "ts_decorator",
                            },
                        ))

        return contracts
