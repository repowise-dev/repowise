import { describe, expect, it } from "vitest";
import type { ExtractionDiagnostics, UnmatchedConsumer } from "@repowise-dev/types/workspace";
import {
  inServiceConsumers,
  unmatchedOutsideService,
  unmatchedReasonsOutsideService,
} from "@repowise-dev/ui/workspace/contract-facts";
import { httpLinkCoverage, httpLinkedStat, percentOf, unmatchedHint } from "./http-link-coverage";

function consumer(reason: UnmatchedConsumer["reason"], type = "http"): UnmatchedConsumer {
  return { repo: "web", file_path: "a.ts", contract_id: "GET /x", contract_type: type, reason };
}

function diag(overrides: Partial<ExtractionDiagnostics> = {}): ExtractionDiagnostics {
  return {
    total_providers: 0,
    total_consumers: 0,
    total_links: 0,
    weak_link_count: 0,
    repo_breakdown: [
      {
        repo: "web",
        providers_by_type: {},
        consumers_by_type: { http: 300, grpc: 5 },
        provider_count: 0,
        consumer_count: 305,
        providers_by_layer: {},
        consumers_by_layer: {},
        http_consumers_unresolved: 0,
      },
      {
        repo: "api",
        providers_by_type: {},
        consumers_by_type: { http: 8 },
        provider_count: 0,
        consumer_count: 8,
        providers_by_layer: {},
        consumers_by_layer: {},
        http_consumers_unresolved: 0,
      },
    ],
    unmatched_consumers: [consumer("no_provider"), consumer("external_host")],
    unmatched_by_reason: { no_provider: 1, external_host: 1 },
    orphan_providers: [],
    in_service_consumers: [consumer("internal_only"), consumer("internal_only", "grpc")],
    providers_by_layer: {},
    consumers_by_layer: {},
    http_consumers_unresolved: 50,
    http_consumer_coverage: 308 / 392,
    http_consumers_linked: 235,
    http_link_coverage: 235 / 392,
    unrecognised_call_files: { web: 34 },
    unrecognised_call_file_count: 34,
    ...overrides,
  };
}

const t = (key: string, values?: Record<string, string | number>) =>
  values ? `${key} ${JSON.stringify(values)}` : key;

describe("httpLinkCoverage", () => {
  it("splits calls the way the CLI headline does", () => {
    expect(httpLinkCoverage(diag())).toEqual({
      seen: 392,
      read: 308,
      linked: 235,
      inService: 1,
      readNotLinked: 72,
    });
  });

  it("is null on an artifact older than the linked count", () => {
    expect(httpLinkCoverage(diag({ http_consumers_linked: undefined }))).toBeNull();
    expect(httpLinkCoverage(diag({ http_consumers_linked: null }))).toBeNull();
  });

  it("is null on an artifact older than the network probe", () => {
    expect(httpLinkCoverage(diag({ unrecognised_call_file_count: null }))).toBeNull();
  });

  it("is null when no call was seen, since 0 of 0 is not 100%", () => {
    const empty = diag({
      repo_breakdown: [],
      http_consumers_unresolved: 0,
      unrecognised_call_file_count: 0,
      http_consumers_linked: 0,
    });
    expect(httpLinkCoverage(empty)).toBeNull();
  });
});

describe("in-service calls on an older artifact", () => {
  // Before the split, in-service consumers sat in the unmatched list.
  const old = diag({
    in_service_consumers: undefined,
    unmatched_consumers: [consumer("no_provider"), consumer("internal_only")],
  });

  it("are counted as in-service", () => {
    expect(inServiceConsumers(old)).toHaveLength(1);
  });

  it("are never counted as unmatched", () => {
    expect(unmatchedOutsideService(old).map((u) => u.reason)).toEqual(["no_provider"]);
  });

  it("are dropped from the reason counts", () => {
    expect(unmatchedReasonsOutsideService({ no_provider: 2, internal_only: 5, unlinked: 0 })).toEqual({
      no_provider: 2,
    });
  });
});

describe("httpLinkedStat", () => {
  it("reads linked over seen as a floored percent", () => {
    const stat = httpLinkedStat(t, diag());
    expect(stat?.value).toBe("59%");
    expect(stat?.sub).toBe('sub {"linked":"235","seen":"392","read":"308"}');
  });

  it("is null when the counts are missing, so the page keeps its old stat", () => {
    expect(httpLinkedStat(t, diag({ http_consumers_linked: undefined }))).toBeNull();
  });
});

describe("percentOf", () => {
  it("never rounds a near miss up to 100%", () => {
    expect(percentOf(996, 1000)).toBe("99%");
  });
});

describe("unmatchedHint", () => {
  it("names the in-service calls when there are some", () => {
    expect(unmatchedHint(t, diag())).toBe('unmatchedHint inServiceHint {"count":2}');
  });

  it("ends cleanly when there are none", () => {
    expect(unmatchedHint(t, diag({ in_service_consumers: [] }))).toBe("unmatchedHint");
  });
});
