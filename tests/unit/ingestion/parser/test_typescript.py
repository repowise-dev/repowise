"""Unit tests for the unified ASTParser.

Tests parse inline byte strings so no filesystem I/O is needed.
Covers Python, TypeScript, Go, Rust, Java, C++ — one test class per language.
"""

from __future__ import annotations

from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.parser._helpers import _make_file_info

TS_SOURCE = b"""/**
 * Sample TypeScript client module.
 * Exports ApiClient and related types.
 */

import type {
  ApiClientConfig,
  CalculationRequest,
  CalculationResponse,
} from "./types";
import { validateRequest, parseApiError } from "./utils";

/** Error from the API. */
export class ApiClientError extends Error {
  public readonly apiError: unknown;
  constructor(apiError: unknown) {
    super("API error");
    this.apiError = apiError;
  }
}

/** Validation error. */
export class ValidationError extends Error {}

const DEFAULT_TIMEOUT_MS = 10_000;

/** Typed HTTP client. */
export class ApiClient {
  private readonly baseUrl: string;

  constructor(config: ApiClientConfig) {
    this.baseUrl = config.baseUrl;
  }

  async calculate(request: CalculationRequest): Promise<CalculationResponse> {
    return this.post("/calculations", request);
  }

  async healthCheck(): Promise<boolean> {
    return true;
  }

  private async post<T>(path: string, body: unknown): Promise<T> {
    return {} as T;
  }
}

export function createClient(config: ApiClientConfig): ApiClient {
  return new ApiClient(config);
}
"""


class TestTypeScriptParser:
    def test_finds_classes(self, parser: ASTParser) -> None:
        fi = _make_file_info("typescript_pkg/src/client.ts", "typescript")
        result = parser.parse_file(fi, TS_SOURCE)
        class_names = [s.name for s in result.symbols if s.kind == "class"]
        assert "ApiClient" in class_names
        assert "ApiClientError" in class_names
        assert "ValidationError" in class_names

    def test_finds_methods(self, parser: ASTParser) -> None:
        fi = _make_file_info("typescript_pkg/src/client.ts", "typescript")
        result = parser.parse_file(fi, TS_SOURCE)
        method_names = [s.name for s in result.symbols if s.kind == "method"]
        assert "calculate" in method_names
        assert "healthCheck" in method_names

    def test_finds_top_level_function(self, parser: ASTParser) -> None:
        fi = _make_file_info("typescript_pkg/src/client.ts", "typescript")
        result = parser.parse_file(fi, TS_SOURCE)
        fn_names = [s.name for s in result.symbols if s.kind == "function"]
        assert "createClient" in fn_names

    def test_parses_imports(self, parser: ASTParser) -> None:
        fi = _make_file_info("typescript_pkg/src/client.ts", "typescript")
        result = parser.parse_file(fi, TS_SOURCE)
        module_paths = [i.module_path for i in result.imports]
        assert "./types" in module_paths
        assert "./utils" in module_paths

    def test_relative_imports_flagged(self, parser: ASTParser) -> None:
        fi = _make_file_info("typescript_pkg/src/client.ts", "typescript")
        result = parser.parse_file(fi, TS_SOURCE)
        types_import = next(i for i in result.imports if i.module_path == "./types")
        assert types_import.is_relative is True

    def test_no_parse_errors(self, parser: ASTParser) -> None:
        fi = _make_file_info("typescript_pkg/src/client.ts", "typescript")
        result = parser.parse_file(fi, TS_SOURCE)
        assert result.parse_errors == []

    def test_nested_helpers_inside_component_not_extracted(self, parser: ASTParser) -> None:
        # Regression (D5): React component bodies contain helper function
        # declarations and arrow-const handlers (``handleSave``,
        # ``handleKeyDown``, ``Section``) that were being flattened to
        # top-level public symbols and surfacing as ``unused_export``
        # findings with confidence 1.0.
        src = b"""
export function GeneralForm({ onSave }: Props) {
  function addPattern(p: string) { return p; }
  async function handleSave() { onSave(); }
  const Section = ({ title }: { title: string }) => <div>{title}</div>;
  const handleKeyDown = (e: KeyboardEvent) => { e.preventDefault(); };
  return <Section title="x" />;
}

export const Wrapper = () => {
  const inner = () => 42;
  return inner();
};
"""
        fi = _make_file_info("ui/src/general-form.tsx", "typescript")
        result = parser.parse_file(fi, src)
        names = {s.name for s in result.symbols}
        assert "GeneralForm" in names
        assert "Wrapper" in names
        for hidden in ("addPattern", "handleSave", "Section", "handleKeyDown", "inner"):
            assert hidden not in names, f"nested helper {hidden} leaked to top level"

    def test_tsx_file_uses_jsx_grammar(self, parser: ASTParser) -> None:
        # .tsx files require the JSX-aware grammar variant; the default
        # typescript grammar errors out on ``<Component />`` and recovers
        # by hoisting nested helpers out of the broken component body.
        src = b"""
export function Card({ title }: { title: string }) {
  function handleClick() { console.log("clicked"); }
  return <div onClick={handleClick}>{title}</div>;
}
"""
        fi = _make_file_info("ui/src/card.tsx", "typescript")
        result = parser.parse_file(fi, src)
        assert result.parse_errors == []
        names = {s.name for s in result.symbols}
        assert "Card" in names
        assert "handleClick" not in names

    def test_jsx_element_registers_as_call_target(self, parser: ASTParser) -> None:
        # Regression: ``<StatRow ... />`` inside the same file as the
        # ``StatRow`` component definition was not registering as a call,
        # so same-file-only sub-components surfaced as unused public
        # exports at confidence 1.0. The tsx-specific JSX captures now
        # emit a CallSite for both self-closing and paired elements.
        src = b"""
export function StatRow({ value }: { value: number }) {
  return <span>{value}</span>;
}

export function Section({ title }: { title: string }) {
  return <h2>{title}</h2>;
}

export function Card() {
  return (
    <div>
      <Section title="x" />
      <StatRow value={1}></StatRow>
    </div>
  );
}
"""
        fi = _make_file_info("ui/src/card.tsx", "typescript")
        result = parser.parse_file(fi, src)
        targets = {c.target_name for c in result.calls}
        assert "StatRow" in targets
        assert "Section" in targets

    def test_jsx_element_call_works_for_jsx_grammar(self, parser: ASTParser) -> None:
        # Same behaviour for .jsx (plain JavaScript grammar already
        # supports JSX node types natively).
        src = b"""
export function StatRow({ value }) {
  return <span>{value}</span>;
}

export function Card() {
  return <StatRow value={1} />;
}
"""
        fi = _make_file_info("ui/src/card.jsx", "javascript")
        result = parser.parse_file(fi, src)
        targets = {c.target_name for c in result.calls}
        assert "StatRow" in targets

    def test_class_methods_still_extracted(self, parser: ASTParser) -> None:
        # Negative for D5: methods inside class bodies must still be
        # extracted — only function/method *bodies* count as nesting.
        src = b"""
export class Service {
  run() { return 1; }
  private helper() { return 2; }
}
"""
        fi = _make_file_info("ui/src/service.ts", "typescript")
        result = parser.parse_file(fi, src)
        method_names = {s.name for s in result.symbols if s.kind == "method"}
        assert {"run", "helper"} <= method_names
