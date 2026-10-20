import { describe, expect, it } from "vitest";

import {
  EVIDENCE_CARD_KINDS,
  MAX_CARD_LIST_LENGTH,
  MAX_CARDS_PER_MESSAGE,
  parseEvidenceCards,
  validateEvidenceCard,
  type CardBasis,
  type CardSpec,
  type EvidenceCard,
} from "./cards";

const base = {
  schema_version: 1 as const,
  title: "Title",
  facts: [
    {
      label: "Files touched",
      value: 3,
      basis: { kind: "measured" as const, source: "git history" },
    },
  ],
  evidence: [
    {
      statement: "auth/session.py is imported by 14 modules",
      basis: { kind: "inferred" as const, source: "import graph" },
      refs: [{ type: "file" as const, id: "auth/session.py", start_line: 10, end_line: 42 }],
    },
  ],
  links: [{ label: "Session store", ref: { type: "symbol" as const, id: "auth/session.py::Store" } }],
  generated_at: "2026-10-14T09:30:00Z",
};

const VALID: Record<EvidenceCard["kind"], EvidenceCard> = {
  answer: {
    ...base,
    kind: "answer",
    question: "Where are sessions stored?",
    summary: "Sessions live in the Store class.",
    confidence: "high",
    repo: { id: "r1", name: "acme" },
  },
  change: { ...base, kind: "change", change: { type: "commit", id: "4c1e31f" } },
  component: {
    ...base,
    kind: "component",
    component: { type: "component", id: "payments" },
    facts: [
      {
        label: "Criticality",
        value: "high",
        basis: { kind: "declared", source: ".repowise/components.yaml" },
      },
    ],
  },
  scope: { ...base, kind: "scope", request: "Add SSO to the login page", evidence: [] },
  suspects: {
    ...base,
    kind: "suspects",
    incident: "Checkout errors after 14:00",
    window: { since: "2026-10-13T00:00:00Z", until: "2026-10-14T00:00:00Z" },
  },
  event: {
    ...base,
    kind: "event",
    event_type: "health.dropped",
    occurred_at: "2026-10-14T08:00:00Z",
    facts: [],
    links: [],
  },
};

const INVALID: Array<[string, unknown, string]> = [
  ["not an object", "card", "card.kind"],
  ["unknown kind", { ...VALID.answer, kind: "owner" }, "card.kind"],
  ["wrong schema version", { ...VALID.answer, schema_version: 2 }, "card.schema_version"],
  ["missing kind field", { ...base, kind: "change" }, "card.change: required"],
  [
    "fact without basis",
    { ...VALID.change, facts: [{ label: "Files", value: 3 }] },
    "card.facts[0].basis: required",
  ],
  [
    "evidence basis without source",
    { ...VALID.change, evidence: [{ statement: "x", basis: { kind: "measured" } }] },
    "card.evidence[0].basis.source: required",
  ],
  [
    "unknown basis kind",
    { ...VALID.change, evidence: [{ statement: "x", basis: { kind: "guessed", source: "s" } }] },
    "card.evidence[0].basis.kind",
  ],
  [
    "person field",
    { ...VALID.change, author: "someone@example.com" },
    "card.author: unknown field",
  ],
  [
    "person in a fact",
    { ...VALID.change, facts: [{ ...base.facts[0], owner: "someone" }] },
    "card.facts[0].owner: unknown field",
  ],
  [
    "link is a URL",
    { ...VALID.change, links: [{ label: "x", url: "https://example.com" }] },
    "card.links[0].ref: required",
  ],
  [
    "unknown ref type",
    { ...VALID.change, change: { type: "person", id: "x" } },
    "card.change.type",
  ],
  ["bad line", { ...VALID.change, links: [{ label: "x", ref: { type: "file", id: "a", start_line: 0 } }] }, "start_line"],
  ["non-finite number", { ...VALID.change, facts: [{ ...base.facts[0], value: Number.NaN }] }, "card.facts[0].value"],
  ["bad timestamp", { ...VALID.event, occurred_at: "yesterday" }, "card.occurred_at"],
  ["field of another kind", { ...VALID.answer, window: VALID.suspects }, "card.window: unknown field"],
  ["empty title", { ...VALID.answer, title: "" }, "card.title"],
  ["inherited name as a field", { ...VALID.answer, constructor: "x" }, "card.constructor: unknown field"],
  ["toString as a field", { ...VALID.answer, toString: "x" }, "card.toString: unknown field"],
  [
    "own __proto__",
    JSON.parse(`{"__proto__": {"x": 1}, ${JSON.stringify(VALID.answer).slice(1)}`),
    "card.__proto__: unknown field",
  ],
  [
    "required field only on the prototype",
    Object.assign(Object.create({ title: "From the prototype" }), (({ title: _t, ...rest }) => rest)(VALID.answer)),
    "card.title: required",
  ],
  ["optional key set to undefined", { ...VALID.answer, summary: undefined }, "card.summary"],
  ["date without time", { ...VALID.answer, generated_at: "2026-10-14" }, "card.generated_at"],
  ["bare number as a date", { ...VALID.answer, generated_at: "1" }, "card.generated_at"],
  ["prose date", { ...VALID.answer, generated_at: "March 5" }, "card.generated_at"],
  ["timestamp without a zone", { ...VALID.answer, generated_at: "2026-10-14T09:30:00" }, "card.generated_at"],
  ["impossible month", { ...VALID.answer, generated_at: "2026-13-14T09:30:00Z" }, "card.generated_at"],
  ["February 30", { ...VALID.answer, generated_at: "2026-02-30T09:30:00Z" }, "card.generated_at"],
  ["April 31", { ...VALID.answer, generated_at: "2026-04-31T09:30:00Z" }, "card.generated_at"],
  ["February 29 in a common year", { ...VALID.answer, generated_at: "2026-02-29T09:30:00Z" }, "card.generated_at"],
  ["hour 24", { ...VALID.answer, generated_at: "2026-10-14T24:00:00Z" }, "card.generated_at"],
  ["second 60", { ...VALID.answer, generated_at: "2026-10-14T09:30:60Z" }, "card.generated_at"],
  [
    "end before start",
    { ...VALID.change, links: [{ label: "x", ref: { type: "file", id: "a", start_line: 9, end_line: 3 } }] },
    "end_line: before start_line",
  ],
  [
    "end without start",
    { ...VALID.change, links: [{ label: "x", ref: { type: "file", id: "a", end_line: 3 } }] },
    "end_line: requires start_line",
  ],
  [
    "lines on a commit ref",
    { ...VALID.change, change: { type: "commit", id: "abc", start_line: 1 } },
    "line numbers only apply to file and symbol refs",
  ],
  [
    "too many facts",
    { ...VALID.change, facts: Array.from({ length: MAX_CARD_LIST_LENGTH + 1 }, () => base.facts[0]) },
    `card.facts: more than ${MAX_CARD_LIST_LENGTH} items`,
  ],
];

