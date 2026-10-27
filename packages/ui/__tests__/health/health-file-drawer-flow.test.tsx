/**
 * The drawer as the map's one selection surface: non-modal, swapped in place,
 * honest about a failed load, and built to be read in five seconds: a plain
 * verdict, the top issues, vital signs, and everything else collapsed.
 */

import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { RefactoringOpportunity } from "@repowise-dev/types/refactoring";
import { HealthFileDrawer } from "../../src/health/health-file-drawer.js";
import {
  drawerFinding as finding,
  drawerMetric as metric,
  drawerSignals as signals,
  openSection,
} from "./drawer-test-utils.js";
import { ScoreBreakdown } from "../../src/health/score-breakdown.js";
import { fileVerdict } from "../../src/health/plain-language.js";

const five = () => [
  finding({ id: "a", biomarker_type: "nested_complexity", function_name: "one", health_impact: 0.5 }),
  finding({ id: "b", biomarker_type: "brain_method", function_name: "two", health_impact: 3.0 }),
  finding({ id: "c", biomarker_type: "complex_method", function_name: "three", health_impact: 1.0 }),
  finding({ id: "d", biomarker_type: "large_method", function_name: "four", health_impact: 2.0 }),
  finding({ id: "e", biomarker_type: "primitive_obsession", function_name: "five", health_impact: 0.1 }),
];

const whatsWrong = () => within(screen.getByRole("region", { name: "What's wrong" }));

