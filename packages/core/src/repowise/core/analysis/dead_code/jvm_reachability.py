"""Package-granular reachability for JVM source (Java + Kotlin).

The generic unreachable-file pass treats ``in_degree == 0`` as dead. For
JVM source that view misses three real shapes:

- **Sibling-rescued packages.** Java's import resolver fans out to every
  file in a package, so an imported package is normally all-or-nothing.
  But Lombok / record / data-class synthesis and Kotlin top-level files
  occasionally land in a package whose other files carry the importer
  edge — package-level reachability rescues them as a belt-and-suspenders
  pass that mirrors :mod:`go_reachability`.
- **Stereotype-annotated classes.** A class with ``@Component`` /
  ``@Service`` / ``@RestController`` / ``@Entity`` / ``@QuarkusMain`` /
  ``@SpringBootApplication`` (and friends) is instantiated by the runtime
  via reflection — never by a static call edge. The graph stores those
  annotations as decorators on the class symbol; the helper checks the
  file's defined symbols.
- **``main(String[])`` carriers.** A file declaring a ``public static void
  main(String[])`` is the JAR entry point.

META-INF/services, JPMS ``provides ... with``, and Spring Boot autoconfig
imports are *not* re-checked here — those are stamped as
``is_reachability_root`` on the file node during the JVM warmup (see
:mod:`graph_warmups`), and the analyzer's root skip honours them before
reaching this hook.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import PurePosixPath
from typing import Any

from ...entry_candidacy import is_reachability_root
from ...ingestion.decorator_names import decorator_base
from .entry_shape import defines_main_function

# Class-level annotation names (without the leading ``@``) that mark the
# bearing class as runtime-instantiated. Kept compact; full Spring
# stereotype recognition + meta-annotation resolution lands in Phase 4
# framework_edges. Anything matched here is "the runtime will load this".
_STEREOTYPE_ANNOTATIONS: frozenset[str] = frozenset({
    "Component",
    "Service",
    "Repository",
    "Controller",
    "RestController",
    "Configuration",
    "ControllerAdvice",
    "RestControllerAdvice",
    "SpringBootApplication",
    "SpringBootConfiguration",
    "EnableAutoConfiguration",
    "Mapper",                   # MapStruct + MyBatis
    "Entity",
    "MappedSuperclass",
    "Embeddable",
    "Converter",
    "QuarkusMain",
    "QuarkusTest",
    "QuarkusIntegrationTest",
    "MicronautApplication",
    "MicronautTest",
    "WebMvcTest",
    "DataJpaTest",
    "WebServlet",
    "WebFilter",
    "WebListener",
    "Path",                     # JAX-RS
    "Provider",
    "ApplicationScoped",
    "RequestScoped",
    "SessionScoped",
    "Singleton",
    "Stateless",
    "Stateful",
    "Dependent",
    "Factory",
    "Endpoint",
    "RestControllerEndpoint",
    "RegisterForReflection",
    "Module",                   # Dagger / Hilt
    "GlideModule",              # Glide annotation processor
    # JMH runs these by reflection; ``Benchmark`` sits on the methods.
    "State",
    "BenchmarkMode",
    "Benchmark",
})


def _pkg_dir(node: str) -> str:
    """Repo-relative POSIX directory of a JVM source file ("" = repo root)."""
    parent = PurePosixPath(node).parent.as_posix()
    return "" if parent == "." else parent


def build_jvm_package_files(graph: Any) -> dict[str, list[str]]:
    """Group every ``.java`` and ``.kt`` file node in *graph* by directory.

    Directory is the JVM compilation unit's package on disk (modulo the
    ``src/main/java`` vs ``src/main/kotlin`` srcDir prefix — both languages
    live alongside each other in the same package directory).
    """
    packages: dict[str, list[str]] = {}
    for node in graph.nodes():
        s = str(node)
        if not (s.endswith(".java") or s.endswith(".kt")):
            continue
        packages.setdefault(_pkg_dir(s), []).append(s)
    return packages


def _annotation_base(decorator: str) -> str:
    """Strip ``@``, generic args, arg list and trailing modifiers from a decorator string."""
    s = decorator_base(decorator).partition("<")[0]
    # Spring annotations may be FQN ('org.springframework.stereotype.Service');
    # we match the bare type name.
    if "." in s:
        s = s.rsplit(".", 1)[1]
    return s


# The shape follows the JVM launcher rules: one ``String[]`` parameter
# (``String[] a``, ``String... a``, ``String a[]``, annotations allowed) or,
# since Java 25 (JEP 512), no parameters. The graph keeps no ``static`` keyword
# for Java, so the parameter shape is what tells the entry point from an
# ordinary method that happens to be named ``main``.
_JAVA_ANNOTATION = r"@[\w.]+(?:\([^)]*\))?"
_JAVA_MAIN_PARAMS = re.compile(
    rf"^main\(\s*(?:(?:(?:{_JAVA_ANNOTATION}|final)\s+)*(?:java\.lang\.)?String"
    rf"(?:\s*(?:{_JAVA_ANNOTATION}\s*)?(?:\[\s*\]|\.\.\.)\s*\w+|\s+\w+\s*\[\s*\])\s*)?\)"
)


def _defined_symbols(graph: Any, file_node: str) -> Iterator[dict[str, Any]]:
    """The attribute dicts of the symbols *file_node* defines."""
    for succ in graph.successors(file_node):
        succ_data = graph.nodes.get(succ, {})
        if succ_data.get("node_type") != "symbol":
            continue
        if graph.get_edge_data(file_node, succ, {}).get("edge_type") == "defines":
            yield succ_data


def _is_main_method(is_java: bool, sym: dict[str, Any]) -> bool:
    # The symbol name, not the id: an overloaded Java id reads ``main#1(String[])``.
    if sym.get("name") != "main" or sym.get("kind") != "method":
        return False
    if is_java:
        return bool(_JAVA_MAIN_PARAMS.match(sym.get("signature") or ""))
    # Kotlin: a ``@JvmStatic`` main in an object. A class member ``fun main()``
    # is an ordinary method.
    return "JvmStatic" in _annotation_names(sym)


def _annotation_names(sym: dict[str, Any]) -> set[str]:
    # One modifiers blob holds every annotation of the declaration.
    return {
        _annotation_base(token)
        for dec in sym.get("decorators") or []
        for token in dec.split("@")
        if token.strip()
    }


def defines_main(graph: Any, file_node: str) -> bool:
    """True if *file_node* is a Java or Kotlin file declaring a program ``main``."""
    if not file_node.endswith((".java", ".kt")):
        return False
    is_java = file_node.endswith(".java")
    # Kotlin's top-level ``fun main`` is a function, not a method.
    if not is_java and defines_main_function(graph, file_node):
        return True
    return any(_is_main_method(is_java, sym) for sym in _defined_symbols(graph, file_node))


def _file_defines_entry_class(graph: Any, file_node: str) -> bool:
    """True if *file_node* defines a class with a stereotype annotation
    or a ``main`` method.
    """
    if any(
        not _STEREOTYPE_ANNOTATIONS.isdisjoint(_annotation_names(sym))
        for sym in _defined_symbols(graph, file_node)
    ):
        return True
    return defines_main(graph, file_node)


def is_jvm_file_reachable(
    node: str,
    graph: Any,
    package_files: dict[str, list[str]],
) -> bool:
    """Return True if a JVM source file is reachable beyond ``in_degree``.

    Called from the analyzer only for ``.java`` / ``.kt`` nodes that have
    already passed the generic skips (entry point, test, never-flag). The
    file is rescued when:

    - it has an inbound graph edge (degenerate fast path);
    - any sibling in the same package directory has an inbound edge or is
      an entry-point file (sibling-rescued package);
    - it defines a class annotated with a runtime-loaded stereotype
      (Spring/Jakarta/Quarkus/Micronaut/JPA);
    - it defines a ``main`` method.
    """
    # Called, not imported at module level: file_reachability imports this module.
    from .file_reachability import has_dependency_importer

    if has_dependency_importer(graph, node):
        return True

    if _file_defines_entry_class(graph, node):
        return True

    for sibling in package_files.get(_pkg_dir(node), ()):
        if sibling == node:
            continue
        sib_data = graph.nodes.get(sibling, {})
        if has_dependency_importer(graph, sibling):
            return True
        if is_reachability_root(sib_data):
            return True
        if _file_defines_entry_class(graph, sibling):
            return True

    return False
