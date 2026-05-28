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
``is_entry_point`` on the file node during the JVM warmup (see
:mod:`graph_warmups`), and the analyzer's existing entry-point skip
honours them before reaching this hook.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

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
    """Strip ``@``, generic args, and arg list from a decorator string."""
    s = decorator.lstrip("@")
    for sep in ("(", "<"):
        pos = s.find(sep)
        if pos >= 0:
            s = s[:pos]
    # Spring annotations may be FQN ('org.springframework.stereotype.Service');
    # we match the bare type name.
    if "." in s:
        s = s.rsplit(".", 1)[1]
    return s


def _file_defines_entry_class(graph: Any, file_node: str) -> bool:
    """True if *file_node* defines a class with a stereotype annotation
    or a ``main`` method.
    """
    for succ in graph.successors(file_node):
        succ_data = graph.nodes.get(succ, {})
        if succ_data.get("node_type") != "symbol":
            continue
        edge = graph.get_edge_data(file_node, succ, {})
        if edge.get("edge_type") != "defines":
            continue
        # Stereotype annotation on a class / record / object.
        decorators = succ_data.get("decorators") or []
        for dec in decorators:
            if _annotation_base(dec) in _STEREOTYPE_ANNOTATIONS:
                return True
        # ``main`` method — JAR / Kotlin file entry point.
        if (
            succ_data.get("kind") in ("method", "function")
            and succ_data.get("name") == "main"
        ):
            return True
    return False


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
    if graph.in_degree(node) > 0:
        return True

    if _file_defines_entry_class(graph, node):
        return True

    for sibling in package_files.get(_pkg_dir(node), ()):
        if sibling == node:
            continue
        sib_data = graph.nodes.get(sibling, {})
        if graph.in_degree(sibling) > 0:
            return True
        if sib_data.get("is_entry_point", False):
            return True
        if _file_defines_entry_class(graph, sibling):
            return True

    return False
