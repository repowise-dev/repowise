import { describe, expect, it } from "vitest";
import type {
  PerformanceFacetKey,
  PerformanceOpportunityPage,
} from "@repowise-dev/types/health";

import { capabilitiesOf } from "../../src/health/performance/capabilities";
import { toQuery, INITIAL_FILTERS, withFilter } from "../../src/health/performance/query";
import contract from "./fixtures/performance-wire-contract.json";
import { adapter, facets, legacyPage, page, resolvedDetail } from "./fixtures/performance";

/**
 * The wire contract, pinned.
 *
 * `performance-wire-contract.json` is the key set a live server produced for
 * this repository. The fixtures the view tests run against have to keep
 * matching it, so a field that is renamed or dropped on the server fails here
 * rather than silently rendering as blank in the tab.
 */

const keys = (value: object) => Object.keys(value).sort();

describe("canonical performance wire contract", () => {
  it("keeps the page envelope the server emits", () => {
    expect(keys(page())).toEqual(contract.page);
  });

  it("keeps the canonical summary and carries no retired flat counters", () => {
    expect(contract.summary).not.toContain("production_total");
    expect(contract.summary).not.toContain("without_plan_total");
    for (const field of ["status", "total", "with_plan_total", "actionability", "context"]) {
      expect(contract.summary).toContain(field);
    }
    // The fixture may omit an optional field, but must never add an unknown one.
    expect(contract.summary).toEqual(expect.arrayContaining(keys(page().summary)));
  });

  it("keeps the five facet groups, each counted per value", () => {
    expect(keys(facets())).toEqual(contract.facets);
    expect(keys(facets().context![0]!)).toEqual(contract.facet_entry);
  });

  it("keeps every opportunity field the queue reads", () => {
    // Present only when the preview is bounded, so the sampled row omits it.
    const optional = ["evidence_next_cursor"];
    expect(keys(page().items[0]!).filter((key) => !optional.includes(key))).toEqual(contract.item);
    expect(contract.item.filter((key) => optional.includes(key))).toEqual([]);
    expect(keys(page().items[0]!.facets)).toEqual(contract.item_facets);
    expect(keys(page().items[0]!.evidence[0]!)).toEqual(contract.evidence);
    expect(keys(page().items[0]!.why_ranked[0]!)).toEqual(contract.why_ranked);
    expect(keys(page().items[0]!.fix!)).toEqual(contract.fix);
  });

  it("publishes no value twice across the confidence facets", () => {
    const item = page().items[0]!;
    expect(contract.item).toContain("confidence");
    expect(contract.item_facets).toContain("actionability_confidence");
    expect(contract.fix).toContain("safety");
    // Evidence confidence, actionability confidence, and fix safety are three
    // fields in three places; none of them is a copy of another.
    expect(contract.item_facets).not.toContain("confidence");
    expect(contract.item_facets).not.toContain("safety");
    expect(item.facets.actionability_confidence).toBeDefined();
  });

  it("keeps the detail additions and the unresolved answer", () => {
    const detail = resolvedDetail() as Record<string, unknown>;
    for (const field of contract.detail_extra) expect(detail[field]).toBeDefined();
    expect(contract.unresolved_detail).toEqual([
      "detail",
      "model_state",
      "opportunity_id",
      "resolved",
    ]);
    expect(contract.model_state).toContain("refresh_required");
  });

  it("never emits a plan reference into the browser address space", () => {
    expect(contract.item).not.toContain("plan_reference");
  });
});

describe("legacy adapter compatibility", () => {
  it("detects a server without facets or a canonical summary", () => {
    const modern = capabilitiesOf(adapter(), page());
    expect(modern.serverFacets).toBe(true);
    expect(modern.canonicalContexts).toBe(true);

    const older = capabilitiesOf(adapter(), legacyPage());
    expect(older.serverFacets).toBe(false);
    expect(older.canonicalContexts).toBe(false);
  });

  it("reports a host capability from the adapter rather than a deployment flag", () => {
    const bare = capabilitiesOf(
      adapter({
        getPerformanceOpportunity: undefined,
        getPerformanceOpportunityFindings: undefined,
        getRefactoringPlan: undefined,
      }),
      page(),
    );
    expect(bare.detailById).toBe(false);
    expect(bare.pagedEvidence).toBe(false);
    expect(bare.planById).toBe(false);
  });

  it("sends the retired context pairing only against a server that needs it", () => {
    const production = withFilter(INITIAL_FILTERS, "context", "production");
    const older = capabilitiesOf(adapter(), legacyPage());
    expect(
      toQuery(production, { limit: 20, collapseContexts: !older.canonicalContexts }).context,
    ).toBe("production_tooling");
    const modern = capabilitiesOf(adapter(), page());
    expect(
      toQuery(production, { limit: 20, collapseContexts: !modern.canonicalContexts }).context,
    ).toBe("production");
  });

  it("keeps the facet key union and the emitted facet groups in step", () => {
    const emitted = contract.facets as PerformanceFacetKey[];
    const declared: PerformanceFacetKey[] = [
      "context",
      "boundary",
      "confidence",
      "actionability",
      "plan_state",
    ];
    expect([...emitted].sort()).toEqual([...declared].sort());
  });

  it("treats an absent facet value as absent, never as a zero the server sent", () => {
    const withoutUnknown: PerformanceOpportunityPage = page();
    expect(withoutUnknown.facets.context?.some((entry) => entry.value === "unknown")).toBe(false);
  });
});
