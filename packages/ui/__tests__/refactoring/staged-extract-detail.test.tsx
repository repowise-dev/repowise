import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { RefactoringPlan } from "@repowise-dev/types/refactoring";

import { PlanDetail } from "../../src/refactoring/plan-detail";
import { extractMethodStages } from "../../src/refactoring/types";

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

describe("extractMethodStages bounds", () => {
  const withBounds = (start: unknown, end: unknown) =>
    ({
      ...plan,
      plan: { ...plan.plan, stages: [{ ...first, span: { start, end } }, second] },
    }) as unknown as RefactoringPlan;

  it.each([
    ["a numeric string", "10", 30],
    ["a boolean", true, 30],
    ["a float", 10.5, 30],
    ["zero", 0, 30],
    ["an end that is a string", 10, "30"],
  ])("drops a stage whose bound is %s", (_label, start, end) => {
    const stages = extractMethodStages(withBounds(start, end));
    expect(stages.map((s) => s.span)).toEqual([{ start: 34, end: 60 }]);
  });

  it("keeps whole positive line numbers", () => {
    expect(extractMethodStages(withBounds(10, 30)).map((s) => s.span.start)).toEqual([10, 34]);
  });
});

describe("PlanDetail with fewer than two valid stages", () => {
  it("renders the single-span view for one stage", () => {
    const single = { ...plan, plan: { ...plan.plan, stages: [first] } } satisfies RefactoringPlan;
    render(<PlanDetail plan={single} />);
    expect(screen.queryByText(/helpers it calls in order/)).toBeNull();
    expect(screen.getByText("Extract span")).toBeTruthy();
  });

  it("renders the single-span view when invalid bounds leave one stage", () => {
    const broken = {
      ...plan,
      plan: { ...plan.plan, stages: [{ ...first, span: { start: "10", end: 30 } }, second] },
    } as unknown as RefactoringPlan;
    render(<PlanDetail plan={broken} />);
    expect(screen.queryByText(/helpers it calls in order/)).toBeNull();
  });
});
