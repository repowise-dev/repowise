/**
 * The structural field: one mark per file, four distinguishable shapes, and a
 * hue assignment derived from the data rather than fixed per type.
 *
 * The field used to fill every mark with one accent at two opacities, so colour
 * encoded nothing, and `extract_class` and `move_method` both drew a triangle,
 * so two of the four types were indistinguishable on the only channel that
 * carried type at all.
 */

import { render, screen } from "@testing-library/react";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import type { RefactoringOpportunity } from "@repowise-dev/types/refactoring";

import {
  isRecededType,
  salienceFill,
  salienceOrder,
  structuralMarks,
  type StructuralMark,
} from "../../src/refactoring/opportunity";
import { StructuralMap } from "../../src/refactoring/structural-map";

function opportunity(
  overrides: Partial<RefactoringOpportunity> = {},
): RefactoringOpportunity {
  return {
    opportunity_id: `refop2_${overrides.file_path ?? "x"}`,
    refactoring_model_version: 2,
    status: "open",
    file_path: "src/a.py",
    lead_biomarker: "long_file",
    lead_refactoring_type: "split_file",
    addresses_primary_problem: true,
    effort_bucket: "M",
    confidence: "high",
    step_count: 2,
    mechanical_steps: 1,
    judgment_steps: 1,
    evidence_total: 0,
    affected_files_total: 1,
    recoverable_health: 1,
    rank_score: 1,
    rank_position: 1,
    queue_position: 1,
    rank_factors: {},
    why_ranked: [],
    file_nloc: 400,
    dependents: 12,
    ...overrides,
  };
}

function mark(leadType: string, i: number): StructuralMark {
  return {
    opportunityId: `refop2_${leadType}_${i}`,
    filePath: `src/${leadType}${i}.py`,
    leadType,
    stepCount: 1,
    status: "open",
    x: 5,
    y: 100,
  };
}

describe("structuralMarks", () => {
  it("draws one mark per file, not one per plan", () => {
    // The same file caught by two detectors composes to one opportunity, so it
    // can only ever produce one mark. That is the whole point of the unit.
    const marks = structuralMarks([
      opportunity({ file_path: "src/a.py", opportunity_id: "refop2_a" }),
      opportunity({ file_path: "src/b.py", opportunity_id: "refop2_b" }),
    ]);
    expect(marks).toHaveLength(2);
    expect(marks.map((m) => m.filePath)).toEqual(["src/a.py", "src/b.py"]);
  });

  it("keeps only the structural lead types", () => {
    const marks = structuralMarks([
      opportunity({ lead_refactoring_type: "split_file", opportunity_id: "s" }),
      opportunity({ lead_refactoring_type: "extract_method", opportunity_id: "e" }),
      opportunity({ lead_refactoring_type: "break_cycle", opportunity_id: "b" }),
    ]);
    expect(marks.map((m) => m.leadType)).toEqual(["split_file", "break_cycle"]);
  });

  it("drops an opportunity with no measured figures instead of plotting the origin", () => {
    // Absent is not zero. A store written before the finalizer recorded these
    // has neither figure, and a 1,400-line file at (0, 0) is a lie.
    const { file_nloc: _n, ...noLines } = opportunity();
    const { dependents: _d, ...noReach } = opportunity();
    expect(structuralMarks([noLines])).toHaveLength(0);
    expect(structuralMarks([noReach])).toHaveLength(0);
    // A file nothing imports is a measured zero, and belongs on the field.
    expect(structuralMarks([opportunity({ dependents: 0 })])).toHaveLength(1);
  });
});

describe("salience", () => {
  it("derives the hue order from the data, never from a fixed per-type map", () => {
    const splitLed = [mark("split_file", 1), mark("split_file", 2), mark("break_cycle", 1)];
    expect(salienceOrder(splitLed).slice(0, 2)).toEqual(["split_file", "break_cycle"]);
    // `accent-primary`, not `accent-fill`: they are the same orange in dark
    // mode, but light deepens the former to clear the 3.0 non-text floor on
    // warm paper (2.12:1 against 4.58:1, measured).
    expect(salienceFill("split_file", salienceOrder(splitLed))).toBe(
      "var(--color-accent-primary)",
    );

    // A repo whose cycles outnumber its oversized files gets the opposite
    // assignment. A hardcoded hue would paint its dominant type as the rare one.
    const cycleLed = [mark("break_cycle", 1), mark("break_cycle", 2), mark("split_file", 1)];
    expect(salienceOrder(cycleLed).slice(0, 2)).toEqual(["break_cycle", "split_file"]);
    expect(salienceFill("break_cycle", salienceOrder(cycleLed))).toBe(
      "var(--color-accent-primary)",
    );
    expect(salienceFill("split_file", salienceOrder(cycleLed))).toBe(
      "var(--color-accent-secondary)",
    );
  });

  it("spends the accent pair, then neutrals - never the sequential ramp", () => {
    const order = ["split_file", "break_cycle", "move_method", "extract_class"];
    const fills = order.map((t) => salienceFill(t, order));
    expect(fills).toEqual([
      "var(--color-accent-primary)",
      "var(--color-accent-secondary)",
      "var(--color-neutral-1)",
      "var(--color-neutral-2)",
    ]);
    // globals.css reserves --color-ramp-* for magnitude; these are categories.
    expect(fills.some((f) => f.includes("ramp"))).toBe(false);
  });

  it("breaks a count tie deterministically", () => {
    const tied = [mark("break_cycle", 1), mark("split_file", 1)];
    expect(salienceOrder(tied)).toEqual(salienceOrder([...tied].reverse()));
  });

  it("knows which types have receded past the two hues", () => {
    const order = ["split_file", "break_cycle", "move_method"];
    expect(isRecededType("split_file", order)).toBe(false);
    expect(isRecededType("break_cycle", order)).toBe(false);
    expect(isRecededType("move_method", order)).toBe(true);
  });
});