describe("evidence card conformance", () => {
  it("has a valid fixture for every kind", () => {
    expect(Object.keys(VALID).sort()).toEqual([...EVIDENCE_CARD_KINDS].sort());
  });

  it.each(Object.entries(VALID))("accepts a valid %s card", (_kind, card) => {
    expect(validateEvidenceCard(card)).toEqual({ ok: true, card });
  });

  it.each(INVALID)("rejects %s", (_name, card, error) => {
    const result = validateEvidenceCard(card);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.errors.join("\n")).toContain(error);
  });

  it.each(Object.entries(VALID))("round-trips a %s card through JSON unchanged", (_kind, card) => {
    const wire = JSON.parse(JSON.stringify(card)) as unknown;
    expect(wire).toEqual(card);
    expect(validateEvidenceCard(wire)).toEqual({ ok: true, card });
  });

  it("accepts a timestamp with an offset and fractional seconds", () => {
    expect(
      validateEvidenceCard({ ...VALID.answer, generated_at: "2026-10-14T09:30:00.123+05:30" }).ok,
    ).toBe(true);
  });

  it("accepts February 29 in a leap year", () => {
    expect(validateEvidenceCard({ ...VALID.answer, generated_at: "2028-02-29T09:30:00Z" }).ok).toBe(true);
  });

  it("reads at most the per-message cap of cards", () => {
    const many = Array.from({ length: MAX_CARDS_PER_MESSAGE + 10 }, () => VALID.answer);
    expect(parseEvidenceCards(many)).toHaveLength(MAX_CARDS_PER_MESSAGE);
  });

  it("needs a check for every field of a card type", () => {
    const check = () => {};
    // @ts-expect-error a field without a check fails to compile
    const missing: CardSpec<CardBasis> = { required: { kind: check }, optional: {} };
    // @ts-expect-error a check for a field the type lacks fails to compile
    const extra: CardSpec<CardBasis> = { required: { kind: check, source: check, owner: check }, optional: {} };
    expect([missing, extra]).toHaveLength(2);
  });

  it("keeps the valid cards of a mixed list", () => {
    expect(parseEvidenceCards([VALID.answer, { kind: "owner" }, VALID.scope])).toEqual([
      VALID.answer,
      VALID.scope,
    ]);
    expect(parseEvidenceCards(undefined)).toEqual([]);
  });
});
