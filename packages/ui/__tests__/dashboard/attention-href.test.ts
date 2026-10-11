import { describe, expect, it } from "vitest";
import { docDriftHref, getDefaultHref, type AttentionItem } from "../../src/dashboard/attention-href";

function drift(target_id?: string): AttentionItem {
  return {
    id: "drift-7",
    type: "doc_drift",
    title: "docs/a b.md",
    description: "No file matches this path.",
    severity: "medium",
    ...(target_id ? { target_id } : {}),
  };
}

describe("getDefaultHref: doc_drift", () => {
  it("lands on the drift tab filtered to the document", () => {
    expect(getDefaultHref(drift("docs/a b.md"), "/repos/r1")).toBe(
      "/repos/r1/code-health?tab=doc-drift&document=docs%2Fa%20b.md",
    );
  });

  it("falls back to the unfiltered tab without a document", () => {
    expect(getDefaultHref(drift(), "/repos/r1")).toBe("/repos/r1/code-health?tab=doc-drift");
  });
});

describe("docDriftHref", () => {
  it("encodes the document and omits it when absent", () => {
    expect(docDriftHref("/repos/r1", "docs/a&b.md")).toBe(
      "/repos/r1/code-health?tab=doc-drift&document=docs%2Fa%26b.md",
    );
    expect(docDriftHref("/repos/r1")).toBe("/repos/r1/code-health?tab=doc-drift");
  });
});
