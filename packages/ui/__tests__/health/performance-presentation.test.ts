import { describe, expect, it } from "vitest";
import { PERFORMANCE_HOME_BIOMARKERS } from "../../src/health/biomarker-glossary";

import {
  affectedSummary,
  agentHandoffCall,
  boundaryLabel,
  facetValueLabel,
  opportunityEvidenceLine,
  opportunityTitle,
  planPresentation,
  whyRankedLabel,
} from "../../src/health/performance/presentation";
import { contiguousSections } from "../../src/health/performance/queue";
import { opportunity } from "./fixtures/performance";

describe("performance presentation", () => {
  it("titles a cause in words and keeps the sink out of the title", () => {
    const title = opportunityTitle(opportunity());
    expect(title).toBe("Database call inside a loop");
    expect(title).not.toContain("::");
  });

  it("gives every performance marker a non-empty title", () => {
    for (const marker of PERFORMANCE_HOME_BIOMARKERS) {
      const title = opportunityTitle(opportunity({ biomarker_type: marker, boundary_kind: null }));
      expect(title.length, marker).toBeGreaterThan(0);
      expect(title[0], marker).toBe(title[0]!.toUpperCase());
    }
  });

  it("names an absent boundary rather than leaving it blank", () => {
    expect(boundaryLabel(null)).toBe("In-process");
    expect(boundaryLabel("none")).toBe("In-process");
    expect(boundaryLabel("db")).toBe("Database");
    expect(boundaryLabel("something_new")).toBe("Something new");
  });

  it("falls back from sink to intervention symbol to file for the evidence line", () => {
    expect(opportunityEvidenceLine(opportunity())).toBe("src/db.py::fetch");
    expect(opportunityEvidenceLine(opportunity({ terminal_sink: null }))).toBe(
      "src/shared.py::load",
    );
    expect(
      opportunityEvidenceLine(
        opportunity({ terminal_sink: null, intervention_symbol: null }),
      ),
    ).toBe("src/shared.py::run");
    expect(
      opportunityEvidenceLine(
        opportunity({ terminal_sink: null, intervention_symbol: null, evidence: [] }),
      ),
    ).toBe("src/shared.py");
  });

  it("keeps singular and plural counts honest", () => {
    expect(affectedSummary(opportunity())).toBe("2 call sites across 2 files");
    expect(
      affectedSummary(opportunity({ affected_call_sites_total: 1, affected_files_total: 1 })),
    ).toBe("1 call site across 1 file");
  });

  it("reads a rank factor as words with its signed contribution", () => {
    expect(whyRankedLabel({ factor: "boundary_kind", value: "db", points: 4 })).toBe(
      "Boundary kind: Db (+4)",
    );
    expect(whyRankedLabel({ factor: "entry_reachability", value: true, points: 0 })).toBe(
      "Entry reachability (+0)",
    );
  });

  it("labels each facet in its own vocabulary", () => {
    expect(facetValueLabel("context", "unknown")).toBe("Unclassified");
    expect(facetValueLabel("actionability", "investigate")).toBe("Needs investigation");
    expect(facetValueLabel("confidence", "high")).toBe("High");
    expect(facetValueLabel("plan_state", "no_safe_plan")).toBe("No safe plan");
    expect(facetValueLabel("boundary", "none")).toBe("In-process");
  });

  it("separates a plan that exists from one that needs a refresh and from none", () => {
    expect(planPresentation(opportunity()).actionable).toBe(true);
    const refresh = planPresentation(
      opportunity({ plan_id: null, plan_status: "not_persisted" }),
    );
    expect(refresh.actionable).toBe(false);
    expect(refresh.label).toBe("Needs an index refresh");
    const none = planPresentation(opportunity({ plan_id: null, plan_status: "no_safe_plan" }));
    expect(none.label).toBe("No safe plan");
  });

  it("never calls a plan actionable without an id to fetch it with", () => {
    expect(planPresentation(opportunity({ plan_id: null })).actionable).toBe(false);
  });

  it("quotes the opportunity id in the agent drill-down", () => {
    expect(agentHandoffCall("perf2_abc")).toBe('get_health(opportunity_id="perf2_abc")');
  });
});

describe("queue sections", () => {
  it("splits the page into runs without moving a single row", () => {
    const items = [
      opportunity({ opportunity_id: "a", actionability_state: "plan_ready" }),
      opportunity({ opportunity_id: "b", actionability_state: "advisory" }),
      opportunity({ opportunity_id: "c", actionability_state: "advisory" }),
      opportunity({ opportunity_id: "d", actionability_state: "investigate" }),
    ];
    const sections = contiguousSections(items);
    expect(sections.map((s) => s.state)).toEqual(["plan_ready", "advisory", "investigate"]);
    expect(sections.flatMap((s) => s.items.map((i) => i.opportunity_id))).toEqual([
      "a",
      "b",
      "c",
      "d",
    ]);
  });

  it("keeps an interleaved order intact rather than regrouping it", () => {
    const items = [
      opportunity({ opportunity_id: "a", actionability_state: "advisory" }),
      opportunity({ opportunity_id: "b", actionability_state: "investigate" }),
      opportunity({ opportunity_id: "c", actionability_state: "advisory" }),
    ];
    const sections = contiguousSections(items);
    expect(sections).toHaveLength(3);
    expect(sections.flatMap((s) => s.items.map((i) => i.opportunity_id))).toEqual(["a", "b", "c"]);
  });

  it("returns nothing for an empty page", () => {
    expect(contiguousSections([])).toEqual([]);
  });
});
