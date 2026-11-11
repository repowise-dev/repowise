import { describe, expect, it } from "vitest";
import { createTranslator } from "next-intl";
import type { ChangeRecord, EvidenceItem } from "@repowise-dev/types/changes";
import { type ChangesTranslator, changePromptInput, filePromptInput, healthDeltaLabels, splitEvidence } from "./changes";
import en from "../../messages/en.json";
import zhCN from "../../messages/zh-CN.json";

function ev(kind: string, status: EvidenceItem["status"], over: Partial<EvidenceItem> = {}): EvidenceItem {
  return { kind, label: kind, status, basis: "inferred", value: null, details: [], source: "s", ...over };
}

describe("splitEvidence", () => {
  it("lists a revert with the exceptions so the list's attention mark has a row", () => {
    const view = splitEvidence([ev("outcomes", "attention"), ev("owners", "ok")]);
    expect(view.exceptions.map((i) => i.kind)).toEqual(["outcomes"]);
    expect(view.outcomes?.kind).toBe("outcomes");
  });

  it("leaves health, outcomes and provenance to their own places and lists exceptions first", () => {
    const view = splitEvidence([
      ev("owners", "ok"),
      ev("components_touched", "unknown"),
      ev("decisions_crossed", "attention"),
      ev("health_delta", "attention"),
      ev("provenance", "unknown"),
      ev("outcomes", "ok"),
    ]);
    expect(view.outcomes?.kind).toBe("outcomes");
    expect(view.exceptions.map((i) => i.kind)).toEqual(["decisions_crossed", "components_touched"]);
    expect(view.passed.map((i) => i.kind)).toEqual(["owners"]);
  });
});

const finding = {
  change_kind: "introduced",
  dimension: "defect",
  biomarker_type: "complex_method",
  severity: "medium",
  path: "src/a.py",
  symbol: "run",
  line_start: 10,
  line_end: 40,
  attribution_basis: "added_lines",
  reason: "run has cyclomatic complexity 9",
  sha: "c".repeat(40),
};

const change = (health: ChangeRecord["health"]) =>
  ({
    id: "pr-7",
    forge_ref: "#7",
    title: "feat: x",
    head_sha: "a".repeat(40),
    commits: ["a".repeat(40)],
    files: [{ path: "src/a.py", added: 3, deleted: 1 }],
    health,
  }) as ChangeRecord;

describe.each([
  ["en", en],
  ["zh-CN", zhCN],
])("changePromptInput in %s", (locale, messages) => {
  const t = createTranslator({ locale, messages, namespace: "views.changes" }) as unknown as ChangesTranslator;

  it("carries the rollup's worse findings and resolved count", () => {
    const input = changePromptInput(
      t,
      change({
        status: "partial",
        kind: "mixed",
        scanned_commits: 1,
        unscanned_commits: 1,
        introduced: 1,
        worsened: 0,
        resolved: 2,
        files_worsened: 1,
        findings: [finding],
      }),
    );
    expect(input).toMatchObject({ label: "#7", files: ["src/a.py"], resolved: 2, complete: false, omitted: 0 });
    expect(input.findings[0]).toMatchObject({ path: "src/a.py", sha: finding.sha });
    expect(input.attention).toEqual([]);
    const one = filePromptInput({ ...input, findings: [...input.findings, { ...finding, path: "src/b.py" }] }, "src/a.py");
    expect(one.files).toEqual(["src/a.py"]);
    expect(one.findings.map((f) => f.path)).toEqual(["src/a.py"]);
    expect(one.resolved).toBe(0);
  });

  it("counts the worse findings past the server's capped list", () => {
    const capped = changePromptInput(
      t,
      change({
        status: "available",
        kind: "worsened",
        scanned_commits: 1,
        unscanned_commits: 0,
        introduced: 60,
        worsened: 2,
        resolved: 0,
        files_worsened: 1,
        findings: [finding],
      }),
    );
    expect(capped.omitted).toBe(61);
    expect(filePromptInput(capped, "src/a.py").omitted).toBe(0);
  });

  it("names every health mark state from the catalog", () => {
    for (const word of Object.values(healthDeltaLabels(t))) expect(word).not.toMatch(/healthLabels/);
  });

  it("words the flagged rows when the change was never scanned", () => {
    const input = changePromptInput(t, change(null), [
      ev("decisions_crossed", "attention", { line: { key: "decisions_crossed", params: { accepted: 2, candidate: 0 } } }),
      ev("owners", "unknown"),
    ]);
    expect(input.findings).toEqual([]);
    expect(input.complete).toBe(true);
    expect(input.attention).toHaveLength(2);
    for (const line of input.attention) expect(line).not.toMatch(/[{}]|evidenceKinds|status(Attention|Unknown)/);
  });
});
