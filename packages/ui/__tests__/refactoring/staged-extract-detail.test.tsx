import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { RefactoringPlan } from "@repowise-dev/types/refactoring";

import { PlanDetail } from "../../src/refactoring/plan-detail";

function stage(start: number, end: number, name: string | null, out: string[]) {
  return {
    span: { start, end },
    params: ["ctx"],
    returns: out,
    suggested_name: name,
    ccn: 6,
    nloc: 20,
    context_params: ["session"],
    new_symbol: {
      kind: "function",
      async: true,
      signature_text: `async def ${name ?? "<name>"}(ctx: _PersistContext):`,
      return_text: out.length ? `return ${out.join(", ")}` : null,
      notes: ["Read session in the helper as ctx.session."],
    },
    call_site: {
      replace_span: { start, end },
      new_text: `${out.length ? `${out.join(", ")} = ` : ""}await ${name ?? "<name>"}(ctx)`,
    },
  };
}

const first = stage(10, 30, "_persist_pages", ["pages"]);
const second = stage(34, 60, null, []);

const plan = {
  id: "plan-staged",
  refactoring_type: "extract_method",
  file_path: "pkg/persist.py",
  target_symbol: "persist",
  line_start: 5,
  line_end: 90,
  plan: {
    ...first,
    stages: [first, second],
    parameter_object: {
      name: "_PersistContext",
      var: "ctx",
      fields: [{ name: "session", type: null }],
      declaration_text: "@dataclass(frozen=True)\nclass _PersistContext:\n    session: Any",
      construct_text: "ctx = _PersistContext(session=session)",
      construct_before: 10,
      notes: [],
    },
    orchestrator: { ccn_before: 40, ccn_after: 12 },
  },
  evidence: { slice_nloc: 40, ccn_removed: 28 },
  impact_delta: 0.7,
  effort_bucket: "L",
  blast_radius: { scope: "local" },
  confidence: "medium",
  source_biomarker: "brain_method",
  rank_score: 5,
} satisfies RefactoringPlan;

describe("PlanDetail for a staged Extract Method plan", () => {
  it("lists every stage in order, not only the first", () => {
    render(<PlanDetail plan={plan} />);
    expect(screen.getByText(/into 2 helpers it calls in order/)).toBeTruthy();
    expect(screen.getByText(/from 40 to about 12/)).toBeTruthy();
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]!.textContent).toContain("_persist_pages");
    expect(items[0]!.textContent).toContain("lines 10–30");
    expect(items[1]!.textContent).toContain("Stage 2");
    expect(items[1]!.textContent).toContain("lines 34–60");
  });

  it("shows the shared object once and each stage's texts", () => {
    render(<PlanDetail plan={plan} />);
    expect(screen.getAllByText(/class _PersistContext/)).toHaveLength(1);
    expect(screen.getByText("ctx = _PersistContext(session=session)")).toBeTruthy();
    expect(screen.getByText("return pages")).toBeTruthy();
    expect(screen.getByText("pages = await _persist_pages(ctx)")).toBeTruthy();
    expect(screen.getByText("await <name>(ctx)")).toBeTruthy();
  });

  it("opens the first stage and folds the rest", () => {
    const { container } = render(<PlanDetail plan={plan} />);
    const folds = container.querySelectorAll("details");
    expect(folds).toHaveLength(2);
    expect(folds[0]!.open).toBe(true);
    expect(folds[1]!.open).toBe(false);
  });
});
