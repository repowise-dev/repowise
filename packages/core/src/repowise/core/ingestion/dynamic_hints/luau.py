"""Dynamic-hint extractor for Luau / Roblox patterns."""

from __future__ import annotations

import re
from pathlib import Path

from .base import DynamicEdge, DynamicHintExtractor

_SKIP_DIRS = {".git", "node_modules", "out", "build", "Packages"}

# game:GetService("ReplicatedStorage")
_GET_SERVICE_RE = re.compile(r"game\s*:\s*GetService\s*\(\s*[\"']([A-Za-z_]\w*)[\"']")
# setmetatable(t, {__index = Other})
_SETMETATABLE_RE = re.compile(
    r"setmetatable\s*\([^,]+,\s*\{[^}]*__index\s*=\s*([A-Za-z_]\w*)"
)
# require(script.Foo) — already handled by static resolver, here we capture
# absolute Roblox paths that fall through.
_REQUIRE_GAME_RE = re.compile(r"require\s*\(\s*game[.:]([A-Za-z_][\w.:]*)\)")


class LuauDynamicHints(DynamicHintExtractor):
    """Discover Luau metatable / GetService / Roblox-instance patterns."""

    name = "luau"

    def extract(self, repo_root: Path) -> list[DynamicEdge]:
        edges: list[DynamicEdge] = []

        sources: list[tuple[Path, str]] = []
        repo_root_resolved = repo_root.resolve()
        # luau plus lua extensions
        for ext in (".luau", ".lua"):
            for src in repo_root.rglob(f"*{ext}"):
                try:
                    rel_path = src.resolve().relative_to(repo_root_resolved)
                except ValueError:
                    continue
                if any(part in _SKIP_DIRS for part in rel_path.parts):
                    continue
                try:
                    text = src.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                sources.append((src, text))

        # Build a map of file stem → file path for metatable __index resolution
        stem_to_file: dict[str, str] = {}
        for src, _ in sources:
            try:
                rel = src.resolve().relative_to(repo_root.resolve()).as_posix()
            except ValueError:
                continue
            stem_to_file.setdefault(src.stem, rel)

        for src, text in sources:
            try:
                rel = src.resolve().relative_to(repo_root.resolve()).as_posix()
            except ValueError:
                continue

            for match in _GET_SERVICE_RE.finditer(text):
                edges.append(DynamicEdge(
                    source=rel,
                    target=f"external:roblox_service:{match.group(1)}",
                    edge_type="dynamic_uses",
                    hint_source=f"{self.name}:get_service",
                ))

            for match in _SETMETATABLE_RE.finditer(text):
                target = stem_to_file.get(match.group(1))
                if target and target != rel:
                    edges.append(DynamicEdge(
                        source=rel, target=target,
                        edge_type="dynamic_uses",
                        hint_source=f"{self.name}:metatable_index",
                    ))

            for match in _REQUIRE_GAME_RE.finditer(text):
                edges.append(DynamicEdge(
                    source=rel,
                    target=f"external:roblox_path:{match.group(1)}",
                    edge_type="dynamic_imports",
                    hint_source=f"{self.name}:require_game",
                ))

        return edges