describe("StructuralMap", () => {
  // The field sizes itself from its container, and jsdom reports every element
  // as zero-width, so without this it renders its axes and legend and no marks
  // at all - which would let every assertion below pass vacuously.
  let clientWidth: PropertyDescriptor | undefined;
  beforeAll(() => {
    clientWidth = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "clientWidth");
    Object.defineProperty(HTMLElement.prototype, "clientWidth", {
      configurable: true,
      get: () => 900,
    });
  });
  afterAll(() => {
    if (clientWidth) Object.defineProperty(HTMLElement.prototype, "clientWidth", clientWidth);
  });

  const fourTypes: StructuralMark[] = [
    { ...mark("split_file", 1), x: 3, y: 100 },
    { ...mark("break_cycle", 1), x: 9, y: 300 },
    { ...mark("move_method", 1), x: 20, y: 500 },
    { ...mark("extract_class", 1), x: 40, y: 700 },
  ];

  it("gives each of the four structural types its own shape", () => {
    const { container } = render(<StructuralMap marks={fourTypes} onSelect={() => {}} />);
    const svg = container.querySelector("svg");
    // Circle (split_file) plus three distinct paths. `extract_class` and
    // `move_method` used to share the triangle, so two types drew identically.
    const paths = [...(svg?.querySelectorAll("path") ?? [])].map((p) => p.getAttribute("d"));
    const unique = new Set(paths.filter(Boolean));
    expect(unique.size).toBe(3);
  });

  it("names every mark, so type survives with no colour at all", () => {
    render(<StructuralMap marks={fourTypes} onSelect={() => {}} />);
    for (const label of ["Split File", "Break Cycle", "Move Method", "Extract Class"]) {
      expect(screen.getAllByLabelText(new RegExp(`^${label},`)).length).toBeGreaterThan(0);
    }
  });

  it("keeps every mark reachable by keyboard", () => {
    render(<StructuralMap marks={fourTypes} onSelect={() => {}} />);
    const marks = screen.getAllByRole("button");
    expect(marks).toHaveLength(4);
    for (const node of marks) expect(node.getAttribute("tabindex")).toBe("0");
  });

  it("says out loud when a type has receded into grey", () => {
    render(<StructuralMap marks={fourTypes} onSelect={() => {}} />);
    expect(screen.getByText(/Shape is the type/)).toBeTruthy();
    expect(screen.getByText(/the rarer types sit in grey/)).toBeTruthy();
  });

  it("outlines every mark, so a receded fill is never the only thing drawing it", () => {
    // The neutral tiers sit between 1.25:1 and 2.45:1 against the page in both
    // themes, under the 3.0 floor for non-text UI. The stroke is what carries
    // visibility; the fill only ranks attention.
    const { container } = render(<StructuralMap marks={fourTypes} onSelect={() => {}} />);
    const drawn = [...container.querySelectorAll("svg path, svg circle")].filter((node) =>
      node.getAttribute("fill")?.startsWith("var("),
    );
    expect(drawn.length).toBeGreaterThan(0);
    for (const node of drawn) {
      expect(node.getAttribute("stroke")).toBe("var(--color-text-tertiary)");
    }
  });

  it("is a group, not an image, so its marks stay in the accessibility tree", () => {
    const { container } = render(<StructuralMap marks={fourTypes} onSelect={() => {}} />);
    const svg = container.querySelector("svg");
    expect(svg?.getAttribute("role")).toBe("group");
  });

  it("stays quiet about receding when only the two hues are in play", () => {
    render(
      <StructuralMap
        marks={[fourTypes[0]!, fourTypes[1]!]}
        onSelect={() => {}}
      />,
    );
    expect(screen.queryByText(/Shape is the type/)).toBeNull();
  });

  it("states its own bound rather than covering part of the subject silently", () => {
    render(<StructuralMap marks={fourTypes} dropped={7} onSelect={() => {}} />);
    expect(screen.getByText(/7 not plotted/)).toBeTruthy();
  });
});
