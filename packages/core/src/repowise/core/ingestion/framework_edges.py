"""Framework-aware synthetic edge detection.

Extracted from ``graph.py`` — detects Django, FastAPI, Flask, and pytest
convention-based relationships and adds ``edge_type="framework"`` edges.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .resolvers import ResolverContext, resolve_import

if TYPE_CHECKING:
    import networkx as nx

    from .models import ParsedFile


def add_framework_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, ParsedFile],
    ctx: ResolverContext,
    tech_stack: list[str] | None = None,
) -> int:
    """Add synthetic edges for framework-mediated relationships.

    Returns the number of edges added.
    """
    count = 0
    path_set = set(parsed_files.keys())

    # Always run: pytest conftest detection
    count += _add_conftest_edges(graph, path_set)

    stack_lower = {s.lower() for s in (tech_stack or [])}

    if "django" in stack_lower:
        count += _add_django_edges(graph, path_set)
    if "fastapi" in stack_lower or "starlette" in stack_lower:
        count += _add_fastapi_edges(graph, parsed_files, ctx, path_set)
    if "flask" in stack_lower:
        count += _add_flask_edges(graph, parsed_files, ctx, path_set)

    # ASP.NET framework edges run when the tech stack hints at .NET web,
    # OR when any .cs file imports Microsoft.AspNetCore.* (cheap fallback
    # so we don't depend on detect_tech_stack catching the project).
    aspnet_in_stack = any(
        token in stack_lower
        for token in ("aspnet", "asp.net", "aspnetcore", "asp.net core")
    )
    if aspnet_in_stack or _has_aspnet_imports(parsed_files):
        count += _add_aspnet_edges(graph, parsed_files, path_set)

    if "rails" in stack_lower or "config/application.rb" in path_set:
        count += _add_rails_edges(graph, parsed_files, ctx, path_set)

    if (
        "laravel" in stack_lower
        or "routes/web.php" in path_set
        or "routes/api.php" in path_set
    ):
        count += _add_laravel_edges(graph, parsed_files, ctx, path_set)

    spring_in_stack = any(
        token in stack_lower for token in ("spring", "springboot", "spring-boot", "spring boot")
    )
    if spring_in_stack or _has_spring_imports(parsed_files):
        count += _add_spring_edges(graph, parsed_files, path_set)

    express_in_stack = any(
        token in stack_lower for token in ("express", "nestjs", "nest", "nest.js")
    )
    if express_in_stack or _has_express_imports(parsed_files):
        count += _add_express_edges(graph, parsed_files, ctx, path_set)

    go_router_in_stack = any(token in stack_lower for token in ("gin", "echo", "chi"))
    if go_router_in_stack or _has_go_router_imports(parsed_files):
        count += _add_go_router_edges(graph, parsed_files, path_set)

    rust_router_in_stack = any(token in stack_lower for token in ("axum", "actix", "actix-web"))
    if rust_router_in_stack or _has_rust_router_imports(parsed_files):
        count += _add_rust_router_edges(graph, parsed_files, path_set)

    return count


def _add_edge_if_new(graph: nx.DiGraph, source: str, target: str) -> bool:
    """Add a framework edge if no edge already exists. Returns True if added."""
    if source == target:
        return False
    if graph.has_edge(source, target):
        return False
    graph.add_edge(source, target, edge_type="framework", imported_names=[])
    return True


def _add_conftest_edges(graph: nx.DiGraph, path_set: set[str]) -> int:
    """conftest.py -> test files in the same or child directories."""
    count = 0
    conftest_paths = [p for p in path_set if Path(p).name == "conftest.py"]

    for conf in conftest_paths:
        conf_dir = Path(conf).parent.as_posix()
        prefix = f"{conf_dir}/" if conf_dir != "." else ""
        for p in path_set:
            if p == conf:
                continue
            node = graph.nodes.get(p, {})
            if not node.get("is_test", False):
                continue
            if (p.startswith(prefix) or (prefix == "" and "/" not in p)) and _add_edge_if_new(
                graph, p, conf
            ):
                count += 1
    return count


def _add_django_edges(graph: nx.DiGraph, path_set: set[str]) -> int:
    """Django conventions: admin->models, urls->views in the same directory."""
    count = 0
    by_dir: dict[str, dict[str, str]] = {}
    for p in path_set:
        pp = Path(p)
        d = pp.parent.as_posix()
        by_dir.setdefault(d, {})[pp.stem] = p

    for _d, stems in by_dir.items():
        if (
            "admin" in stems
            and "models" in stems
            and _add_edge_if_new(graph, stems["admin"], stems["models"])
        ):
            count += 1
        if (
            "urls" in stems
            and "views" in stems
            and _add_edge_if_new(graph, stems["urls"], stems["views"])
        ):
            count += 1
        if (
            "forms" in stems
            and "models" in stems
            and _add_edge_if_new(graph, stems["forms"], stems["models"])
        ):
            count += 1
        if (
            "serializers" in stems
            and "models" in stems
            and _add_edge_if_new(graph, stems["serializers"], stems["models"])
        ):
            count += 1
    return count


def _add_fastapi_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    ctx: ResolverContext,
    path_set: set[str],
) -> int:
    """Detect include_router() calls and link app files to router modules."""
    count = 0
    var_to_file: dict[str, str] = {}

    for path, parsed in parsed_files.items():
        for imp in parsed.imports:
            for name in imp.imported_names:
                if name.lower().endswith("router") or name.lower().endswith("app"):
                    resolved = resolve_import(
                        imp.module_path,
                        path,
                        parsed.file_info.language,
                        ctx,
                    )
                    if resolved and resolved in path_set:
                        var_to_file[name] = resolved

    router_re = re.compile(r"(?:include_router|add_api_route)\s*\(\s*(\w+)")
    for path, parsed in parsed_files.items():
        if parsed.file_info.language != "python":
            continue
        try:
            source = Path(parsed.file_info.abs_path).read_text(errors="ignore")
        except Exception:
            continue
        for match in router_re.finditer(source):
            var_name = match.group(1)
            target = var_to_file.get(var_name)
            if target and target in path_set and _add_edge_if_new(graph, path, target):
                count += 1
    return count


def _add_flask_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    ctx: ResolverContext,
    path_set: set[str],
) -> int:
    """Detect register_blueprint() calls and link app files to blueprint modules."""
    count = 0
    var_to_file: dict[str, str] = {}

    for path, parsed in parsed_files.items():
        for imp in parsed.imports:
            for name in imp.imported_names:
                if "blueprint" in name.lower() or name.lower().endswith("bp"):
                    resolved = resolve_import(
                        imp.module_path,
                        path,
                        parsed.file_info.language,
                        ctx,
                    )
                    if resolved and resolved in path_set:
                        var_to_file[name] = resolved

    bp_re = re.compile(r"register_blueprint\s*\(\s*(\w+)")
    for path, parsed in parsed_files.items():
        if parsed.file_info.language != "python":
            continue
        try:
            source = Path(parsed.file_info.abs_path).read_text(errors="ignore")
        except Exception:
            continue
        for match in bp_re.finditer(source):
            var_name = match.group(1)
            target = var_to_file.get(var_name)
            if target and target in path_set and _add_edge_if_new(graph, path, target):
                count += 1
    return count


# ---------------------------------------------------------------------------
# ASP.NET / .NET framework edges
# ---------------------------------------------------------------------------

# A class becomes a controller when it is annotated [ApiController] OR its
# class name ends in "Controller" (the MVC discovery convention). The first
# is preferred — it produces zero false positives.
_ASPNET_CONTROLLER_ATTR_RE = re.compile(r"\[\s*ApiController\b")
_ASPNET_ROUTE_RE = re.compile(r"\[\s*(?:Http(?:Get|Post|Put|Delete|Patch|Options|Head)|Route)\b")
_ASPNET_MAP_CALL_RE = re.compile(
    r"\.\s*Map(?:Get|Post|Put|Delete|Patch|Controllers|Hub|GrpcService|Razor|Fallback)\s*[<(]"
)
_ASPNET_USE_MIDDLEWARE_RE = re.compile(r"\.\s*UseMiddleware\s*<\s*(\w+)")
_DBCONTEXT_DECL_RE = re.compile(r"class\s+\w+\s*:\s*[\w.<>,\s]*\bDbContext\b")
_DBSET_RE = re.compile(r"\bDbSet\s*<\s*([A-Z]\w*)\s*>")


def _has_aspnet_imports(parsed_files: dict[str, Any]) -> bool:
    """True if any parsed file imports Microsoft.AspNetCore.* — cheap signal."""
    for parsed in parsed_files.values():
        if parsed.file_info.language != "csharp":
            continue
        for imp in parsed.imports:
            if imp.module_path.startswith("Microsoft.AspNetCore"):
                return True
    return False


def _read_cs_text(parsed: Any) -> str:
    try:
        return Path(parsed.file_info.abs_path).read_text(encoding="utf-8-sig", errors="ignore")
    except OSError:
        return ""


def _add_aspnet_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    path_set: set[str],
) -> int:
    """Add edges representing ASP.NET wiring.

    Three independent signal sources are merged:

    1. Application entry point (``Program.cs``/``Startup.cs``) → every
       controller file. ``MapControllers()`` is the discovery anchor;
       individual controllers are not statically referenced from
       ``Program.cs`` but the framework wires them at runtime.

    2. Application entry point → handler files for any ``app.MapGet(...)``
       / ``MapPost(...)`` minimal API call. We can't resolve the handler
       expression statically, so we approximate by linking to every file
       whose class is named in the handler argument (heuristic only).

    3. ``DbContext`` subclasses → files declaring the entity types named
       in their ``DbSet<T>`` properties. This surfaces EF Core's implicit
       persistence model wiring that no static import edge captures.

    Returns the number of edges added.
    """
    count = 0
    cs_files = [
        (path, parsed)
        for path, parsed in parsed_files.items()
        if parsed.file_info.language == "csharp" and path in path_set
    ]
    if not cs_files:
        return 0

    # ---- 1. Discover controllers and entry points ----
    controllers: list[str] = []
    entry_points: list[str] = []
    dbcontext_files: list[tuple[str, str]] = []  # (path, source)
    type_decl_to_file: dict[str, str] = {}  # ClassName -> defining file path

    for path, parsed in cs_files:
        # Index every named class/struct/record this file declares so the
        # DbSet<T> step can resolve targets without re-parsing.
        for sym in parsed.symbols:
            if sym.kind in ("class", "struct", "record"):
                # Last-write wins is fine — duplicates are rare in well-formed code.
                type_decl_to_file[sym.name] = path

        text = _read_cs_text(parsed)
        if not text:
            continue
        if _ASPNET_CONTROLLER_ATTR_RE.search(text) or path.endswith("Controller.cs"):
            controllers.append(path)
        name = Path(path).name
        if name in ("Program.cs", "Startup.cs"):
            entry_points.append(path)
        if _DBCONTEXT_DECL_RE.search(text):
            dbcontext_files.append((path, text))

    # ---- 2. Entry point → controllers (MapControllers / UseEndpoints) ----
    for entry in entry_points:
        for ctrl in controllers:
            if ctrl == entry:
                continue
            if _add_edge_if_new(graph, entry, ctrl):
                count += 1

    # ---- 3. Entry point → file containing handler class referenced in MapXxx ----
    handler_arg_re = re.compile(
        r"\.\s*Map(?:Get|Post|Put|Delete|Patch)\s*\(\s*[\"'][^\"']+[\"']\s*,\s*([A-Za-z_]\w*)"
    )
    for entry in entry_points:
        text = _read_cs_text(parsed_files[entry])
        if not text:
            continue
        for match in handler_arg_re.finditer(text):
            ident = match.group(1)
            target = type_decl_to_file.get(ident)
            if target and target in path_set and _add_edge_if_new(graph, entry, target):
                count += 1

    # ---- 4. UseMiddleware<T>() ----
    middleware_re = _ASPNET_USE_MIDDLEWARE_RE
    for entry in entry_points:
        text = _read_cs_text(parsed_files[entry])
        if not text:
            continue
        for match in middleware_re.finditer(text):
            target = type_decl_to_file.get(match.group(1))
            if target and target in path_set and _add_edge_if_new(graph, entry, target):
                count += 1

    # ---- 5. DbContext → DbSet<T> entity files ----
    for db_path, db_text in dbcontext_files:
        for match in _DBSET_RE.finditer(db_text):
            entity = match.group(1)
            target = type_decl_to_file.get(entity)
            if target and target in path_set and _add_edge_if_new(graph, db_path, target):
                count += 1

    return count


# ---------------------------------------------------------------------------
# Helpers shared across the F1–F6 slices
# ---------------------------------------------------------------------------


def _read_text(parsed: Any) -> str:
    try:
        return Path(parsed.file_info.abs_path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _build_class_to_file(parsed_files: dict[str, Any], languages: tuple[str, ...]) -> dict[str, str]:
    """Map declared class/interface/struct/enum/record names → file path."""
    result: dict[str, str] = {}
    for path, parsed in parsed_files.items():
        if parsed.file_info.language not in languages:
            continue
        for sym in parsed.symbols:
            if sym.kind in ("class", "interface", "struct", "record", "enum", "trait"):
                result.setdefault(sym.name, path)
    return result


def _build_function_to_file(
    parsed_files: dict[str, Any], languages: tuple[str, ...]
) -> dict[str, list[str]]:
    """Map declared function/method names → list of file paths declaring them."""
    result: dict[str, list[str]] = {}
    for path, parsed in parsed_files.items():
        if parsed.file_info.language not in languages:
            continue
        for sym in parsed.symbols:
            if sym.kind in ("function", "method"):
                result.setdefault(sym.name, []).append(path)
    return result


# ---------------------------------------------------------------------------
# F2 — Rails framework edges
# ---------------------------------------------------------------------------


_RAILS_RESOURCES_RE = re.compile(r"\bresources?\s+:(\w+)")
_RAILS_GET_TO_RE = re.compile(
    r"\b(?:get|post|put|patch|delete|match)\s+[^\n]+?(?:to:\s*|=>\s*)['\"]([\w/]+)#\w+['\"]"
)
_RAILS_NAMESPACE_RE = re.compile(r"\bnamespace\s+:(\w+)\b")
_RAILS_AR_RELATION_RE = re.compile(r"\b(?:belongs_to|has_many|has_one|has_and_belongs_to_many)\s+:(\w+)")


def _singularize(word: str) -> str:
    """Very rough Rails inflector — sufficient for routes/AR lookups."""
    if word.endswith("ies") and len(word) > 3:
        return word[:-3] + "y"
    if word.endswith("ses") or word.endswith("xes") or word.endswith("zes"):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _camelize(word: str) -> str:
    return "".join(part.capitalize() for part in word.split("_"))


def _add_rails_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    ctx: ResolverContext,
    path_set: set[str],
) -> int:
    count = 0

    # ---- routes.rb → controller files ----
    routes_path = "config/routes.rb"
    if routes_path in path_set:
        try:
            text = Path(parsed_files[routes_path].file_info.abs_path).read_text(
                encoding="utf-8", errors="ignore"
            )
        except (OSError, KeyError):
            text = ""
        if text:
            # Parse line-by-line to track namespace nesting (indent-agnostic; we
            # use the order of opening keywords vs `end`).
            namespace_stack: list[str] = []
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                ns_match = _RAILS_NAMESPACE_RE.search(line)
                if ns_match and ("do" in line or line.endswith("do")):
                    namespace_stack.append(ns_match.group(1))
                    continue
                if line == "end" and namespace_stack:
                    namespace_stack.pop()
                    continue
                # resources :users → users_controller
                for m in _RAILS_RESOURCES_RE.finditer(line):
                    resource = m.group(1)
                    target = _resolve_rails_controller(ctx, namespace_stack, resource, path_set)
                    if target and _add_edge_if_new(graph, routes_path, target):
                        count += 1
                # get "/foo", to: "users#index"
                for m in _RAILS_GET_TO_RE.finditer(line):
                    ctrl_path = m.group(1)
                    target = _resolve_rails_controller_path(ctx, namespace_stack, ctrl_path, path_set)
                    if target and _add_edge_if_new(graph, routes_path, target):
                        count += 1

    # ---- ActiveRecord relationships: model → model ----
    for path, parsed in parsed_files.items():
        if parsed.file_info.language != "ruby":
            continue
        if "/models/" not in path and not path.startswith("app/models/"):
            continue
        text = _read_text(parsed)
        if not text:
            continue
        for m in _RAILS_AR_RELATION_RE.finditer(text):
            assoc_name = m.group(1)
            target = _resolve_rails_relation(ctx, assoc_name, path_set)
            if target and _add_edge_if_new(graph, path, target):
                count += 1

    return count


def _resolve_rails_controller(
    ctx: ResolverContext, namespace_stack: list[str], resource: str, path_set: set[str]
) -> str | None:
    """`resources :users` (with optional `namespace :admin do`) → controller path."""
    namespace_segs = [seg for seg in namespace_stack]
    candidate_path = "/".join([*namespace_segs, f"{resource}_controller"])
    expected = f"app/controllers/{candidate_path}.rb"
    if expected in path_set:
        return expected
    # Try via Rails autoload index (heritage)
    constant = "::".join(_camelize(seg) for seg in [*namespace_segs, f"{resource}_controller"])
    return ctx.rails_lookup(constant)


def _resolve_rails_controller_path(
    ctx: ResolverContext, namespace_stack: list[str], controller_token: str, path_set: set[str]
) -> str | None:
    """`to: "users#index"` or `to: "admin/users#index"` → controller path."""
    expected = f"app/controllers/{controller_token}_controller.rb"
    if expected in path_set:
        return expected
    parts = controller_token.split("/")
    constant = "::".join(_camelize(p) for p in [*parts[:-1], f"{parts[-1]}_controller"])
    return ctx.rails_lookup(constant)


def _resolve_rails_relation(
    ctx: ResolverContext, assoc_name: str, path_set: set[str]
) -> str | None:
    """`belongs_to :user` / `has_many :orders` → model file."""
    singular = _singularize(assoc_name)
    expected = f"app/models/{singular}.rb"
    if expected in path_set:
        return expected
    return ctx.rails_lookup(_camelize(singular))


# ---------------------------------------------------------------------------
# F3 — Laravel framework edges
# ---------------------------------------------------------------------------


_LARAVEL_ROUTE_ARRAY_RE = re.compile(
    r"Route::(?:get|post|put|patch|delete|any|match|resource|apiResource)\s*\([^,]*,\s*\[\s*([\w\\]+)::class"
)
_LARAVEL_ROUTE_LEGACY_RE = re.compile(
    r"Route::(?:get|post|put|patch|delete|any|match)\s*\([^,]*,\s*['\"]([\w\\]+)@\w+['\"]"
)
_LARAVEL_ROUTE_RESOURCE_RE = re.compile(
    r"Route::(?:resource|apiResource)\s*\(\s*['\"][^'\"]+['\"]\s*,\s*([\w\\]+)::class"
)
_LARAVEL_BIND_RE = re.compile(
    r"->\s*(?:bind|singleton|instance)\s*\(\s*([\w\\]+)::class\s*,\s*([\w\\]+)::class"
)
_LARAVEL_ELOQUENT_RE = re.compile(
    r"\$this->\s*(?:hasMany|hasOne|belongsTo|belongsToMany|morphMany|morphOne|morphTo)\s*\(\s*([\w\\]+)::class"
)


def _resolve_laravel_class(
    ctx: ResolverContext, fqn: str, class_to_file: dict[str, str], path_set: set[str]
) -> str | None:
    """Resolve `Foo\\Bar\\Baz` (or short `Bar`) to repo-relative .php path."""
    from .resolvers.php_composer import resolve_via_psr4

    if "\\" in fqn:
        result = resolve_via_psr4(fqn, ctx)
        if result and result in path_set:
            return result
    short = fqn.rsplit("\\", 1)[-1]
    return class_to_file.get(short)


def _add_laravel_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    ctx: ResolverContext,
    path_set: set[str],
) -> int:
    count = 0
    class_to_file = _build_class_to_file(parsed_files, ("php",))

    # ---- routes/web.php / routes/api.php → controllers ----
    for routes_path in ("routes/web.php", "routes/api.php"):
        if routes_path not in path_set:
            continue
        try:
            text = Path(parsed_files[routes_path].file_info.abs_path).read_text(
                encoding="utf-8", errors="ignore"
            )
        except (OSError, KeyError):
            continue
        seen_targets: set[str] = set()
        for regex in (
            _LARAVEL_ROUTE_ARRAY_RE,
            _LARAVEL_ROUTE_LEGACY_RE,
            _LARAVEL_ROUTE_RESOURCE_RE,
        ):
            for m in regex.finditer(text):
                target = _resolve_laravel_class(ctx, m.group(1), class_to_file, path_set)
                if target and target in path_set and target not in seen_targets:
                    seen_targets.add(target)
                    if _add_edge_if_new(graph, routes_path, target):
                        count += 1

    # ---- Service providers → bound classes ----
    for path, parsed in parsed_files.items():
        if parsed.file_info.language != "php":
            continue
        if not path.endswith("ServiceProvider.php"):
            continue
        text = _read_text(parsed)
        if not text:
            continue
        for m in _LARAVEL_BIND_RE.finditer(text):
            for fqn in (m.group(1), m.group(2)):
                target = _resolve_laravel_class(ctx, fqn, class_to_file, path_set)
                if target and target in path_set and _add_edge_if_new(graph, path, target):
                    count += 1

    # ---- Eloquent relationships: model → related model ----
    for path, parsed in parsed_files.items():
        if parsed.file_info.language != "php":
            continue
        text = _read_text(parsed)
        if not text:
            continue
        for m in _LARAVEL_ELOQUENT_RE.finditer(text):
            target = _resolve_laravel_class(ctx, m.group(1), class_to_file, path_set)
            if target and target in path_set and _add_edge_if_new(graph, path, target):
                count += 1

    return count


# ---------------------------------------------------------------------------
# F1 — Spring Boot framework edges
# ---------------------------------------------------------------------------


_SPRING_BEAN_ANNOT = ("@Component", "@Service", "@Repository", "@Controller", "@RestController", "@Configuration")
_SPRING_AUTOWIRED_FIELD_RE = re.compile(
    r"@Autowired\s+(?:private|protected|public|final|\s)*\s*([A-Z]\w*)\s+\w+"
)
_SPRING_CTOR_PARAM_RE = re.compile(r"\b([A-Z]\w*)\s+\w+\s*[,)]")
_SPRING_BEAN_METHOD_RE = re.compile(
    r"@Bean\b[^\n]*\n\s*(?:public|protected|private|static|final|\s)+\s*([A-Z]\w*)\s+\w+\s*\("
)
_SPRING_BEAN_METHOD_KOTLIN_RE = re.compile(
    r"@Bean\b[^\n]*\n\s*(?:public|protected|private|internal|fun|open|\s)+\s*\w+\s*\([^)]*\)\s*:\s*([A-Z]\w*)"
)


def _has_spring_imports(parsed_files: dict[str, Any]) -> bool:
    for parsed in parsed_files.values():
        if parsed.file_info.language not in ("java", "kotlin"):
            continue
        for imp in parsed.imports:
            if imp.module_path.startswith("org.springframework"):
                return True
    return False


def _add_spring_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    path_set: set[str],
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
        results: list[str] = []
        own = class_to_file.get(type_name)
        if own:
            results.append(own)
        for impl in impl_map.get(type_name, []):
            if impl not in results:
                results.append(impl)
        return results

    for path, parsed in parsed_files.items():
        if parsed.file_info.language not in ("java", "kotlin"):
            continue
        text = _read_text(parsed)
        if not text:
            continue
        is_bean = any(annot in text for annot in _SPRING_BEAN_ANNOT)
        if not is_bean:
            continue

        # @Autowired field injection
        for m in _SPRING_AUTOWIRED_FIELD_RE.finditer(text):
            type_name = m.group(1)
            for target in _resolve_type(type_name):
                if target in path_set and _add_edge_if_new(graph, path, target):
                    count += 1

        # Constructor parameter injection: collect param types from any @Autowired
        # constructor or any single public constructor in a bean (Spring 4.3+ omits
        # the annotation when the class has only one constructor).
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

        # @Bean factory methods → return-type file
        if "@Configuration" in text:
            for m in _SPRING_BEAN_METHOD_RE.finditer(text):
                for target in _resolve_type(m.group(1)):
                    if target in path_set and _add_edge_if_new(graph, path, target):
                        count += 1
            for m in _SPRING_BEAN_METHOD_KOTLIN_RE.finditer(text):
                for target in _resolve_type(m.group(1)):
                    if target in path_set and _add_edge_if_new(graph, path, target):
                        count += 1

    return count


# ---------------------------------------------------------------------------
# F4 — Express / NestJS framework edges
# ---------------------------------------------------------------------------


_EXPRESS_USE_RE = re.compile(r"\.\s*use\s*\(\s*(?:['\"][^'\"]+['\"]\s*,\s*)?(\w+)\s*[,)]")
_NEST_MODULE_RE = re.compile(r"@Module\s*\(\s*\{([^}]*)\}\s*\)", re.DOTALL)
_NEST_ARRAY_FIELD_RE = re.compile(
    r"\b(?:controllers|providers|imports|exports)\s*:\s*\[([^\]]*)\]"
)
_IDENT_RE = re.compile(r"\b([A-Z]\w*)\b")


def _has_express_imports(parsed_files: dict[str, Any]) -> bool:
    for parsed in parsed_files.values():
        if parsed.file_info.language not in ("typescript", "javascript"):
            continue
        for imp in parsed.imports:
            mp = imp.module_path
            if mp == "express" or mp.startswith("@nestjs/"):
                return True
    return False


def _add_express_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    ctx: ResolverContext,
    path_set: set[str],
) -> int:
    count = 0
    class_to_file = _build_class_to_file(parsed_files, ("typescript", "javascript"))

    # ---- Express: app.use(routerVar) ----
    for path, parsed in parsed_files.items():
        if parsed.file_info.language not in ("typescript", "javascript"):
            continue
        text = _read_text(parsed)
        if not text:
            continue

        # Build local var → source file map for this file
        var_to_file: dict[str, str] = {}
        for imp in parsed.imports:
            for name in imp.imported_names:
                resolved = resolve_import(
                    imp.module_path,
                    path,
                    parsed.file_info.language,
                    ctx,
                )
                if resolved and resolved in path_set:
                    var_to_file[name] = resolved

        if "express" in text or any(
            imp.module_path == "express" for imp in parsed.imports
        ):
            for m in _EXPRESS_USE_RE.finditer(text):
                var_name = m.group(1)
                target = var_to_file.get(var_name)
                if target and target in path_set and _add_edge_if_new(graph, path, target):
                    count += 1

        # ---- NestJS: @Module({ controllers: [...], providers: [...], imports: [...] }) ----
        for mod_match in _NEST_MODULE_RE.finditer(text):
            body = mod_match.group(1)
            for arr_match in _NEST_ARRAY_FIELD_RE.finditer(body):
                for ident_match in _IDENT_RE.finditer(arr_match.group(1)):
                    cls = ident_match.group(1)
                    target = var_to_file.get(cls) or class_to_file.get(cls)
                    if target and target in path_set and _add_edge_if_new(graph, path, target):
                        count += 1

    return count


# ---------------------------------------------------------------------------
# F5 — Gin / Echo / Chi framework edges (Go)
# ---------------------------------------------------------------------------


_GO_ROUTER_PKG_PATTERNS = (
    "github.com/gin-gonic/gin",
    "github.com/labstack/echo",
    "github.com/go-chi/chi",
)
_GO_ROUTE_CALL_RE = re.compile(
    r"\.\s*(?:GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD|HandleFunc|Handle|Any)"
    r"\s*\(\s*[\"'][^\"']*[\"']\s*,\s*([\w.]+)"
)


def _has_go_router_imports(parsed_files: dict[str, Any]) -> bool:
    for parsed in parsed_files.values():
        if parsed.file_info.language != "go":
            continue
        for imp in parsed.imports:
            mp = imp.module_path
            if any(mp.startswith(pkg) for pkg in _GO_ROUTER_PKG_PATTERNS):
                return True
    return False


def _add_go_router_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    path_set: set[str],
) -> int:
    count = 0
    func_to_files = _build_function_to_file(parsed_files, ("go",))
    class_to_file = _build_class_to_file(parsed_files, ("go",))

    for path, parsed in parsed_files.items():
        if parsed.file_info.language != "go":
            continue
        text = _read_text(parsed)
        if not text:
            continue
        if not any(
            imp.module_path.startswith(pkg)
            for pkg in _GO_ROUTER_PKG_PATTERNS
            for imp in parsed.imports
        ):
            # Allow if some other go file in the repo imports a router (router
            # setup may be split across files); be conservative and only emit
            # edges for files that themselves reference router calls.
            pass

        for m in _GO_ROUTE_CALL_RE.finditer(text):
            handler = m.group(1)
            targets = _resolve_go_handler(handler, parsed, func_to_files, class_to_file)
            for target in targets:
                if target != path and target in path_set and _add_edge_if_new(graph, path, target):
                    count += 1

    return count


def _resolve_go_handler(
    handler: str,
    parsed: Any,
    func_to_files: dict[str, list[str]],
    class_to_file: dict[str, str],
) -> list[str]:
    """Resolve `pkg.Func` / `recv.Method` / `Func` to candidate file paths."""
    if "." in handler:
        prefix, name = handler.rsplit(".", 1)
        # First: was prefix imported as a package?
        for imp in parsed.imports:
            short = imp.module_path.rsplit("/", 1)[-1]
            if short == prefix and imp.resolved_file:
                # Find file declaring `name` whose path starts with the resolved package dir
                pkg_dir = "/".join(imp.resolved_file.split("/")[:-1])
                results = [
                    p
                    for p in func_to_files.get(name, [])
                    if p.startswith(pkg_dir + "/") or p == imp.resolved_file
                ]
                if results:
                    return results
        # Second: receiver-method — try the receiver's type file
        type_file = class_to_file.get(prefix.title())
        if type_file:
            return [type_file]
        # Third: fall back to any file declaring the bare name
        return list(func_to_files.get(name, []))
    return list(func_to_files.get(handler, []))


# ---------------------------------------------------------------------------
# F6 — Axum / Actix framework edges (Rust)
# ---------------------------------------------------------------------------


_RUST_AXUM_ROUTE_RE = re.compile(
    r"\.\s*route\s*\(\s*[\"'][^\"']*[\"']\s*,\s*"
    r"(?:get|post|put|delete|patch|head|options|on)\s*\(\s*([\w:]+)\s*\)"
)
_RUST_ACTIX_TO_RE = re.compile(r"web::\s*(?:get|post|put|delete|patch|head)\(\)\s*\.\s*to\s*\(\s*([\w:]+)\s*\)")
_RUST_ACTIX_SERVICE_RE = re.compile(r"\.\s*service\s*\(\s*([\w:]+)\s*\)")
_RUST_SCOPE_CONFIGURE_RE = re.compile(r"\.\s*configure\s*\(\s*([\w:]+)\s*\)")


def _has_rust_router_imports(parsed_files: dict[str, Any]) -> bool:
    for parsed in parsed_files.values():
        if parsed.file_info.language != "rust":
            continue
        for imp in parsed.imports:
            mp = imp.module_path
            if mp.startswith("axum") or mp.startswith("actix_web") or mp.startswith("actix-web"):
                return True
    return False


def _add_rust_router_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    path_set: set[str],
) -> int:
    count = 0
    func_to_files = _build_function_to_file(parsed_files, ("rust",))

    def _resolve(handler: str) -> list[str]:
        name = handler.rsplit("::", 1)[-1]
        return list(func_to_files.get(name, []))

    for path, parsed in parsed_files.items():
        if parsed.file_info.language != "rust":
            continue
        text = _read_text(parsed)
        if not text:
            continue
        for regex in (
            _RUST_AXUM_ROUTE_RE,
            _RUST_ACTIX_TO_RE,
            _RUST_ACTIX_SERVICE_RE,
            _RUST_SCOPE_CONFIGURE_RE,
        ):
            for m in regex.finditer(text):
                for target in _resolve(m.group(1)):
                    if target != path and target in path_set and _add_edge_if_new(graph, path, target):
                        count += 1

    return count
