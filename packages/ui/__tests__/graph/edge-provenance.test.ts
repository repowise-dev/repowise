/**
 * The provenance vocabulary is the one place a resolver word becomes English,
 * so what it refuses to say matters as much as what it says.
 *
 * This file pins the product decisions layered on the vocabulary: which
 * origins count as a guess, and that an unknown word degrades rather than
 * blanks.
 *
 * It is NOT what keeps the vocabulary complete, and the list below is not
 * authoritative — that chain is `satisfies Record<ResolutionOrigin, …>` in the
 * module (union to labels, exhaustive at compile time) plus
 * `tests/unit/server/test_wire_vocabulary_parity.py` (Python to union). The
 * list here is a redundant runtime check, so it can only drift by going stale,
 * never by letting an unlabelled origin through.
 */

import { describe, it, expect } from "vitest";
import {
  isNameMatch,
  originDescriptor,
  terminationCopy,
} from "../../src/graph/edge-provenance";
import type { FlowTermination, ResolutionOrigin } from "@repowise-dev/types/graph";

const ALL_ORIGINS: ResolutionOrigin[] = [
  "same_file",
  "self_scope",
  "enclosing_class",
  "receiver_same_file",
  "same_package",
  "import_scoped",
  "receiver_same_package",
  "package_alias",
  "module_alias",
  "crate_root",
  "receiver_import",
  "import_merged",
  "same_target",
  "receiver_global",
  "global_unique",
  "receiver_typed_same_file",
  "receiver_typed_same_package",
  "receiver_typed_import",
  "receiver_typed_global",
  "receiver_field_same_file",
  "receiver_field_same_package",
  "receiver_field_import",
  "receiver_field_global",
  "receiver_framework_same_file",
  "receiver_framework_same_package",
  "receiver_framework_import",
  "receiver_framework_global",
  "self_inherited",
  "enclosing_inherited",
];

const ALL_TERMINATIONS: FlowTermination[] = [
  "depth_limit",
  "callees_truncated",
  "cycle",
  "confidence_filtered",
  "excluded_target",
  "no_callees",
];

describe("originDescriptor", () => {
  it("describes every origin the indexer can stamp", () => {
    for (const origin of ALL_ORIGINS) {
      const d = originDescriptor(origin);
      expect(d, origin).not.toBeNull();
      expect(d!.label.length, origin).toBeGreaterThan(0);
      expect(d!.because.length, origin).toBeGreaterThan(0);
    }
  });

  it("reads a because clause as a sentence", () => {
    // Rendered as `Resolved because ${because}`, so a leading capital or a
    // trailing period would show up in the tooltip.
    for (const origin of ALL_ORIGINS) {
      const because = originDescriptor(origin)!.because;
      expect(because[0], origin).toBe(because[0]!.toLowerCase());
      expect(because.endsWith("."), origin).toBe(false);
    }
  });

  it("returns null for no origin, so an older index renders nothing", () => {
    expect(originDescriptor(null)).toBeNull();
    expect(originDescriptor(undefined)).toBeNull();
    expect(originDescriptor("")).toBeNull();
  });

  it("degrades an unrecognised word instead of blanking it", () => {
    // A newer indexer can stamp a word this bundle predates.
    const d = originDescriptor("some_future_strategy");
    expect(d).not.toBeNull();
    expect(d!.tier).not.toBe("name_match");
  });
});

describe("isNameMatch", () => {
  it("marks exactly the origins that rest on a name alone", () => {
    const marked = ALL_ORIGINS.filter(isNameMatch);
    expect(marked.sort()).toEqual(
      [
        "global_unique",
        "receiver_field_global",
        "receiver_framework_global",
        "receiver_global",
        "receiver_typed_global",
      ].sort(),
    );
  });

  it("does not mark an absent or unrecognised origin", () => {
    expect(isNameMatch(null)).toBe(false);
    expect(isNameMatch("some_future_strategy")).toBe(false);
  });
});

describe("terminationCopy", () => {
  it("describes every reason a walk can stop", () => {
    for (const reason of ALL_TERMINATIONS) {
      const c = terminationCopy(reason);
      expect(c, reason).not.toBeNull();
      expect(c!.sentence.endsWith("."), reason).toBe(true);
    }
  });

  it("separates a gap in our knowledge from a fact about the code", () => {
    // The whole point of the field: a depth cut is our limit, a cycle is the
    // code's shape. Rendering both as "execution ends here" is the bug.
    expect(terminationCopy("depth_limit")!.isLimit).toBe(true);
    expect(terminationCopy("no_callees")!.isLimit).toBe(true);
    expect(terminationCopy("cycle")!.isLimit).toBe(false);
  });

  it("never asserts that a symbol with no recorded calls calls nothing", () => {
    const sentence = terminationCopy("no_callees")!.sentence;
    expect(sentence).toMatch(/could not resolve/i);
  });

  it("returns null when the index carries no termination", () => {
    expect(terminationCopy(null)).toBeNull();
    expect(terminationCopy("invented_reason")).toBeNull();
  });
});