describe("HealthFileDrawer as one selection surface", () => {
  it("stays open when the reader clicks the page behind it, and Escape closes it", () => {
    const onClose = vi.fn();
    render(
      <>
        <button type="button">next file on the map</button>
        <HealthFileDrawer open onClose={onClose} metric={metric()} />
      </>,
    );
    // Non-modal: a click behind is the reader picking the next file.
    const behind = screen.getByRole("button", { name: "next file on the map" });
    fireEvent.pointerDown(behind);
    fireEvent.click(behind);
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("names the next file at once and dims the previous one until it lands", () => {
    const { rerender } = render(
      <HealthFileDrawer open onClose={() => {}} filePath="src/a.py" metric={metric()} />,
    );
    const title = screen.getByRole("heading", { name: /a\.py/ });
    expect(title).toHaveTextContent("a.py");
    expect(title).toHaveTextContent("src/");
    rerender(
      <HealthFileDrawer open onClose={() => {}} filePath="src/b.py" loading metric={metric()} />,
    );
    expect(screen.getByRole("heading", { name: /b\.py/ })).toBeInTheDocument();
    expect(document.querySelector('[aria-busy="true"]')).not.toBeNull();
    expect(screen.getByText("1.0")).toBeInTheDocument();
  });

  it("makes the previous file's body inert while the next one loads", () => {
    const onStatus = vi.fn(async () => {});
    const findings = [finding({ id: "a", function_name: "one" })];
    const { rerender } = render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        filePath="src/a.py"
        metric={metric()}
        findings={findings}
        onFindingStatusChange={onStatus}
      />,
    );
    const busy = () => document.querySelector('[aria-busy="true"]');
    expect(busy()).toBeNull();
    rerender(
      <HealthFileDrawer
        open
        onClose={() => {}}
        filePath="src/b.py"
        loading
        metric={metric()}
        findings={findings}
        onFindingStatusChange={onStatus}
      />,
    );
    // A triage write from A's body would revalidate B's key, so nothing in it
    // can be reached until B lands. jsdom does not prune inert subtrees, so
    // the attribute is the contract.
    expect(busy()).toHaveAttribute("inert");
    expect(busy()?.className).toContain("pointer-events-none");
  });

  it("starts the next file with every disclosure closed again", () => {
    const { rerender } = render(<HealthFileDrawer open onClose={() => {}} metric={metric()} />);
    openSection(/Details/);
    expect(screen.getByRole("button", { name: /Details/ })).toHaveAttribute("aria-expanded", "true");
    rerender(
      <HealthFileDrawer open onClose={() => {}} metric={metric({ file_path: "src/b.py" })} />,
    );
    expect(screen.getByRole("button", { name: /Details/ })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("says the request failed and offers a retry, not 'no metric yet'", () => {
    const onRetry = vi.fn();
    render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        filePath="src/a.py"
        error={new Error("Network down")}
        onRetry={onRetry}
        metric={null}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(/Couldn't load this file/);
    expect(screen.queryByText(/No metric for this file yet/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Retry/ }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});

describe("HealthFileDrawer verdict", () => {
  it("says how bad the file is and why in one plain sentence", () => {
    render(<HealthFileDrawer open onClose={() => {}} metric={metric()} findings={five()} />);
    const verdict = within(screen.getByRole("region", { name: "Verdict" }));
    expect(verdict.getByText("At risk")).toBeInTheDocument();
    const sentence = verdict.getByText(/^Hard to change safely:/).textContent ?? "";
    expect(sentence).toBe("Hard to change safely: three functions are too complex and no test file was found for it.");
    // No detector jargon on the first screen.
    for (const word of [/biomarker/i, /CCN/, /NLOC/, /pillar/i, /capped/i, /marker/i]) {
      expect(sentence).not.toMatch(word);
    }
  });

  it("tells a healthy file so, and offers no fix", () => {
    render(<HealthFileDrawer open onClose={() => {}} metric={metric({ score: 9.6, max_ccn: 4 })} findings={[]} />);
    expect(screen.getByText("Nothing needs fixing here.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /AI prompt/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "What's wrong" })).not.toBeInTheDocument();
  });

  it("offers the points back only when the category is not held at its limit", () => {
    const findings = [finding({ id: "x", biomarker_type: "large_method", health_impact: 0.8 })];
    const breakdown = (capped: boolean) => ({
      score: 9.2,
      total_deduction: 0.8,
      categories: [
        {
          category: "size_and_complexity",
          cap: 1.5,
          raw_deduction: 0.8,
          applied_deduction: 0.8,
          capped,
          finding_count: 1,
          findings: [],
        },
      ],
    });
    const { rerender } = render(
      <HealthFileDrawer open onClose={() => {}} metric={metric({ score: 9.2 })} findings={findings} breakdown={breakdown(false)} />,
    );
    expect(screen.getByText(/Fixing the top issue is worth about/).textContent).toContain("0.8 points");
    rerender(
      <HealthFileDrawer open onClose={() => {}} metric={metric({ score: 9.2 })} findings={findings} breakdown={breakdown(true)} />,
    );
    expect(screen.queryByText(/Fixing the top issue/)).not.toBeInTheDocument();
  });

  it("keeps the AI prompt primary and the stored plan a quiet link", () => {
    const opportunity = { opportunity_id: "op1" } as unknown as RefactoringOpportunity;
    render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={metric()}
        findings={five()}
        opportunity={opportunity}
        refactoringOpportunityHref={(id) => `/plan/${id}`}
      />,
    );
    const plan = screen.getByRole("link", { name: /^Refactoring plan$/ });
    expect(plan).toHaveAttribute("href", "/plan/op1");
    // Ink, not accent: the accent is the primary action's alone (focus rings aside).
    expect(plan.className).toContain("text-[var(--color-text-secondary)]");
    expect(plan.className).not.toMatch(/(^|\s)text-\[var\(--color-accent/);
    expect(screen.getAllByRole("button", { name: "Agent prompt" })).toHaveLength(1);
  });
});

describe("HealthFileDrawer what's wrong", () => {
  it("leads with three issues, one per kind, and keeps the rest collapsed", () => {
    render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={metric()}
        findings={five()}
        breakdown={{ score: 1, total_deduction: 6.6, categories: [] }}
        signals={signals()}
      />,
    );
    const titles = whatsWrong()
      .getAllByRole("button", { expanded: false })
      .map((b) => b.textContent);
    expect(titles).toEqual([
      expect.stringContaining("Function does too much"),
      expect.stringContaining("Very long function"),
      expect.stringContaining("Too many paths through one function"),
    ]);
    for (const name of [/Other issues \(2\)/, /Details/, /History & ownership/, /Dependencies/, /Why this score/]) {
      expect(screen.getByRole("button", { name })).toHaveAttribute("aria-expanded", "false");
    }
    // Hints say what is behind a section without restating its numbers.
    expect(screen.getByRole("button", { name: /Dependencies/ })).toHaveTextContent("others depend on it");
    expect(screen.getByRole("button", { name: /Other issues/ })).toHaveTextContent("not repeating the 3 above");
  });

  it("folds repeats of one kind into a row that says how many more", () => {
    const repeats = [272, 266, 240].map((line, i) =>
      finding({ id: `r${i}`, biomarker_type: "error_handling", function_name: null, line_start: line, health_impact: 0.15 - i * 0.01 }),
    );
    render(<HealthFileDrawer open onClose={() => {}} metric={metric({ score: 9.3 })} findings={repeats} />);
    const rows = whatsWrong().getAllByRole("listitem");
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent("Errors caught too broadly");
    expect(rows[0]).toHaveTextContent("line 272");
    expect(rows[0]).toHaveTextContent("and 2 more");
  });

  it("opens a row onto its evidence and suggested fix", () => {
    render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={metric()}
        findings={[finding({ id: "a", reason: "Cyclomatic 40 across 120 lines." })]}
        suggestions={{ brain_method: "Split the parsing out of the loop." }}
      />,
    );
    expect(screen.queryByText(/Split the parsing/)).not.toBeInTheDocument();
    fireEvent.click(whatsWrong().getByRole("button", { name: /Function does too much/ }));
    expect(screen.getByText(/Cyclomatic 40 across 120 lines\./)).toBeInTheDocument();
    expect(screen.getByText(/Split the parsing out of the loop\./)).toBeInTheDocument();
  });

  it("shows a location as plain text unless the host can link to the line", () => {
    const one = [finding({ id: "a", function_name: "run", line_start: 12 })];
    const { rerender } = render(
      <HealthFileDrawer open onClose={() => {}} metric={metric()} findings={one} fileViewHref="/files/a.py" />,
    );
    expect(screen.queryByRole("link", { name: /run:12/ })).not.toBeInTheDocument();
    rerender(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={metric()}
        findings={one}
        fileViewHrefFor={(line) => `/files/a.py#L${line}`}
      />,
    );
    fireEvent.click(whatsWrong().getByRole("button", { name: /Function does too much/ }));
    expect(screen.getByRole("link", { name: /run:12/ })).toHaveAttribute("href", "/files/a.py#L12");
  });

  it("puts triage behind one menu per row and drops a resolved issue from the lead", async () => {
    const onStatus = vi.fn(async () => {});
    render(
      <HealthFileDrawer open onClose={() => {}} metric={metric()} findings={five()} onFindingStatusChange={onStatus} />,
    );
    expect(screen.queryByRole("button", { name: "Resolved" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Triage Function does too much at two:120" }));
    // A labelled group of toggle buttons, not a menu without its keyboard.
    const group = await screen.findByRole("group", { name: "Mark as" });
    expect(within(group).getByRole("button", { name: "Open" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(within(group).getByRole("button", { name: "Resolved" }));
    expect(onStatus).toHaveBeenCalledWith("b", "resolved");
    await waitFor(() => expect(whatsWrong().queryByText("Function does too much")).not.toBeInTheDocument());
  });

  it("does not repeat the performance causes under the performance lens", () => {
    const findings = [
      finding({ id: "p", biomarker_type: "io_in_loop", dimension: "performance", health_impact: 2.0 }),
      finding({ id: "s", biomarker_type: "large_method", health_impact: 0.5 }),
    ];
    const { rerender } = render(
      <HealthFileDrawer open onClose={() => {}} metric={metric()} findings={findings} />,
    );
    expect(whatsWrong().getAllByRole("listitem")).toHaveLength(2);
    rerender(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={metric()}
        findings={findings}
        lens="performance"
        performance={{ items: [], total: 0 }}
      />,
    );
    const rows = whatsWrong().getAllByRole("listitem");
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent("Very long function");
  });

  it("says how many findings a language gate held back", () => {
    render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={metric()}
        findings={[]}
        heldBack={{ php: { count: 4, reason: "PHP findings are below the precision bar." } }}
      />,
    );
    openSection(/All issues \(0\)/);
    expect(screen.getByText(/held back/).textContent).toContain(
      "4 more findings are held back: PHP findings are below the precision bar.",
    );
  });
});

describe("HealthFileDrawer vital signs", () => {
  it("states the healthy-or-not picture as a dot and a word per fact", () => {
    render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={metric({ max_ccn: 40, line_coverage_pct: 12, has_test_file: true })}
        signals={signals()}
      />,
    );
    const vitals = within(screen.getByRole("list", { name: "Vital signs" }));
    const read = (label: string) => vitals.getByText(label).nextElementSibling?.textContent;
    expect(read("Complexity")).toBe("high");
    expect(read("Test file")).toBe("found");
    expect(read("Coverage")).toBe("12%");
    expect(read("Changes often")).toBe("yes");
    expect(read("Bug fixes")).toBe("a few");
  });

  it("drops code facts for a file health does not analyse", () => {
    render(<HealthFileDrawer open onClose={() => {}} metric={metric({ score: null, max_ccn: null })} signals={signals()} />);
    const vitals = within(screen.getByRole("list", { name: "Vital signs" }));
    expect(vitals.queryByText("Test file")).not.toBeInTheDocument();
    expect(vitals.getByText("Changes often")).toBeInTheDocument();
  });
});

describe("ScoreBreakdown without its finding lists", () => {
  it("shows categories only when the surface already lists the findings", () => {
    const categories = [
      {
        category: "structural_complexity",
        cap: 2.5,
        raw_deduction: 1,
        applied_deduction: 1,
        capped: false,
        finding_count: 1,
        findings: [
          { id: "f", biomarker_type: "brain_method", severity: "high" as const, raw_impact: 1, applied_impact: 1, function_name: "run", reason: "" },
        ],
      },
    ];
    const { rerender } = render(<ScoreBreakdown score={9} totalDeduction={1} categories={categories} />);
    expect(screen.getByText("run")).toBeInTheDocument();
    rerender(<ScoreBreakdown score={9} totalDeduction={1} categories={categories} showFindings={false} />);
    expect(screen.queryByText("run")).not.toBeInTheDocument();
    expect(screen.getByText("Structural complexity")).toBeInTheDocument();
  });
});

describe("fileVerdict", () => {
  const brain = { biomarker_type: "brain_method", function_name: "run" };

  it("says the file is history-only in plain words", () => {
    expect(fileVerdict({ score: 7, top: [], historyOnly: true }).text).toMatch(
      /score comes from change history/,
    );
  });

  it("names the problem for an unscored file that still has findings", () => {
    const v = fileVerdict({ score: null, top: [brain], historyOnly: false });
    expect(v).toEqual({ text: "Worth a look: one function is too complex.", healthy: false });
  });

  it("calls a good file clean when what is left takes nothing off", () => {
    const v = fileVerdict({
      score: 9.8,
      top: [],
      historyOnly: false,
      rest: { count: 1, impact: 0, historyImpact: 0 },
    });
    expect(v).toEqual({ text: "Nothing here takes points off its score.", healthy: true });
  });

  it("does not call a file clean while findings below still take points off", () => {
    // The performance lens moves its findings out of the lead, but they still
    // carry the deduction.
    const v = fileVerdict({
      score: 6.1,
      top: [],
      historyOnly: false,
      rest: { count: 2, impact: 1.4, historyImpact: 0 },
    });
    expect(v.healthy).toBe(false);
    expect(v.text).toBe("Its score comes from the issues listed in this panel.");
    const history = fileVerdict({
      score: 6.1,
      top: [],
      historyOnly: false,
      rest: { count: 1, impact: 1.4, historyImpact: 1.4 },
    });
    expect(history.text).toMatch(/change history/);
  });

  it("makes no claim before the findings are known", () => {
    const v = fileVerdict({ score: 3.2, top: [], historyOnly: false, findingsKnown: false });
    expect(v).toEqual({ text: null, healthy: false });
  });

  it("does not call a low score clean once its issues are resolved in-session", () => {
    const v = fileVerdict({ score: 3.2, top: [], historyOnly: false });
    expect(v.healthy).toBe(false);
    expect(v.text).not.toMatch(/Nothing needs fixing/);
  });
});

describe("HealthFileDrawer honest states", () => {
  it("keeps the prompt and makes no all-clear for a low score with nothing loaded", () => {
    render(<HealthFileDrawer open onClose={() => {}} metric={metric({ score: 3.2 })} />);
    expect(screen.queryByText(/Nothing needs fixing/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Agent prompt" })).toBeInTheDocument();
  });

  it("does not call the file clean after its last issue is resolved here", async () => {
    const onStatus = vi.fn(async () => {});
    render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={metric({ score: 3.2 })}
        findings={[finding({ id: "only", function_name: "run" })]}
        onFindingStatusChange={onStatus}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /^Triage / }));
    fireEvent.click(
      within(await screen.findByRole("group", { name: "Mark as" })).getByRole("button", { name: "Resolved" }),
    );
    await waitFor(() => expect(screen.queryByRole("region", { name: "What's wrong" })).not.toBeInTheDocument());
    expect(screen.queryByText(/Nothing needs fixing/)).not.toBeInTheDocument();
    expect(screen.getByText("No open issue on hand explains this score.")).toBeInTheDocument();
  });

  it("counts every place a row covers in the points it offers back", () => {
    const findings = [0.3, 0.2].map((impact, i) =>
      finding({ id: `l${i}`, biomarker_type: "large_method", function_name: `f${i}`, health_impact: impact }),
    );
    render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={metric({ score: 9.5 })}
        findings={findings}
        breakdown={{
          score: 9.5,
          total_deduction: 0.5,
          categories: [
            { category: "size_and_complexity", cap: 1.5, raw_deduction: 0.5, applied_deduction: 0.5, capped: false, finding_count: 2, findings: [] },
          ],
        }}
      />,
    );
    expect(screen.getByText(/Fixing the top issue/).textContent).toBe(
      "Fixing the top issue in all 2 places is worth about 0.5 points.",
    );
  });

  it("points aria-controls only at a body that is mounted", () => {
    render(<HealthFileDrawer open onClose={() => {}} metric={metric()} findings={five()} />);
    const row = whatsWrong().getAllByRole("button", { expanded: false })[0]!;
    expect(row).not.toHaveAttribute("aria-controls");
    fireEvent.click(row);
    const id = row.getAttribute("aria-controls");
    expect(id).toBeTruthy();
    expect(document.getElementById(id!)).not.toBeNull();
  });
});
