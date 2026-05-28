"""JVM-wide dynamic-hint extractor (reflection, mocking, MapStruct, Boot).

The Spring-specific extractor handles ``getBean`` / ``@Bean`` factories.
This module covers the JVM patterns that aren't Spring-bound:

- **Reflection** — ``Class.forName("a.b.C")`` and
  ``Mockito.mock(X.class)`` / ``Mockito.spy(X.class)`` /
  ``mockk<X>()`` (Kotlin).
- **MapStruct factories** — ``Mappers.getMapper(XMapper.class)`` resolves
  the runtime impl ``XMapperImpl`` (synthesised in Phase 2) plus the
  ``XMapper`` interface file.
- **Boot entry point** — ``SpringApplication.run(X.class, args)`` and the
  Kotlin ``runApplication<X>(args)`` form mark X as a Boot entry.
- **Jackson polymorphic deserialisation** — ``objectMapper.readValue(json,
  X.class)`` / ``new Gson().fromJson(json, X.class)`` produce weak edges
  to X's defining file so a class that is only ever deserialised from JSON
  is not flagged as dead.

The detection gate is *any* ``.java`` or ``.kt`` file in the repo (the
patterns are JVM-wide, not Spring-only), keeping the scan free for
non-JVM repos.
"""

from __future__ import annotations

import re
from pathlib import Path

from .base import DynamicEdge, DynamicHintExtractor

_SKIP_DIRS = {"build", "target", "out", "node_modules", ".git", ".gradle", ".idea"}

_TYPE_DECL_RE = re.compile(r"\b(?:class|interface|record|enum)\s+([A-Z]\w*)")
_KOTLIN_TYPE_DECL_RE = re.compile(r"\b(?:class|interface|object)\s+([A-Z]\w*)")

# Class.forName("a.b.C") / Class.forName("a.b.C", true, classLoader)
_CLASS_FORNAME_RE = re.compile(r"Class\.forName\s*\(\s*[\"']([\w.$]+)[\"']")

# Mockito.mock(X.class) / Mockito.spy(X.class) / Mockito.mock(X.class, ...)
_MOCKITO_RE = re.compile(r"Mockito\.(?:mock|spy)\s*\(\s*([A-Z]\w*)\s*\.class")
# Kotlin: mockk<X>() / spyk<X>() / spyk(X::class)
_MOCKK_RE = re.compile(r"\b(?:mockk|spyk)\s*<\s*([A-Z]\w*)\s*>")

# MapStruct: Mappers.getMapper(XMapper.class)
_MAPSTRUCT_RE = re.compile(r"Mappers\.getMapper\s*\(\s*([A-Z]\w*)\s*\.class")

# Spring Boot entry: SpringApplication.run(MyApp.class, args)
_BOOT_RUN_RE = re.compile(r"SpringApplication\.run\s*\(\s*([A-Z]\w*)\s*\.class")
# Kotlin: runApplication<MyApp>(*args)
_KOTLIN_RUN_APP_RE = re.compile(r"\brunApplication\s*<\s*([A-Z]\w*)\s*>")

# Jackson / Gson: objectMapper.readValue(json, X.class) / gson.fromJson(json, X.class)
_JSON_READ_RE = re.compile(
    r"\.(?:readValue|fromJson|convertValue|treeToValue)\s*\([^)]*?([A-Z]\w*)\s*\.class"
)

# Constructor.newInstance / Method.invoke type references — best-effort.
_REFLECTION_INVOKE_RE = re.compile(
    r"\.getDeclared(?:Method|Field|Constructor)\s*\(\s*[\"'](\w+)[\"']"
)


class JvmDynamicHints(DynamicHintExtractor):
    """Discover JVM reflection / Mockito / MapStruct / Boot / Jackson patterns."""

    name = "jvm"

    def extract(self, repo_root: Path) -> list[DynamicEdge]:
        edges: list[DynamicEdge] = []

        # Build maps: short type name → file, AND fully-qualified type name → file.
        type_to_file: dict[str, str] = {}
        fqn_to_file: dict[str, str] = {}
        sources: list[tuple[Path, str, str]] = []  # (path, text, lang)
        repo_root_resolved = repo_root.resolve()

        for ext, lang in ((".java", "java"), (".kt", "kotlin")):
            for src in self._rglob(repo_root, f"*{ext}"):
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

                # Extract package declaration to build FQNs.
                pkg_match = re.search(r"^\s*package\s+([\w.]+)", text, re.MULTILINE)
                pkg = pkg_match.group(1) if pkg_match else ""

                pattern = _KOTLIN_TYPE_DECL_RE if lang == "kotlin" else _TYPE_DECL_RE
                for match in pattern.finditer(text):
                    name = match.group(1)
                    type_to_file.setdefault(name, rel)
                    if pkg:
                        fqn_to_file.setdefault(f"{pkg}.{name}", rel)

        def _emit(src_rel: str, target_rel: str | None, suffix: str) -> None:
            if target_rel and target_rel != src_rel:
                edges.append(
                    DynamicEdge(
                        source=src_rel,
                        target=target_rel,
                        edge_type="dynamic_uses",
                        hint_source=f"{self.name}:{suffix}",
                    )
                )

        for src, text, lang in sources:
            try:
                rel = src.resolve().relative_to(repo_root_resolved).as_posix()
            except ValueError:
                continue

            for match in _CLASS_FORNAME_RE.finditer(text):
                fqn = match.group(1)
                target = fqn_to_file.get(fqn) or type_to_file.get(fqn.rsplit(".", 1)[-1])
                _emit(rel, target, "class_forname")

            for match in _MOCKITO_RE.finditer(text):
                _emit(rel, type_to_file.get(match.group(1)), "mockito")

            for match in _MOCKK_RE.finditer(text):
                _emit(rel, type_to_file.get(match.group(1)), "mockk")

            for match in _MAPSTRUCT_RE.finditer(text):
                _emit(rel, type_to_file.get(match.group(1)), "mapstruct")

            for match in _BOOT_RUN_RE.finditer(text):
                _emit(rel, type_to_file.get(match.group(1)), "spring_boot_run")

            for match in _KOTLIN_RUN_APP_RE.finditer(text):
                _emit(rel, type_to_file.get(match.group(1)), "spring_boot_run")

            for match in _JSON_READ_RE.finditer(text):
                _emit(rel, type_to_file.get(match.group(1)), "jackson_readvalue")

        return edges
