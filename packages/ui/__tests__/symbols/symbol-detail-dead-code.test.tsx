import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { SymbolDetailBody } from "../../src/symbols/symbol-detail-body.js";
import type { SymbolBodyDeadFinding } from "@repowise-dev/types/symbols";

const finding = (lines: number | null): SymbolBodyDeadFinding => ({
  id: "f1",
  kind: "unused_export",
  reason: "no importers",
  lines,
  safe_to_delete: false,
});

const renderWith = (lines: number | null) =>
  render(
    <SymbolDetailBody
      data={{
        identity: { name: "helper", kind: "function", file_path: "src/a.py", start_line: 1 },
        dead_code: [finding(lines)],
      }}
    />,
  );

describe("symbol detail dead code", () => {
  it("shows the line count when it is known", () => {
    renderWith(12);
    expect(screen.getByText(/no importers \(12 lines\)/)).toBeInTheDocument();
  });

  it("omits the count when it could not be measured", () => {
    renderWith(null);
    expect(screen.getByText(/no importers/)).toBeInTheDocument();
    expect(screen.queryByText(/lines\)/)).not.toBeInTheDocument();
  });
});
