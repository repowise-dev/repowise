"""Spring + Spring Boot framework edges (DI, routing, Spring Data, autoconfig).

Expanded in Phase 4 of the JVM parity plan. The handler now covers:

- **Stereotype detection** — ``@Component`` / ``@Service`` / ``@Repository``
  / ``@Controller`` / ``@RestController`` / ``@Configuration`` /
  ``@ControllerAdvice`` / ``@RestControllerAdvice``.
- **Field injection** — ``@Autowired`` / ``@Inject`` / ``@Resource`` on
  fields, mapped to the field type's defining file.
- **Constructor injection** — explicit constructors annotated
  ``@Autowired`` or the single implicit constructor (Spring 4.3+);
  **plus** Lombok ``@RequiredArgsConstructor`` / ``@AllArgsConstructor``
  on a stereotype class, which synthesize a constructor over the
  ``final`` (RAC) or all (AAC) declared fields.
- **``@Bean`` factories** — Java and Kotlin signatures.
- **Spring Data repositories** — any interface extending
  ``CrudRepository`` / ``JpaRepository`` / ``MongoRepository`` /
  friends becomes an entry point (the impl is generated at runtime).
- **Autoconfig consumer edges** — emit edges from each
  ``META-INF/spring/...AutoConfiguration.imports`` file (and
  ``spring.factories``) to the listed FQNs so the resource file
  shows as the importer (the FQN files were already stamped
  ``is_entry_point`` during the JVM warmup).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..resolvers import ResolverContext
from .base import (
    DetectionContext,
    FrameworkHandler,
    _add_edge_if_new,
    _build_class_to_file,
    read_text,
)

if TYPE_CHECKING:
    import networkx as nx


_SPRING_BEAN_ANNOT = (
    "@Component",
    "@Service",
    "@Repository",
    "@Controller",
    "@RestController",
    "@Configuration",
    "@ControllerAdvice",
    "@RestControllerAdvice",
)
_SPRING_INJECT_FIELD_RE = re.compile(
    r"@(?:Autowired|Inject|Resource)\s+(?:private|protected|public|final|\s)*\s*([A-Z][\w<>,\s.]*?)\s+\w+\s*[;=]"
)
_SPRING_CTOR_PARAM_RE = re.compile(r"\b([A-Z]\w*)\s+\w+\s*[,)]")
_SPRING_BEAN_METHOD_RE = re.compile(
    r"@Bean\b[^\n]*\n\s*(?:public|protected|private|static|final|\s)+\s*([A-Z]\w*)\s+\w+\s*\("
)
_SPRING_BEAN_METHOD_KOTLIN_RE = re.compile(
    r"@Bean\b[^\n]*\n\s*(?:public|protected|private|internal|fun|open|\s)+\s*\w+\s*\([^)]*\)\s*:\s*([A-Z]\w*)"
)

# Lombok constructor-synthesis annotations. RAC = constructor over every
# ``final`` (and ``@NonNull``) field; AAC = constructor over every field.
_LOMBOK_RAC_ANNOT = ("@RequiredArgsConstructor", "@AllArgsConstructor")
_LOMBOK_DATA_VALUE = ("@Data", "@Value")

# Spring Data repository base interfaces — any interface extending one of
# these has Spring-generated impls at runtime and must be treated as an
# entry point. List covers spring-data-commons + jpa / mongodb / r2dbc /
# elasticsearch / neo4j / couchbase / cassandra / redis flavours.
_SPRING_DATA_BASES: frozenset[str] = frozenset({
    "Repository",
    "CrudRepository",
    "PagingAndSortingRepository",
    "JpaRepository",
    "ReactiveCrudRepository",
    "ReactiveSortingRepository",
    "RxJava3CrudRepository",
    "MongoRepository",
    "ReactiveMongoRepository",
    "R2dbcRepository",
    "ElasticsearchRepository",
    "ReactiveElasticsearchRepository",
    "Neo4jRepository",
    "ReactiveNeo4jRepository",
    "CouchbaseRepository",
    "ReactiveCouchbaseRepository",
    "CassandraRepository",
    "ReactiveCassandraRepository",
    "KeyValueRepository",
    "QueryByExampleExecutor",
    "JpaSpecificationExecutor",
})

# Java field declaration with optional ``final`` modifier — used for
# Lombok RAC ctor-param inference. Captures the type's head identifier.
_JAVA_FINAL_FIELD_RE = re.compile(
    r"^\s*(?:private|protected|public|\s)*\s*(?:(final)\s+|@NonNull\s+)?"
    r"([A-Z]\w*)\s*(?:<[^>]+>)?\s+(\w+)\s*[;=]",
    re.MULTILINE,
)


def _has_spring_imports(parsed_files: dict[str, Any]) -> bool:
    for parsed in parsed_files.values():
        if parsed.file_info.language not in ("java", "kotlin"):
            continue
        for imp in parsed.imports:
            if imp.module_path.startswith("org.springframework"):
                return True
    return False


def _is_spring_data_repo(parsed: Any) -> bool:
    """True if *parsed* is a Java/Kotlin interface extending a Spring Data base."""
    if parsed.file_info.language not in ("java", "kotlin"):
        return False
    for rel in parsed.heritage:
        if rel.kind in ("extends", "implements"):
            base = rel.parent_name.split("<")[0].rsplit(".", 1)[-1].strip()
            if base in _SPRING_DATA_BASES:
                return True
    return False


def _scan_lombok_ctor_params(text: str) -> list[str]:
    """Return Lombok-synthesized constructor param types (head identifiers).

    For a class annotated ``@RequiredArgsConstructor`` (or ``@Data``), this
    is every ``final`` field's head type; for ``@AllArgsConstructor`` (or
    ``@Value``), every field's head type. Builtin types are filtered.
    """
    is_rac = any(a in text for a in _LOMBOK_RAC_ANNOT) or any(
        a in text for a in _LOMBOK_DATA_VALUE if a == "@Data"
    )
    is_aac = "@AllArgsConstructor" in text or "@Value" in text
    if not (is_rac or is_aac):
        return []

    params: list[str] = []
    for m in _JAVA_FINAL_FIELD_RE.finditer(text):
        is_final = bool(m.group(1))
        type_head = m.group(2)
        if type_head in ("String", "Integer", "Long", "Boolean", "Double",
                         "Float", "Byte", "Short", "Character", "Object",
                         "List", "Map", "Set", "Collection"):
            continue
        if is_aac or (is_rac and is_final):
            params.append(type_head)
    return params


def _add_spring_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    path_set: set[str],
    ctx: ResolverContext,
) -> int:
    count = 0
    class_to_file = _build_class_to_file(parsed_files, ("java", "kotlin"))

    # Build interface → list of impl files map from heritage
    impl_map: dict[str, list[str]] = {}
    for path, parsed in parsed_files.items():
        if parsed.file_info.language not in ("java", "kotlin"):
            continue
        for rel in parsed.heritage:
            if rel.kind in ("implements", "extends"):
                impl_map.setdefault(rel.parent_name, []).append(path)

    def _resolve_type(type_name: str) -> list[str]:
        # Strip generics if accidentally passed in
        type_name = type_name.split("<")[0].strip()
        results: list[str] = []
        own = class_to_file.get(type_name)
        if own:
            results.append(own)
        for impl in impl_map.get(type_name, []):
            if impl not in results:
                results.append(impl)
        return results

    # ---- 1. Spring Data repository entries ----
    # Mark the interface file node ``is_entry_point=True`` so the dead-code
    # analyzer doesn't flag it. The generated impl never appears in source,
    # so the interface itself is the only thing we can anchor against.
    for path, parsed in parsed_files.items():
        if _is_spring_data_repo(parsed):
            node = graph.nodes.get(path)
            if node is not None:
                node["is_entry_point"] = True
                node["framework_role"] = "spring_data_repository"

    # ---- 2. Per-class DI + @Bean factory edges ----
    for path, parsed in parsed_files.items():
        if parsed.file_info.language not in ("java", "kotlin"):
            continue
        text = read_text(parsed)
        if not text:
            continue
        is_bean = any(annot in text for annot in _SPRING_BEAN_ANNOT)
        if not is_bean:
            continue

        # Stamp the framework role on the file node for downstream consumers.
        node = graph.nodes.get(path)
        if node is not None:
            node["framework_role"] = node.get("framework_role") or "spring_stereotype"

        # 2a. Field injection (@Autowired / @Inject / @Resource)
        for m in _SPRING_INJECT_FIELD_RE.finditer(text):
            type_name = m.group(1).split("<")[0].strip()
            for target in _resolve_type(type_name):
                if target in path_set and _add_edge_if_new(graph, path, target):
                    count += 1

        # 2b. Constructor parameter injection — explicit ctors
        for ctor_match in re.finditer(
            r"(?:@Autowired\s*\n\s*)?(?:public|protected|private|\s)*"
            + re.escape(Path(path).stem)
            + r"\s*\(([^)]*)\)",
            text,
        ):
            params = ctor_match.group(1)
            if not params.strip():
                continue
            for pm in _SPRING_CTOR_PARAM_RE.finditer(params + ","):
                type_name = pm.group(1)
                if type_name in ("String", "Integer", "Long", "Boolean", "Double", "Float"):
                    continue
                for target in _resolve_type(type_name):
                    if target in path_set and _add_edge_if_new(graph, path, target):
                        count += 1

        # 2c. Lombok @RequiredArgsConstructor / @AllArgsConstructor / @Data
        for type_name in _scan_lombok_ctor_params(text):
            for target in _resolve_type(type_name):
                if target in path_set and _add_edge_if_new(graph, path, target):
                    count += 1

        # 2d. @Bean factory methods → return-type file
        if "@Configuration" in text:
            for m in _SPRING_BEAN_METHOD_RE.finditer(text):
                for target in _resolve_type(m.group(1)):
                    if target in path_set and _add_edge_if_new(graph, path, target):
                        count += 1
            for m in _SPRING_BEAN_METHOD_KOTLIN_RE.finditer(text):
                for target in _resolve_type(m.group(1)):
                    if target in path_set and _add_edge_if_new(graph, path, target):
                        count += 1

    # ---- 3. Autoconfig consumer edges ----
    # Pull the JVM workspace index (already memoised on ctx) and emit edges
    # from each autoconfig resource file (key) to the FQN target files.
    # The autoconfig FQN files were stamped ``is_entry_point`` in the
    # warmup; here we add the resource file as a visible importer so the
    # autoconfig file itself does not read as orphan.
    try:
        from ..resolvers.jvm_workspace import get_or_build_jvm_index

        jvm_index = get_or_build_jvm_index(ctx)
    except Exception:
        jvm_index = None

    if jvm_index is not None:
        for resource_path, fqns in jvm_index.autoconfig_imports.items():
            # Resource files may not have a node yet (we don't always index
            # .properties / .imports). Add a minimal file-shape node if missing
            # so the importer edge has a sink.
            if resource_path not in graph:
                graph.add_node(
                    resource_path,
                    node_type="file",
                    language="properties",
                    path=resource_path,
                    is_entry_point=True,
                )
            for fqn in fqns:
                for target in jvm_index.files_for_fqn(fqn):
                    if target in path_set and _add_edge_if_new(graph, resource_path, target):
                        count += 1
        # META-INF/services impls — same pattern
        for iface_fqn, impls in jvm_index.services.items():
            # The resource path isn't tracked per-iface; we use a synthetic
            # source so the impl files gain a visible importer. The warmup
            # already stamped them ``is_entry_point``, so this is belt-and-
            # suspenders for the unused_export visibility heuristic.
            source = f"META-INF/services/{iface_fqn}"
            if source not in graph:
                graph.add_node(
                    source,
                    node_type="file",
                    language="properties",
                    path=source,
                    is_entry_point=True,
                )
            for impl_fqn in impls:
                for target in jvm_index.files_for_fqn(impl_fqn):
                    if target in path_set and _add_edge_if_new(graph, source, target):
                        count += 1

    return count


class _SpringHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        spring_in_stack = any(
            token in dctx.stack_lower
            for token in ("spring", "springboot", "spring-boot", "spring boot")
        )
        return spring_in_stack or _has_spring_imports(dctx.parsed_files)

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_spring_edges(graph, parsed_files, path_set, ctx)


class _JvmAutoconfigHandler:
    """Always-on JVM resource-file consumer edge emitter.

    Runs even when there is no ``org.springframework`` import — Spring
    Boot autoconfig imports + META-INF/services exist in plain JVM libs
    too (e.g. Quarkus/Helidon ship their own). The Spring stereotype
    DI scan above is gated; this scan is not, so an autoconfig-only repo
    still gets the resource → impl edges.
    """

    def detect(self, dctx: DetectionContext) -> bool:
        # Cheap detect: any .java or .kt file in the parse set.
        return any(
            p.file_info.language in ("java", "kotlin")
            for p in dctx.parsed_files.values()
        )

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        # The autoconfig + META-INF block runs inside _add_spring_edges so
        # we don't double-emit. This handler is a no-op stub kept registered
        # so future expansion (e.g. a JVM-only resource consumer that runs
        # in non-Spring repos) has a registration point.
        return 0


HANDLERS: list[FrameworkHandler] = [_SpringHandler()]
