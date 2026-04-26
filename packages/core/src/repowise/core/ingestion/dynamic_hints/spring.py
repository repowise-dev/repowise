"""Dynamic-hint extractor for Spring DI patterns (Java/Kotlin)."""

from __future__ import annotations

import re
from pathlib import Path

from .base import DynamicEdge, DynamicHintExtractor

_SKIP_DIRS = {"build", "target", "out", "node_modules", ".git", ".gradle", ".idea"}

_TYPE_DECL_RE = re.compile(
    r"\b(?:class|interface|record|enum)\s+([A-Z]\w*)"
)
_KOTLIN_TYPE_DECL_RE = re.compile(
    r"\b(?:class|interface|object)\s+([A-Z]\w*)"
)

# applicationContext.getBean(Foo.class) / getBean("foo", Foo.class)
_GETBEAN_CLASS_RE = re.compile(r"getBean\s*\(\s*(?:[\"'][^\"']*[\"']\s*,\s*)?([A-Z]\w*)\s*(?:\.class|::class(?:\.java)?)")
# getBean("beanName") — name-based, can't resolve to a class file
_GETBEAN_STRING_RE = re.compile(r"getBean\s*\(\s*[\"']([^\"']+)[\"']\s*\)")

# @Bean factory return type — pick the line preceding @Bean for the method signature
_BEAN_METHOD_RE = re.compile(
    r"@Bean[^\n]*\n[^\n]*?\b(?:public\s+|private\s+|protected\s+|static\s+|final\s+)*"
    r"([A-Z]\w*)\s+\w+\s*\(",
)
_BEAN_METHOD_KOTLIN_RE = re.compile(
    r"@Bean[^\n]*\n[^\n]*?\bfun\s+\w+\s*\([^)]*\)\s*:\s*([A-Z]\w*)",
)


class SpringDynamicHints(DynamicHintExtractor):
    """Discover Spring `getBean`/`@Bean` runtime-resolved dependencies."""

    name = "spring"

    def extract(self, repo_root: Path) -> list[DynamicEdge]:
        edges: list[DynamicEdge] = []

        type_to_file: dict[str, str] = {}
        sources: list[tuple[Path, str, str]] = []  # (path, text, lang)
        repo_root_resolved = repo_root.resolve()
        for ext, lang in ((".java", "java"), (".kt", "kotlin")):
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
                rel = rel_path.as_posix()
                sources.append((src, text, lang))
                pattern = _KOTLIN_TYPE_DECL_RE if lang == "kotlin" else _TYPE_DECL_RE
                for match in pattern.finditer(text):
                    type_to_file.setdefault(match.group(1), rel)

        # Gate: only fire if any file imports org.springframework
        if not any("org.springframework" in t for _, t, _ in sources):
            return edges

        for src, text, lang in sources:
            try:
                rel = src.resolve().relative_to(repo_root.resolve()).as_posix()
            except ValueError:
                continue

            for match in _GETBEAN_CLASS_RE.finditer(text):
                target = type_to_file.get(match.group(1))
                if target and target != rel:
                    edges.append(
                        DynamicEdge(
                            source=rel, target=target,
                            edge_type="dynamic_uses",
                            hint_source=f"{self.name}:get_bean",
                        )
                    )

            for match in _GETBEAN_STRING_RE.finditer(text):
                edges.append(
                    DynamicEdge(
                        source=rel,
                        target=f"external:bean:{match.group(1)}",
                        edge_type="dynamic_uses",
                        hint_source=f"{self.name}:get_bean_string",
                    )
                )

            bean_re = _BEAN_METHOD_KOTLIN_RE if lang == "kotlin" else _BEAN_METHOD_RE
            for match in bean_re.finditer(text):
                target = type_to_file.get(match.group(1))
                if target and target != rel:
                    edges.append(
                        DynamicEdge(
                            source=rel, target=target,
                            edge_type="dynamic_uses",
                            hint_source=f"{self.name}:bean_factory",
                        )
                    )

        return edges
