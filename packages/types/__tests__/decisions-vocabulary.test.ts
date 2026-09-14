/**
 * The TypeScript half of the decision-vocabulary contract.
 *
 * `tests/fixtures/decision_vocabulary.json` is generated from the Python
 * registry and checked against it by
 * `tests/unit/analysis/test_decision_vocabulary_contract.py`. This file checks the
 * TypeScript consts against the same fixture, so a source, status, currency or
 * review state added on one side fails on the other instead of drifting.
 *
 * Order is asserted, not just membership: `DECISION_STATUSES` is a ranking.
 */
import { describe, it, expect } from "vitest";
import {
  CANDIDATE_REVIEW_STATES,
  DECISION_CURRENCIES,
  DECISION_SOURCES,
  DECISION_CURRENCY_DESCRIPTIONS,
  DECISION_CURRENCY_LABELS,
  DECISION_LANES,
  DECISION_PRESETS,
  DECISION_SOURCE_LABELS,
  DECISION_STATUSES,
  DECISION_STATUS_LABELS,
  GOVERNING_CURRENCIES,
  RETIRED_DECISION_SOURCES,
  decisionAcceptanceBlockers,
  decisionSourceLabel,
  decisionStatusRank,
  isRetiredDecisionSource,
} from "../src/decisions";
import fixture from "../../../tests/fixtures/decision_vocabulary.json";

describe("decision vocabulary matches the engine", () => {
  it("names the same sources", () => {
    expect([...DECISION_SOURCES]).toEqual(fixture.sources);
  });

  it("names the same retired sources", () => {
    expect([...RETIRED_DECISION_SOURCES]).toEqual(fixture.retired_sources);
  });

  it("ranks statuses in the same order", () => {
    expect([...DECISION_STATUSES]).toEqual(fixture.statuses);
  });

  it("names the same currencies", () => {
    expect([...DECISION_CURRENCIES]).toEqual(fixture.currencies);
  });

  it("names the same candidate review states", () => {
    expect([...CANDIDATE_REVIEW_STATES]).toEqual(
      fixture.candidate_review_states,
    );
  });

  it("labels every source a row can carry", () => {
    const labelled = Object.keys(DECISION_SOURCE_LABELS).sort();
    const expected = [...fixture.sources, ...fixture.retired_sources].sort();
    expect(labelled).toEqual(expected);
  });

  it("names the same review lanes, in the same order", () => {
    expect([...DECISION_LANES]).toEqual(fixture.review_lanes);
  });

  it("names the same capture presets", () => {
    expect([...DECISION_PRESETS]).toEqual(fixture.presets);
  });

  it("labels every status", () => {
    expect(Object.keys(DECISION_STATUS_LABELS).sort()).toEqual(
      [...fixture.statuses].sort(),
    );
  });

  it("labels and explains every currency", () => {
    const expected = [...fixture.currencies].sort();
    expect(Object.keys(DECISION_CURRENCY_LABELS).sort()).toEqual(expected);
    expect(Object.keys(DECISION_CURRENCY_DESCRIPTIONS).sort()).toEqual(expected);
  });

  it("keeps every governing currency in the vocabulary", () => {
    for (const currency of GOVERNING_CURRENCIES) {
      expect(fixture.currencies).toContain(currency);
    }
  });
});

describe("lookup helpers", () => {
  it("ranks a known status by position and an unknown one last", () => {
    expect(decisionStatusRank("active")).toBe(0);
    expect(decisionStatusRank("dismissed")).toBe(DECISION_STATUSES.length - 1);
    expect(decisionStatusRank("not_a_status")).toBe(DECISION_STATUSES.length);
  });

  it("does not reach Object.prototype for an unmapped source", () => {
    expect(decisionSourceLabel("toString")).toBe("toString");
    expect(decisionSourceLabel("constructor")).toBe("constructor");
  });

  it("returns an unmapped source verbatim rather than blank", () => {
    expect(decisionSourceLabel("something_new")).toBe("something_new");
  });

  it("recognises a retired source and not a live one", () => {
    expect(isRetiredDecisionSource("readme_mining")).toBe(true);
    expect(isRetiredDecisionSource("comment")).toBe(false);
  });
});

describe("the acceptance contract, mirrored", () => {
  const accepted = {
    rationale: "sessions did not survive a restart",
    affected_files: ["src/auth/service.py"],
    evidence_commits: ["abc1234"],
    source: "pr",
  };

  it("passes a candidate carrying reason, scope and evidence", () => {
    expect(decisionAcceptanceBlockers(accepted)).toEqual([]);
  });

  it("names the missing scope in the engine's own words", () => {
    // The exact sentence the API returns, so a surface can show one string
    // whether it predicted the refusal or received it.
    expect(
      decisionAcceptanceBlockers({ ...accepted, affected_files: [] }),
    ).toEqual(["no scope: name the files or modules it governs"]);
  });

  it("treats a blank-only scope as no scope, as the engine does", () => {
    expect(
      decisionAcceptanceBlockers({ ...accepted, affected_files: ["  ", ""] }),
    ).toContain("no scope: name the files or modules it governs");
  });

  it("falls back to the decision body when there is no rationale", () => {
    expect(
      decisionAcceptanceBlockers({
        ...accepted,
        rationale: "",
        decision: "Issue signed JWTs",
      }),
    ).toEqual([]);
  });

  it("requires evidence from a mined record and not from a typed one", () => {
    const bare = { ...accepted, evidence_commits: [], evidence_file: null };
    expect(decisionAcceptanceBlockers(bare)).toEqual(["no evidence reference"]);
    expect(decisionAcceptanceBlockers({ ...bare, source: "cli" })).toEqual([]);
  });

  it("accepts an evidence file in place of a commit", () => {
    expect(
      decisionAcceptanceBlockers({
        ...accepted,
        evidence_commits: [],
        evidence_file: "docs/auth.md",
      }),
    ).toEqual([]);
  });

  it("names every gap at once rather than the first", () => {
    expect(
      decisionAcceptanceBlockers({ source: "session" }).length,
    ).toBe(3);
  });
});
