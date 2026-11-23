import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PreviewBadge, PreviewBadgeStringsContext } from "../../src/health/preview-badge";
import { RefactoringCard } from "../../src/health/refactoring-card";
import { PerformanceView } from "../../src/health/performance-view";
import { adapter, opportunity, page } from "./fixtures/performance";

const NOTE = "preview, measured 22% precision on 55 judged plans from 2 Java repos, 2026-10-15";

describe("PreviewBadge", () => {
  it("names the note in its label and shows it in a tooltip on focus", async () => {
    render(<PreviewBadge note={NOTE} />);
    const badge = screen.getByRole("button", { name: `Preview: ${NOTE}` });
    expect(badge.textContent).toBe("Preview");
    fireEvent.focus(badge);
    expect((await screen.findAllByText(NOTE)).length).toBeGreaterThan(0);
  });

  it("uses the strings a host provides", () => {
    render(
      <PreviewBadgeStringsContext.Provider value={{ label: "预览", ariaLabel: (n) => `预览：${n}` }}>
        <PreviewBadge note={NOTE} />
      </PreviewBadgeStringsContext.Provider>,
    );
    expect(screen.getByRole("button", { name: `预览：${NOTE}` }).textContent).toBe("预览");
  });

  it("lets an explicit label override the provided one", () => {
    render(<PreviewBadge note={NOTE} label="Beta" />);
    expect(screen.getByRole("button", { name: `Beta: ${NOTE}` }).textContent).toBe("Beta");
  });

  it.each([undefined, null, "", "   "])("renders nothing for %p", (note) => {
    const { container } = render(<PreviewBadge note={note} />);
    expect(container.firstChild).toBeNull();
  });
});

describe("Preview badge on list rows", () => {
  it("marks the preview finding in a file's findings and leaves the other unmarked", () => {
    render(
      <RefactoringCard
        target={{
          file_path: "src/main/java/App.java",
          score: 4,
          nloc: 300,
          primary_biomarker: "brain_method",
          primary_severity: "high",
          primary_reason: "Oversized function.",
          primary_function: "run",
          primary_line_start: 10,
          primary_line_end: 200,
          total_impact: 3,
          finding_count: 2,
          biomarkers: ["brain_method"],
          effort_bucket: "L",
          impact_per_effort: 1,
          all_findings: [
            { id: "a", biomarker_type: "brain_method", severity: "high", function_name: "run", health_impact: 2, reason: "Oversized function.", preview: NOTE },
            { id: "b", biomarker_type: "error_handling", severity: "low", function_name: null, health_impact: 1, reason: "Broad catch.", preview: null },
          ],
        }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Show all 2 findings/ }));
    expect(document.querySelectorAll("[data-preview]")).toHaveLength(1);
  });

  it("marks a work-queue card whose file is served from a preview cell", () => {
    render(
      <RefactoringCard
        target={{
          file_path: "src/main/java/App.java",
          score: 4,
          nloc: 300,
          primary_biomarker: "brain_method",
          primary_severity: "high",
          primary_reason: "Oversized function.",
          primary_function: "run",
          primary_line_start: 10,
          primary_line_end: 200,
          total_impact: 3,
          finding_count: 1,
          biomarkers: ["brain_method"],
          effort_bucket: "L",
          impact_per_effort: 1,
          preview: NOTE,
        }}
      />,
    );
    expect(screen.getByRole("button", { name: `Preview: ${NOTE}` })).toBeTruthy();
  });

  it("marks a preview performance opportunity in the queue", async () => {
    const item = opportunity({ preview: NOTE });
    render(<PerformanceView adapter={adapter({ getPerformanceOpportunities: async () => page({ items: [item] }) })} />);
    expect(await screen.findByRole("button", { name: `Preview: ${NOTE}` })).toBeTruthy();
  });
});
