from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .base import DynamicEdge, DynamicHintExtractor


def _json_loads_lenient(text: str) -> Any:
    """Try json.loads; on failure, strip trailing commas and retry."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        cleaned = re.sub(r",\s*([}\]])", r"\1", text)
        return json.loads(cleaned)


def _collect_export_strings(obj: Any) -> list[str]:
    """Recursively collect string values from an exports object."""
    results: list[str] = []
    if isinstance(obj, str):
        results.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            results.extend(_collect_export_strings(v))
    elif isinstance(obj, list):
        for item in obj:
            results.extend(_collect_export_strings(item))
    return results


class NodeDynamicHints(DynamicHintExtractor):
    name = "node_package"

    def extract(self, repo_root: Path) -> list[DynamicEdge]:
        edges: list[DynamicEdge] = []
        edges.extend(self._scan_package_json(repo_root))
        edges.extend(self._scan_tsconfig(repo_root))
        return edges

    def _scan_package_json(self, repo_root: Path) -> list[DynamicEdge]:
        edges: list[DynamicEdge] = []

        for pkg_file in repo_root.rglob("package.json"):
            # Skip node_modules
            if "node_modules" in pkg_file.parts:
                continue
            try:
                text = pkg_file.read_text(encoding="utf-8", errors="ignore")
                data = json.loads(text)
                rel_pkg = pkg_file.relative_to(repo_root).as_posix()
                pkg_dir = pkg_file.parent
            except Exception:
                continue

            # Collect entry point fields and exports strings
            candidates: list[str] = []
            for field in ("main", "module", "browser"):
                val = data.get(field)
                if isinstance(val, str):
                    candidates.append(val)

            exports = data.get("exports")
            if exports is not None:
                candidates.extend(_collect_export_strings(exports))

            for candidate in candidates:
                if not candidate.startswith("."):
                    # Only resolve relative paths
                    continue
                resolved = (pkg_dir / candidate).resolve()
                try:
                    rel_resolved = resolved.relative_to(repo_root.resolve()).as_posix()
                except ValueError:
                    continue
                if resolved.exists():
                    edges.append(DynamicEdge(
                        source=rel_pkg,
                        target=rel_resolved,
                        edge_type="dynamic_imports",
                        hint_source=self.name,
                    ))

        return edges

    def _scan_tsconfig(self, repo_root: Path) -> list[DynamicEdge]:
        edges: list[DynamicEdge] = []

        for tsconfig in repo_root.rglob("tsconfig*.json"):
            if "node_modules" in tsconfig.parts:
                continue
            try:
                text = tsconfig.read_text(encoding="utf-8", errors="ignore")
                data = _json_loads_lenient(text)
                rel_tsconfig = tsconfig.relative_to(repo_root).as_posix()
                tsconfig_dir = tsconfig.parent
            except Exception:
                continue

            compiler_options = data.get("compilerOptions", {})
            if not isinstance(compiler_options, dict):
                continue

            paths = compiler_options.get("paths")
            if not isinstance(paths, dict):
                continue

            base_url = compiler_options.get("baseUrl", ".")
            base_dir = (tsconfig_dir / base_url).resolve()

            for _alias, targets in paths.items():
                if not isinstance(targets, list):
                    continue
                for pattern in targets:
                    if not isinstance(pattern, str):
                        continue
                    # Drop trailing /* from glob patterns
                    clean = pattern.rstrip("/*").rstrip("/")
                    if not clean:
                        continue
                    resolved = (base_dir / clean).resolve()
                    try:
                        rel_resolved = resolved.relative_to(repo_root.resolve()).as_posix()
                    except ValueError:
                        continue
                    if resolved.exists():
                        edges.append(DynamicEdge(
                            source=rel_tsconfig,
                            target=rel_resolved,
                            edge_type="dynamic_imports",
                            hint_source=self.name,
                        ))

        return edges
