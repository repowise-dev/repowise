import { describe, expect, it } from "vitest";
import { recordPromptKinds } from "./add-decision-button";

describe("recordPromptKinds", () => {
  it("checks a background record, and never a dismissed one", () => {
    expect(recordPromptKinds({ currency: null, status: "proposed" })).toEqual(["review"]);
    expect(recordPromptKinds({ currency: null, status: "dismissed" })).toEqual([]);
  });

  it("offers change, veto and the pin verb that applies on a decision in force", () => {
    expect(recordPromptKinds({ currency: "active", status: "active" })).toEqual([
      "edit",
      "veto",
      "pin",
    ]);
    expect(
      recordPromptKinds({ currency: "needs_review", status: "active", pinned: true }),
    ).toEqual(["edit", "veto", "unpin"]);
  });

  it("offers nothing on a retired decision", () => {
    for (const currency of ["superseded", "dismissed", "stale"] as const) {
      expect(recordPromptKinds({ currency, status: "superseded" })).toEqual([]);
    }
  });
});
