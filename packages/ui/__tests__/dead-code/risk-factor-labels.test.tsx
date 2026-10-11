/**
 * Risk factors reach a reader as English, not as API slugs.
 *
 * Both surfaces that render `risk_factors` used to `join(", ")` the raw tags,
 * so the row marker and the agent prompt said "config, asset" where the
 * engine's own evidence line says "configuration, runtime-loaded web asset".
 *
 * The two were uncovered in different ways, which is worth keeping straight.
 * The marker was *executed* with a real factor — `dead-code-view`'s fixture
 * gives one finding `risk_factors: ["bootstrap"]` and the table renders it —
 * but nothing asserted on the text. The prompt branch was never executed at
 * all: the only prompt test seeds from `safeFindings`, which filters on
 * `safe_to_delete`, and every safe fixture has an empty factor list.
 */

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import type { DeadCodeFinding } from "@repowise-dev/types/dead-code";

import { FindingSafety } from "../../src/dead-code/finding-cells.js";
import { buildDeadCodeAiPrompt } from "../../src/health/ai-prompt-builder.js";

function finding(over: Partial<DeadCodeFinding>): DeadCodeFinding {
  return {
    id: "f1",
    kind: "unreachable_file",
    file_path: "public/sw.js",
    symbol_name: null,
    symbol_kind: null,
    confidence: 0.4,
    reason: "No importers",
    lines: 10,
    safe_to_delete: false,
    risk_factors: [],
    primary_owner: null,
    status: "open",
    note: null,
    ...over,
  };
}

describe("risk-factor labels", () => {
  it("names the risk factors as visible text, with labels, not slugs", () => {
    render(<FindingSafety finding={finding({ risk_factors: ["asset", "config"] })} />);
    const marker = screen.getByText(/may load at runtime/);
    expect(marker.textContent).toContain("runtime-loaded web asset, configuration");
    expect(marker.textContent).not.toContain("asset, config)");
    expect(screen.getByText("Review first")).toBeInTheDocument();
  });

  it("marks nothing on a deletion-ready row", () => {
    const { container } = render(
      <FindingSafety finding={finding({ safe_to_delete: true, confidence: 0.9 })} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("marks only deletion-ready rows when they are the exception", () => {
    const { rerender, container } = render(
      <FindingSafety mark="safe" finding={finding({ safe_to_delete: true, confidence: 0.9 })} />,
    );
    expect(screen.getByText("Deletion-ready")).toBeInTheDocument();
    rerender(<FindingSafety mark="safe" finding={finding({})} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<FindingSafety mark="safe" finding={finding({ risk_factors: ["config"] })} />);
    expect(container.textContent).toBe("may load at runtime (configuration)");
  });

  it("says just Review first when no risk factor is known", () => {
    const { container } = render(<FindingSafety finding={finding({})} />);
    expect(container.textContent).toBe("Review first");
  });

  it("labels the risk factors in the dead-code agent prompt", () => {
    const prompt = buildDeadCodeAiPrompt({
      findings: [{ file_path: "public/sw.js", lines: 10, risk_factors: ["asset"] }],
    });
    expect(prompt).toContain("Runtime-load risk to rule out first: runtime-loaded web asset");
  });

  it("falls back to the raw tag for a factor the label map has not learned", () => {
    render(<FindingSafety finding={finding({ risk_factors: ["telemetry"] })} />);
    expect(screen.getByText(/may load at runtime/).textContent).toContain("telemetry");
  });
});
