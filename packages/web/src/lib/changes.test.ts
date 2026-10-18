import { describe, expect, it } from "vitest";
import { createTranslator } from "next-intl";
import type { EvidenceItem } from "@repowise-dev/types/changes";
import { type ChangesTranslator, commitsViewHref, evidenceValueText, evidenceWord, parseCommitsView } from "./changes";
import en from "../../messages/en.json";
import zhCN from "../../messages/zh-CN.json";

/** One line per key the server's `item_line` can send, with its numbers. */
const LINES: [string, Record<string, unknown>][] = [
  ["unknown", { reason: "not gathered for this change" }],
  ["unknown", { reason: "" }],
  ["components_touched", { count: 2, names: "core, web", more: 0 }],
  ["suggested_reviewers", { count: 8, names: "a, b, c, d, e, f", more: 2 }],
  ["owners", { count: 1, names: "@core", more: 0, unowned: 3 }],
  ["owners", { count: 0, names: "", more: 0, unowned: 1 }],
  ["reaching_tests", { files: 23, reached: 22, absent: 0, unknown: 1 }],
  ["reaching_tests", { files: 0, reached: 0, absent: 0, unknown: 0 }],
  ["contracts", { touched: 0, breaking: 0 }],
  ["contracts_unchecked", { touched: 2, breaking: 0 }],
  ["cross_repo_consumers", { links: 3, names: "web", more: 0 }],
  ["decisions_crossed", { accepted: 1, candidate: 2 }],
  ["decisions_none", { accepted: 0, candidate: 0 }],
  ["health_delta", { introduced: 1, worsened: 0, resolved: 2 }],
  ["health_delta_partial", { introduced: 0, worsened: 0, resolved: 0 }],
  ["provenance", { label: "human attested", known: 3, commits: 4 }],
  ["provenance_named", { label: "agent", known: 1, commits: 1, names: "claude-code, claude-opus-4-5" }],
  ["risk_score", { percentile: 97.5 }],
  ["outcomes", { reverts: 0, references: 1 }],
  ["outcomes_rate", { reverts: 1, references: 0, fixed: 2, files: 5, days: 90 }],
];

function item(key: string, params: Record<string, unknown>): EvidenceItem {
  return {
    kind: "reaching_tests",
    label: "Tests reaching the change",
    status: "unknown",
    basis: "inferred",
    value: null,
    details: [],
    source: "test impact",
    line: { key, params },
  };
}

describe.each([
  ["en", en],
  ["zh-CN", zhCN],
])("evidence lines in %s", (locale, messages) => {
  const t = createTranslator({
    locale,
    messages,
    namespace: "views.changes",
  }) as unknown as ChangesTranslator;

  it.each(LINES)("words %s without raw keys or booleans", (key, params) => {
    const text = evidenceValueText(t, item(key, params));
    expect(text).not.toBe("");
    expect(text).not.toMatch(/[{}_]|true|false|undefined|null/);
  });

  it("labels and bases come from the catalog", () => {
    expect(evidenceWord(t, "evidenceKinds.reaching_tests", "x")).not.toBe("x");
    expect(evidenceWord(t, "evidenceBasis.unresolved", "unresolved")).not.toBe("unresolved");
    expect(evidenceWord(t, "evidenceKinds.not_a_kind", "Server label")).toBe("Server label");
  });
});

it("says how many changed files a test reaches and how many are not known", () => {
  const t = createTranslator({ locale: "en", messages: en, namespace: "views.changes" });
  const text = evidenceValueText(
    t as unknown as ChangesTranslator,
    item("reaching_tests", { files: 23, reached: 22, absent: 0, unknown: 1 }),
  );
  expect(text).toBe("22 of 23 changed files reached by a test; 1 not known");
});

describe("Commits page views", () => {
  it("reads only the known views and keeps the commit view param-free", () => {
    expect(parseCommitsView("changes")).toBe("changes");
    expect(parseCommitsView("events")).toBe("events");
    expect(parseCommitsView("review")).toBe("review");
    expect(parseCommitsView(undefined)).toBe("commits");
    expect(parseCommitsView("anything")).toBe("commits");
    expect(commitsViewHref("r1", "commits")).toBe("/repos/r1/commits");
    expect(commitsViewHref("r1", "events")).toBe("/repos/r1/commits?view=events");
    expect(commitsViewHref("r1", "review")).toBe("/repos/r1/commits?view=review");
  });
});
