"""AWS Lambda handlers named by deployment config.

The Lambda runtime loads a handler from a string in ``serverless.yml``, a SAM
``template.yaml`` (``handler: src/users.create``, ``Handler: app.main.lambda_handler``)
or Terraform (:mod:`.aws_lambda_terraform`) and no source file imports it, so the dead-code pass reported the one export
the deployment depends on.

Each handler that resolves to a top-level symbol gets a ``framework`` edge from
the config file to the handler's file that names the export in
``imported_names``, so the file is reachable while its other exports are still
judged, and a ``framework_binds`` edge from the config's module symbol to the
handler. A handler that does not resolve adds nothing.

Ceiling: only files named ``serverless.yml`` / ``template.yaml`` (and ``.yaml``
/ ``.yml`` spellings) and ``.tf`` files are read, and only the ``file.export``
handler shape (Node.js, Python, Ruby). CDK constructs and Java or .NET handlers
are not resolved.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Any

import yaml

from ..resolvers import ResolverContext
from .aws_lambda_terraform import SOURCE_SUFFIXES, terraform_handlers, wanted
from .base import (
    DetectionContext,
    FrameworkHandler,
    _add_edge_if_new,
    add_symbol_edge,
    source_text,
)

if TYPE_CHECKING:
    import networkx as nx


_SERVERLESS_FILES = frozenset({"serverless.yml", "serverless.yaml"})
_SAM_FILES = frozenset({"template.yml", "template.yaml"})
_CONFIG_FILES = _SERVERLESS_FILES | _SAM_FILES
_FUNCTION_TYPES = frozenset({"AWS::Serverless::Function", "AWS::Lambda::Function"})

# ``<module path>.<export>``: the module may be a path (``src/users``) or a
# dotted Python module (``app.main``); the export is one identifier.
_HANDLER_RE = re.compile(r"^(?P<module>[\w$./-]+)\.(?P<export>[A-Za-z_$][\w$]*)$")


class _TagTolerantLoader(yaml.SafeLoader):
    """SafeLoader that reads CloudFormation tags (``!Ref``, ``!Sub``...) as null."""


_TagTolerantLoader.add_multi_constructor("!", lambda _loader, _suffix, _node: None)


def _load(text: str) -> dict[str, Any]:
    try:
        data = yaml.load(text, Loader=_TagTolerantLoader)
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _serverless_handlers(data: dict[str, Any]) -> Iterator[tuple[tuple[str, ...], str]]:
    """``(code dirs, handler)`` for each function, the dirs relative to the config."""
    for spec in _mapping(data.get("functions")).values():
        yield ("",), _string(_mapping(spec).get("handler"))


def _sam_handlers(data: dict[str, Any]) -> Iterator[tuple[tuple[str, ...], str]]:
    """``(code dirs, handler)`` for each function resource, with ``Globals`` defaults."""
    defaults = _mapping(_mapping(data.get("Globals")).get("Function"))
    for resource in _mapping(data.get("Resources")).values():
        resource = _mapping(resource)
        if resource.get("Type") not in _FUNCTION_TYPES:
            continue
        props = _mapping(resource.get("Properties"))
        code = props.get("CodeUri", props.get("Code", defaults.get("CodeUri", "")))
        # A mapping (an S3 location) or an intrinsic names no repo path.
        if isinstance(code, str):
            yield (code,), _string(props.get("Handler", defaults.get("Handler")))


def _handler_file(
    base: str, code_dirs: tuple[str, ...], module: str, path_set: set[str]
) -> str | None:
    """The source file a handler's module names under the first code dir that has it."""
    spellings = [module]
    if "/" not in module and "." in module:
        spellings.append(module.replace(".", "/"))  # Python dotted module
    for code_dir in code_dirs:
        for spelling in spellings:
            stem = posixpath.normpath(posixpath.join(base, code_dir, spelling))
            if stem.startswith(".."):
                continue
            # A Python package imports as its ``__init__.py``.
            for suffix in (*SOURCE_SUFFIXES, "/__init__.py"):
                if stem + suffix in path_set:
                    return stem + suffix
    return None


def _link(
    graph: nx.DiGraph,
    config: str,
    handlers: Iterable[tuple[tuple[str, ...], str]],
    parsed_files: dict[str, Any],
    path_set: set[str],
    source_map: dict[str, bytes],
) -> int:
    """Edges from *config* to each handler it names that resolves; the count added."""
    count = 0
    base = config.rpartition("/")[0]
    exports: dict[str, set[str]] = {}
    for code_dirs, handler in handlers:
        match = _HANDLER_RE.match(handler)
        if match is None:
            continue
        target = _handler_file(base, code_dirs, match["module"], path_set)
        if target is None:
            continue
        export = match["export"]
        parsed = parsed_files[target]
        symbol = next(
            (sym.id for sym in parsed.symbols if sym.name == export and not sym.parent_name),
            None,
        )
        if symbol is not None:
            count += add_symbol_edge(graph, f"{config}::__module__", symbol)
        # ``exports.create = ...`` declares no symbol, so the name in the
        # text is the evidence that the handler exists.
        elif not re.search(
            rf"(?<![\w$]){re.escape(export)}(?![\w$])",
            source_text(target, parsed, source_map),
        ):
            continue
        exports.setdefault(target, set()).add(export)
    for target, names in exports.items():
        count += _add_edge_if_new(graph, config, target, imported_names=sorted(names))
    return count


def _add_lambda_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    ctx: ResolverContext,
    path_set: set[str],
) -> int:
    count = 0
    source_map = getattr(ctx, "source_map", None) or {}
    tf_texts: dict[str, dict[str, str]] = {}
    for config in sorted(path_set):
        tf_dir, _, name = config.rpartition("/")
        if name.endswith(".tf"):
            text = source_text(config, parsed_files[config], source_map)
            if wanted(text):
                tf_texts.setdefault(tf_dir, {})[config] = text
            continue
        if name in _SERVERLESS_FILES:
            read_handlers = _serverless_handlers
        elif name in _SAM_FILES:
            read_handlers = _sam_handlers
        else:
            continue
        data = _load(source_text(config, parsed_files[config], source_map))
        count += _link(graph, config, read_handlers(data), parsed_files, path_set, source_map)
    for tf_dir, texts in tf_texts.items():
        by_file: dict[str, list[tuple[tuple[str, ...], str]]] = {}
        for tf_file, code_dirs, handler in terraform_handlers(tf_dir, texts):
            by_file.setdefault(tf_file, []).append((code_dirs, handler))
        for tf_file, handlers in by_file.items():
            count += _link(graph, tf_file, handlers, parsed_files, path_set, source_map)
    return count


class _AwsLambdaHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        return any(
            p.endswith(".tf") or p.rpartition("/")[2] in _CONFIG_FILES for p in dctx.path_set
        )

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_lambda_edges(graph, parsed_files, ctx, path_set)


HANDLERS: list[FrameworkHandler] = [_AwsLambdaHandler()]
