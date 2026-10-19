"""Where a JS/TS test runner collects tests from, read from Vitest and Jest config files.

A JavaScript or TypeScript file named like a custom test (``core/stream/charStream.vitest.ts``,
``evals/prompt.eval.ts``) is a test when a governing Vitest or Jest configuration collects it:
Vitest specifies ``test.include``, and Jest specifies ``testMatch`` and ``testRegex``.
:mod:`.test_paths` asks :meth:`JsTestRoots.collects` to classify such files as tests.

Pure: callers hand in config text (or the already-parsed ``package.json`` dictionary),
so ingestion reads each config once during the walk it already makes and passes the
resulting roots into classification.

Each config governs its own directory and any files beneath it, nearest first.
A file that any config above it collects counts as collected.

Promotion semantics:
Unlike pytest, JVM, and PHPUnit runner configs that demote test-shaped names outside
test trees back to production code, JS/TS test runner configs **promote** files. Any
JS/TS source file collected by a governing Vitest or Jest configuration becomes a test
even without a conventional test-shaped filename or test directory.

Promoting a file flips its downstream ingestion and analysis status:
- Health scope: the file is treated as test material rather than production code.
- Dead code: exempt from production dead-code detection.
- Entry-point stamping: prevented from being stamped as a production entry point
  (``entry and not is_test`` in :class:`~repowise.core.ingestion.traverser.FileTraverser`).

Existing ``.test.``/``.spec.`` files remain tests even if a config omits them. Unreadable
or dynamic configs (containing spreads, variables, imports, or function calls) return
``None``, falling back safely to standard filename and directory heuristics.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tree_sitter import Node

VITEST_CONFIG_NAMES: tuple[str, ...] = (
    "vitest.config.ts",
    "vitest.config.js",
    "vitest.config.mts",
    "vitest.config.mjs",
    "vitest.config.cts",
    "vitest.config.cjs",
    "vite.config.ts",
    "vite.config.js",
    "vite.config.mts",
    "vite.config.mjs",
    "vite.config.cts",
    "vite.config.cjs",
    "vitest.workspace.ts",
    "vitest.workspace.js",
    "vitest.workspace.mts",
    "vitest.workspace.mjs",
    "vitest.workspace.cts",
    "vitest.workspace.cjs",
    "vitest.workspace.json",
)

JEST_CONFIG_NAMES: tuple[str, ...] = (
    "jest.config.ts",
    "jest.config.js",
    "jest.config.mts",
    "jest.config.mjs",
    "jest.config.cts",
    "jest.config.cjs",
    "jest.config.json",
    "package.json",
)

JS_TEST_CONFIG_NAMES: frozenset[str] = frozenset(VITEST_CONFIG_NAMES + JEST_CONFIG_NAMES)

_JS_CONFIG_PRECEDENCE: dict[str, int] = {
    name: rank for rank, name in enumerate(VITEST_CONFIG_NAMES + JEST_CONFIG_NAMES)
}


@dataclass(frozen=True, slots=True)
class _JsCollection:
    patterns: tuple[str, ...] = ()
    regexes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class JsTestRoots:
    """Test collection patterns for each directory holding a Vitest or Jest config."""

    by_dir: Mapping[str, _JsCollection] = field(default_factory=dict)

    def collects(self, path: str) -> bool | None:
        """Whether a governing Vitest or Jest config collects *path*.

        Returns:
            True: at least one governing config collects *path*.
            False: governing config(s) exist, but none collects *path*.
            None: no config governs *path*, or governing config is unknown.
        """
        p = PurePosixPath(path)
        verdicts = [
            _js_collected(p, p.relative_to(parent), rules)
            for parent in p.parents
            if (rules := self.by_dir.get("" if str(parent) == "." else str(parent))) is not None
        ]
        return any(verdicts) if verdicts else None


def _expand_braces(pattern: str) -> list[str]:
    """Expand simple shell/minimatch brace expansions like ``{ts,tsx}``."""
    match = re.search(r"\{([^{}]+)\}", pattern)
    if not match:
        return [pattern]
    parts = match.group(1).split(",")
    start, end = pattern[: match.start()], pattern[match.end() :]
    expanded = []
    for part in parts:
        expanded.extend(_expand_braces(f"{start}{part}{end}"))
    return expanded


def _js_collected(path: PurePosixPath, rel: PurePosixPath, rules: _JsCollection) -> bool:
    posix_path = path.as_posix()
    posix_rel = rel.as_posix()

    # 1. Regex patterns (e.g. Jest testRegex)
    for reg in rules.regexes:
        try:
            if re.search(reg, posix_path) or re.search(reg, posix_rel):
                return True
        except re.error:
            continue

    # 2. Glob patterns (Vitest include, Jest testMatch), relative to the config directory
    globs = _compiled_globs(rules.patterns)
    if globs is None:
        return False
    positives, negations = globs
    return any(p.fullmatch(posix_rel) for p in positives) and not any(
        n.fullmatch(posix_rel) for n in negations
    )


@lru_cache(maxsize=256)
def _compiled_globs(
    patterns: tuple[str, ...],
) -> tuple[tuple[re.Pattern[str], ...], tuple[re.Pattern[str], ...]] | None:
    """``(positive, negation)`` regexes for *patterns*, or ``None`` if a negation is unreadable.

    A positive pattern the translator cannot read never matches, and a negation it
    cannot read makes the whole set collect nothing: either way no file is promoted.
    """
    positives: list[re.Pattern[str]] = []
    negations: list[re.Pattern[str]] = []
    for pat in patterns:
        for glob in _expand_braces(pat):
            negated = glob.startswith("!") and not glob.startswith("!(")
            regex = _glob_regex(glob[1:] if negated else glob)
            if regex is None:
                if negated:
                    return None
                continue
            (negations if negated else positives).append(re.compile(regex))
    return tuple(positives), tuple(negations)


def _glob_regex(glob: str) -> str | None:
    """Regex for one brace-expanded glob with picomatch semantics, or ``None``.

    ``**`` as a whole segment spans zero or more segments; ``*``, ``?``, ``[...]``
    and the extglobs ``?() *() +() @()`` stay inside one segment. ``!(...)`` is
    not supported.
    """
    while glob.startswith("./"):
        glob = glob[2:]
    parts = glob.split("/")
    out: list[str] = []
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        if part == "**":
            out.append(".*" if last else "(?:[^/]+/)*")
            continue
        seg = _segment_regex(part)
        if seg is None:
            return None
        out.append(seg if last else seg + "/")
    return "".join(out)


_EXTGLOB_QUANTIFIERS = {"?": "?", "*": "*", "+": "+", "@": ""}


def _segment_regex(seg: str) -> str | None:
    """Regex for glob text that cannot cross ``/``, or ``None`` if unsupported."""
    out: list[str] = []
    i = 0
    while i < len(seg):
        c = seg[i]
        nxt = seg[i + 1] if i + 1 < len(seg) else ""
        if nxt == "(" and c in "?*+@!":
            if c == "!":
                return None
            end = _closing_paren(seg, i + 1)
            if end is None:
                return None
            alts = [_segment_regex(alt) for alt in _split_alternatives(seg[i + 2 : end])]
            if any(a is None for a in alts):
                return None
            out.append(f"(?:{'|'.join(alts)}){_EXTGLOB_QUANTIFIERS[c]}")  # type: ignore[arg-type]
            i = end + 1
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            close = seg.find("]", i + 2)
            if close == -1:
                out.append(re.escape(c))
                i += 1
                continue
            body = seg[i + 1 : close]
            negate = body[:1] in ("!", "^")
            body = body[1:] if negate else body
            body = body.replace("\\", "\\\\")
            out.append(f"[^/{body}]" if negate else f"[{body}]")
            i = close + 1
        elif c == "\\" and nxt:
            out.append(re.escape(nxt))
            i += 2
        else:
            out.append(re.escape(c))
            i += 1
    return "".join(out)


def _closing_paren(text: str, start: int) -> int | None:
    depth = 0
    for j in range(start, len(text)):
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
            if depth == 0:
                return j
    return None


def _split_alternatives(text: str) -> list[str]:
    alts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in text:
        if ch == "|" and depth == 0:
            alts.append("".join(current))
            current = []
            continue
        depth += ch == "("
        depth -= ch == ")"
        current.append(ch)
    alts.append("".join(current))
    return alts


def _normalize_jest_glob(glob: str) -> str:
    """Normalize ``<rootDir>`` tokens in Jest glob patterns."""
    if glob.startswith("<rootDir>/"):
        return glob[len("<rootDir>/") :]
    if glob.startswith("<rootDir>"):
        return glob[len("<rootDir>") :]
    return glob


def _unquote_string(text: str) -> str | None:
    if len(text) >= 2 and text[0] in ('"', "'") and text[0] == text[-1]:
        content = text[1:-1]
        return content.replace(r"\"", '"').replace(r"\'", "'").replace(r"\\", "\\")
    if len(text) >= 2 and text[0] == "`" and text[-1] == "`":
        if "${" in text:
            return None
        return text[1:-1].replace(r"\`", "`").replace(r"\\", "\\")
    return None


def _extract_ast_string(node: Node) -> str | None:
    if node.type == "string":
        return _unquote_string(node.text.decode("utf-8"))
    if node.type == "template_string":
        if any(c.type == "template_substitution" for c in node.children):
            return None
        return _unquote_string(node.text.decode("utf-8"))
    return None


def _extract_ast_string_array(array_node: Node) -> list[str] | None:
    if array_node.type != "array":
        return None
    results: list[str] = []
    for child in array_node.children:
        if child.type in ("[", "]", ",", "comment"):
            continue
        val = _extract_ast_string(child)
        if val is None:
            # Spread, variable, call, or non-literal string
            return None
        results.append(val)
    return results


def _node_key(pair: Node) -> str | None:
    key_node = pair.child_by_field_name("key")
    if not key_node:
        return None
    return key_node.text.decode("utf-8").strip("'\"`")


def _node_value(pair: Node) -> Node | None:
    return pair.child_by_field_name("value")


def _parse_vitest_ast(root: Node, *, is_workspace: bool = False) -> dict[str, Any] | None:
    patterns: list[str] = []

    if is_workspace:
        # Array export at top level: export default [...]
        # Search for arrays in export or expression statements
        arrays: list[Node] = []

        def find_top_arrays(n: Node) -> None:
            if n.type == "array":
                arrays.append(n)
                return
            for c in n.children:
                find_top_arrays(c)

        find_top_arrays(root)
        if not arrays:
            return None
        for elem in arrays[0].children:
            if elem.type in ("[", "]", ",", "comment"):
                continue
            if elem.type in ("string", "template_string"):
                # Project pattern like "packages/*", valid in workspace
                continue
            if elem.type == "object":
                # Inline object
                opt = _parse_vitest_object(elem)
                if opt is None:
                    return None
                patterns.extend(opt.get("patterns", ()))
            else:
                return None
        return {"patterns": tuple(patterns), "regexes": ()}

    return _parse_vitest_object(root)


def _parse_vitest_object(node: Node) -> dict[str, Any] | None:
    def find_pairs(n: Node, target_key: str) -> list[Node]:
        # Stop at every pair: a ``test`` key inside another option (a plugin's
        # options, say) is not this config's test block.
        found: list[Node] = []
        for c in n.children:
            if c.type == "pair":
                if _node_key(c) == target_key:
                    found.append(c)
                continue
            found.extend(find_pairs(c, target_key))
        return found

    test_pairs = find_pairs(node, "test")
    patterns: list[str] = []
    has_test_block = bool(test_pairs)

    # Check top-level include if this is an inline project object
    for child in node.children:
        if child.type == "pair" and _node_key(child) == "include":
            v = _node_value(child)
            if v:
                arr = _extract_ast_string_array(v)
                if arr is None:
                    return None
                patterns.extend(arr)

    for tp in test_pairs:
        val = _node_value(tp)
        if not val or val.type != "object":
            continue
        for child in val.children:
            if child.type != "pair":
                continue
            k = _node_key(child)
            v = _node_value(child)
            if not v:
                continue
            if k == "include":
                arr = _extract_ast_string_array(v)
                if arr is None:
                    return None
                patterns.extend(arr)
            elif k == "projects" and v.type == "array":
                for proj_elem in v.children:
                    if proj_elem.type in ("[", "]", ",", "comment"):
                        continue
                    if proj_elem.type == "object":
                        p_opt = _parse_vitest_object(proj_elem)
                        if p_opt is None:
                            return None
                        patterns.extend(p_opt.get("patterns", ()))
                    elif proj_elem.type not in ("string", "template_string"):
                        return None

    if not has_test_block and not patterns:
        return None
    return {"patterns": tuple(patterns), "regexes": ()}


def _parse_jest_ast(root: Node) -> dict[str, Any] | None:
    def find_pairs(n: Node, target_keys: set[str]) -> list[Node]:
        found: list[Node] = []
        if n.type == "pair" and _node_key(n) in target_keys:
            found.append(n)
        for c in n.children:
            found.extend(find_pairs(c, target_keys))
        return found

    pairs = find_pairs(root, {"testMatch", "testRegex"})
    if not pairs:
        return None

    patterns: list[str] = []
    regexes: list[str] = []

    for p in pairs:
        k = _node_key(p)
        v = _node_value(p)
        if not v:
            continue
        if k == "testMatch":
            arr = _extract_ast_string_array(v)
            if arr is None:
                return None
            patterns.extend(_normalize_jest_glob(g) for g in arr)
        elif k == "testRegex":
            if v.type in ("string", "template_string"):
                s = _extract_ast_string(v)
                if s is None:
                    return None
                regexes.append(s)
            elif v.type == "array":
                arr = _extract_ast_string_array(v)
                if arr is None:
                    return None
                regexes.extend(arr)
            else:
                return None

    return {"patterns": tuple(patterns), "regexes": tuple(regexes)}


def js_test_options(
    name: str,
    text: str = "",
    *,
    json_data: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """The Vitest or Jest test options from one config file, or ``None``.

    Read only string-literal arrays. A spread, a variable, an import, or a
    function call makes that config unknown (``None``), never an empty list.
    """
    if name not in JS_TEST_CONFIG_NAMES:
        return None

    if name == "package.json":
        data = json_data
        if data is None and text:
            try:
                data = json.loads(text)
            except Exception:
                return None
        if not isinstance(data, dict):
            return None
        jest_block = data.get("jest")
        if not isinstance(jest_block, dict):
            return None
        patterns: list[str] = []
        regexes: list[str] = []
        if "testMatch" in jest_block:
            tm = jest_block["testMatch"]
            if not isinstance(tm, list) or not all(isinstance(x, str) for x in tm):
                return None
            patterns.extend(_normalize_jest_glob(x) for x in tm)
        if "testRegex" in jest_block:
            tr = jest_block["testRegex"]
            if isinstance(tr, str):
                regexes.append(tr)
            elif isinstance(tr, list) and all(isinstance(x, str) for x in tr):
                regexes.extend(tr)
            else:
                return None
        if not patterns and not regexes:
            return None
        return {"patterns": tuple(patterns), "regexes": tuple(regexes)}

    if name.endswith(".json"):
        try:
            data = json_data if json_data is not None else json.loads(text)
        except Exception:
            return None
        if name == "vitest.workspace.json":
            if not isinstance(data, list):
                return None
            patterns = []
            for item in data:
                if isinstance(item, dict):
                    inc = item.get("include") or item.get("test", {}).get("include")
                    if inc is not None:
                        if not isinstance(inc, list) or not all(isinstance(x, str) for x in inc):
                            return None
                        patterns.extend(inc)
            return {"patterns": tuple(patterns), "regexes": ()}
        if name == "jest.config.json":
            if not isinstance(data, dict):
                return None
            patterns = []
            regexes = []
            if "testMatch" in data:
                tm = data["testMatch"]
                if not isinstance(tm, list) or not all(isinstance(x, str) for x in tm):
                    return None
                patterns.extend(_normalize_jest_glob(x) for x in tm)
            if "testRegex" in data:
                tr = data["testRegex"]
                if isinstance(tr, str):
                    regexes.append(tr)
                elif isinstance(tr, list) and all(isinstance(x, str) for x in tr):
                    regexes.extend(tr)
                else:
                    return None
            return {"patterns": tuple(patterns), "regexes": tuple(regexes)}

    # JS/TS configs: parse with tree-sitter
    try:
        import tree_sitter_typescript
        from tree_sitter import Language, Parser

        ts_lang = Language(tree_sitter_typescript.language_typescript())
        parser = Parser(ts_lang)
        tree = parser.parse(text.encode("utf-8"))
    except Exception:
        return None

    if name.startswith("vitest.workspace"):
        return _parse_vitest_ast(tree.root_node, is_workspace=True)
    if name.startswith("vitest.config") or name.startswith("vite.config"):
        return _parse_vitest_ast(tree.root_node)
    if name.startswith("jest.config"):
        return _parse_jest_ast(tree.root_node)

    return None


def js_test_roots(configs: Iterable[tuple[str, Mapping[str, Any]]]) -> JsTestRoots:
    """Build from ``(config path, options)`` pairs (:func:`js_test_options`).

    The highest-precedence config in each directory wins.
    """
    chosen: dict[str, tuple[int, Mapping[str, Any]]] = {}
    for path, options in configs:
        p = PurePosixPath(path)
        key = "" if str(p.parent) == "." else str(p.parent)
        rank = _JS_CONFIG_PRECEDENCE.get(p.name, len(_JS_CONFIG_PRECEDENCE))
        if key not in chosen or rank < chosen[key][0]:
            chosen[key] = (rank, options)
    return JsTestRoots(
        {
            key: _JsCollection(
                tuple(o.get("patterns", ())),
                tuple(o.get("regexes", ())),
            )
            for key, (_, o) in chosen.items()
        }
    )


def read_js_test_roots(texts: Iterable[tuple[str, str]]) -> JsTestRoots:
    """:func:`js_test_roots` from ``(config path, text)`` pairs."""
    return js_test_roots(
        (path, options)
        for path, text in texts
        if (options := js_test_options(PurePosixPath(path).name, text)) is not None
    )
