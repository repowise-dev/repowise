/**
 * Evidence cards: the schema re-exported, plus a dependency-free validator any
 * renderer can run on a payload before trusting it.
 *
 * Validation is strict: unknown fields are rejected, so a card can never carry
 * data the schema does not name (a person field, a renderer hint) and a valid
 * card round-trips through JSON unchanged.
 */

import {
  CARD_BASIS_KINDS,
  CARD_CONFIDENCE_LEVELS,
  CARD_REF_TYPES,
  EVIDENCE_CARD_KINDS,
  EVIDENCE_CARD_SCHEMA_VERSION,
  type EvidenceCard,
  type EvidenceCardKind,
} from "@repowise-dev/types/cards";

export * from "@repowise-dev/types/cards";

export type CardValidation =
  | { ok: true; card: EvidenceCard }
  | { ok: false; errors: string[] };

type Rec = Record<string, unknown>;
type Check = (value: unknown, path: string, errors: string[]) => void;

const isRec = (v: unknown): v is Rec =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const str: Check = (v, p, e) => {
  if (typeof v !== "string" || v.length === 0) e.push(`${p}: expected a non-empty string`);
};
const line: Check = (v, p, e) => {
  if (!Number.isInteger(v) || (v as number) < 1) e.push(`${p}: expected a positive integer`);
};
const oneOf =
  (values: readonly string[]): Check =>
  (v, p, e) => {
    if (typeof v !== "string" || !values.includes(v)) {
      e.push(`${p}: expected one of ${values.join(", ")}`);
    }
  };
const value: Check = (v, p, e) => {
  if (!(typeof v === "string" || (typeof v === "number" && Number.isFinite(v)))) {
    e.push(`${p}: expected a string or a finite number`);
  }
};
const timestamp: Check = (v, p, e) => {
  if (typeof v !== "string" || Number.isNaN(Date.parse(v))) {
    e.push(`${p}: expected an ISO 8601 timestamp`);
  }
};

/** Checks an object's fields; a key not in `required` or `optional` is an error. */
function obj(required: Record<string, Check>, optional: Record<string, Check> = {}): Check {
  return (v, p, e) => {
    if (!isRec(v)) {
      e.push(`${p}: expected an object`);
      return;
    }
    for (const [key, check] of Object.entries(required)) {
      if (!(key in v)) e.push(`${p}.${key}: required`);
      else check(v[key], `${p}.${key}`, e);
    }
    for (const [key, check] of Object.entries(optional)) {
      if (key in v) check(v[key], `${p}.${key}`, e);
    }
    for (const key of Object.keys(v)) {
      if (!(key in required) && !(key in optional)) e.push(`${p}.${key}: unknown field`);
    }
  };
}

const list =
  (item: Check): Check =>
  (v, p, e) => {
    if (!Array.isArray(v)) {
      e.push(`${p}: expected an array`);
      return;
    }
    v.forEach((x, i) => item(x, `${p}[${i}]`, e));
  };

const basis = obj({ kind: oneOf(CARD_BASIS_KINDS), source: str });
const ref = obj({ type: oneOf(CARD_REF_TYPES), id: str }, { start_line: line, end_line: line });

const BASE_REQUIRED: Record<string, Check> = {
  schema_version: (v, p, e) => {
    if (v !== EVIDENCE_CARD_SCHEMA_VERSION) {
      e.push(`${p}: expected ${EVIDENCE_CARD_SCHEMA_VERSION}`);
    }
  },
  kind: oneOf(EVIDENCE_CARD_KINDS),
  title: str,
  facts: list(obj({ label: str, value, basis }, { ref })),
  evidence: list(obj({ statement: str, basis }, { refs: list(ref) })),
  links: list(obj({ label: str, ref })),
  generated_at: timestamp,
};
const BASE_OPTIONAL: Record<string, Check> = {
  summary: str,
  confidence: oneOf(CARD_CONFIDENCE_LEVELS),
  repo: obj({ id: str, name: str }),
};

const KIND_REQUIRED: Record<EvidenceCardKind, Record<string, Check>> = {
  answer: { question: str },
  change: { change: ref },
  component: { component: ref },
  scope: { request: str },
  suspects: { incident: str },
  event: { event_type: str, occurred_at: timestamp },
};
const KIND_OPTIONAL: Partial<Record<EvidenceCardKind, Record<string, Check>>> = {
  suspects: { window: obj({ since: timestamp, until: timestamp }) },
};

/** Validates one card, returning every problem found rather than the first. */
export function validateEvidenceCard(input: unknown): CardValidation {
  const errors: string[] = [];
  const kind = isRec(input) ? input.kind : undefined;
  if (typeof kind !== "string" || !(EVIDENCE_CARD_KINDS as readonly string[]).includes(kind)) {
    errors.push(`card.kind: expected one of ${EVIDENCE_CARD_KINDS.join(", ")}`);
    return { ok: false, errors };
  }
  const k = kind as EvidenceCardKind;
  obj(
    { ...BASE_REQUIRED, ...KIND_REQUIRED[k] },
    { ...BASE_OPTIONAL, ...KIND_OPTIONAL[k] },
  )(input, "card", errors);
  return errors.length ? { ok: false, errors } : { ok: true, card: input as EvidenceCard };
}

/** Keeps the valid cards of an untrusted list and drops the rest. */
export function parseEvidenceCards(input: unknown): EvidenceCard[] {
  if (!Array.isArray(input)) return [];
  return input.flatMap((item) => {
    const result = validateEvidenceCard(item);
    return result.ok ? [result.card] : [];
  });
}
