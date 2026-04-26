"""Dynamic-hint extractor for Ruby reflective patterns."""

from __future__ import annotations

import re
from pathlib import Path

from .base import DynamicEdge, DynamicHintExtractor

_SKIP_DIRS = {"vendor", "node_modules", ".git", "tmp", "log"}

# Object.send(:method) / .send("method") / .public_send(...)
_SEND_RE = re.compile(r"\.(?:public_)?send\s*\(\s*[:\"']([A-Za-z_]\w*)")
# Kernel.const_get / Object.const_get / SomeModule.const_get
_CONST_GET_RE = re.compile(r"const_get\s*\(\s*[:\"']?([A-Z]\w*)")
# define_method(:name) — runtime method definition
_DEFINE_METHOD_RE = re.compile(r"define_method\s*\(\s*[:\"']([A-Za-z_]\w*)")
# delegate :foo, to: :bar — ActiveSupport
_DELEGATE_RE = re.compile(r"delegate\s+:([A-Za-z_]\w*)\s*,\s*to:\s*:([A-Za-z_]\w*)")

# Constant/class declarations: class Foo / module Foo / Foo = Class.new
_CLASS_DECL_RE = re.compile(r"^\s*(?:class|module)\s+([A-Z]\w*)", re.MULTILINE)
_METHOD_DECL_RE = re.compile(r"^\s*def\s+(?:self\.)?([A-Za-z_]\w*)", re.MULTILINE)


class RubyDynamicHints(DynamicHintExtractor):
    """Discover Ruby reflective method dispatch and constant lookup."""

    name = "ruby"

    def extract(self, repo_root: Path) -> list[DynamicEdge]:
        edges: list[DynamicEdge] = []

        const_to_file: dict[str, str] = {}
        method_to_files: dict[str, list[str]] = {}
        rb_files: list[tuple[Path, str]] = []
        for src in repo_root.rglob("*.rb"):
            if any(part in _SKIP_DIRS for part in src.parts):
                continue
            try:
                text = src.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            try:
                rel = src.resolve().relative_to(repo_root.resolve()).as_posix()
            except ValueError:
                continue
            rb_files.append((src, text))
            for match in _CLASS_DECL_RE.finditer(text):
                const_to_file.setdefault(match.group(1), rel)
            for match in _METHOD_DECL_RE.finditer(text):
                method_to_files.setdefault(match.group(1), []).append(rel)

        for src, text in rb_files:
            try:
                rel = src.resolve().relative_to(repo_root.resolve()).as_posix()
            except ValueError:
                continue

            seen_targets: set[tuple[str, str]] = set()

            def _emit(target: str, hint: str) -> None:
                key = (target, hint)
                if key in seen_targets:
                    return
                seen_targets.add(key)
                if target != rel:
                    edges.append(
                        DynamicEdge(
                            source=rel, target=target,
                            edge_type="dynamic_uses",
                            hint_source=f"{self.name}:{hint}",
                        )
                    )

            for match in _SEND_RE.finditer(text):
                for target in method_to_files.get(match.group(1), []):
                    _emit(target, "send")

            for match in _CONST_GET_RE.finditer(text):
                target = const_to_file.get(match.group(1))
                if target:
                    _emit(target, "const_get")

            for match in _DEFINE_METHOD_RE.finditer(text):
                # Self-emission only — define_method declares a method on the
                # current scope. Record as external symbol marker.
                edges.append(
                    DynamicEdge(
                        source=rel,
                        target=f"external:ruby_method:{match.group(1)}",
                        edge_type="dynamic_uses",
                        hint_source=f"{self.name}:define_method",
                    )
                )

            for match in _DELEGATE_RE.finditer(text):
                for target in method_to_files.get(match.group(1), []):
                    _emit(target, "delegate")

        return edges
