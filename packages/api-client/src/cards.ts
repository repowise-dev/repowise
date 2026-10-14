/**
 * Evidence cards: the schema re-exported, plus a dependency-free validator any
 * renderer can run on a payload before trusting it.
 *
 * Validation is strict: unknown fields are rejected, so a card can never carry
 * data the schema does not name (a person field, a renderer hint) and a valid
 * card round-trips through JSON unchanged. Only own keys count, so a prototype
 * can neither supply a required field nor smuggle in an inherited one.
 */

import {
  CARD_BASIS_KINDS,
  CARD_CONFIDENCE_LEVELS,
  CARD_REF_TYPES,
  EVIDENCE_CARD_KINDS,
  EVIDENCE_CARD_SCHEMA_VERSION,
  type CardBasis,
  type CardEvidenceRow,
  type CardFact,
  type CardLink,
  type CardRef,
  type EvidenceCard,
  type EvidenceCardKind,
  type SuspectsCard,
} from "@repowise-dev/types/cards";

export * from "@repowise-dev/types/cards";

export type CardValidation =
  | { ok: true; card: EvidenceCard }
  | { ok: false; errors: string[] };

/** Most cards one message renders; a longer list is cut, not rejected. */
export const MAX_CARDS_PER_MESSAGE = 50;
/** Most facts, evidence rows, links or refs one list in a card may hold. */
export const MAX_CARD_LIST_LENGTH = 100;

type Rec = Record<string, unknown>;
type Check = (value: unknown, path: string, errors: string[]) => void;

type OptionalKeys<T> = { [K in keyof T]-?: object extends Pick<T, K> ? K : never }[keyof T];
type RequiredKeys<T> = Exclude<keyof T, OptionalKeys<T>>;
/**
 * One check per field of `T`, split by whether `T` requires it. A field added
 * to a card type without a check here fails to compile.
 */
export interface CardSpec<T> {
  required: { [K in RequiredKeys<T>]: Check };
  optional: { [K in OptionalKeys<T>]: Check };
}

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
const ISO_8601 = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$/;
/** Date.parse rolls impossible dates over (Feb 30 becomes Mar 2), so the fields are compared back. */
function isIsoTimestamp(v: string): boolean {
  const m = ISO_8601.exec(v);
  if (!m) return false;
  const [y, mo, d, h, mi, sec] = m.slice(1, 7).map((x) => Number(x ?? 0));
  const t = new Date(Date.UTC(y!, mo! - 1, d!, h!, mi!, sec!));
  return (
    t.getUTCFullYear() === y &&
    t.getUTCMonth() === mo! - 1 &&
    t.getUTCDate() === d &&
    t.getUTCHours() === h &&
    t.getUTCMinutes() === mi &&
    t.getUTCSeconds() === sec
  );
}
const timestamp: Check = (v, p, e) => {
  if (typeof v !== "string" || !isIsoTimestamp(v)) {
    e.push(`${p}: expected an ISO 8601 timestamp with a time zone`);
  }
};

/** Checks an object's own fields; any other own key is an error. */
function obj<T>(spec: CardSpec<T>, refine?: (v: Rec, p: string, e: string[]) => void): Check {
  const required = Object.entries(spec.required) as Array<[string, Check]>;
  const optional = Object.entries(spec.optional) as Array<[string, Check]>;
  const known = new Set([...required, ...optional].map(([key]) => key));
  return (v, p, e) => {
    if (!isRec(v)) {
      e.push(`${p}: expected an object`);
      return;
    }
    for (const [key, check] of required) {
      if (!Object.hasOwn(v, key)) e.push(`${p}.${key}: required`);
      else check(v[key], `${p}.${key}`, e);
    }
    for (const [key, check] of optional) {
      // Present means checked, so an optional key set to undefined is rejected.
      if (Object.hasOwn(v, key)) check(v[key], `${p}.${key}`, e);
    }
    for (const key of Object.keys(v)) {
      if (!known.has(key)) e.push(`${p}.${key}: unknown field`);
    }
    refine?.(v, p, e);
  };
}

const list =
  (item: Check): Check =>
  (v, p, e) => {
    if (!Array.isArray(v)) {
      e.push(`${p}: expected an array`);
      return;
    }
    if (v.length > MAX_CARD_LIST_LENGTH) {
      e.push(`${p}: more than ${MAX_CARD_LIST_LENGTH} items`);
      return;
    }
    v.forEach((x, i) => item(x, `${p}[${i}]`, e));
  };

const none = {} as const;

const basis = obj<CardBasis>({
  required: { kind: oneOf(CARD_BASIS_KINDS), source: str },
  optional: none,
});

const ref = obj<CardRef>(
  {
    required: { type: oneOf(CARD_REF_TYPES), id: str },
    optional: { start_line: line, end_line: line },
  },
  (r, p, e) => {
    const hasStart = Object.hasOwn(r, "start_line");
    const hasEnd = Object.hasOwn(r, "end_line");
    if ((hasStart || hasEnd) && r.type !== "file" && r.type !== "symbol") {
      e.push(`${p}: line numbers only apply to file and symbol refs`);
    }
    if (hasEnd && !hasStart) e.push(`${p}.end_line: requires start_line`);
    if (hasStart && hasEnd && (r.end_line as number) < (r.start_line as number)) {
      e.push(`${p}.end_line: before start_line`);
    }
  },
);

type CardOf<K extends EvidenceCardKind> = Extract<EvidenceCard, { kind: K }>;
type CardBase = Omit<CardOf<"answer">, "question" | "kind"> & { kind: EvidenceCardKind };

const BASE: CardSpec<CardBase> = {
  required: {
    schema_version: (v, p, e) => {
      if (v !== EVIDENCE_CARD_SCHEMA_VERSION) {
        e.push(`${p}: expected ${EVIDENCE_CARD_SCHEMA_VERSION}`);
      }
    },
    kind: oneOf(EVIDENCE_CARD_KINDS),
    title: str,
    facts: list(obj<CardFact>({ required: { label: str, value, basis }, optional: { ref } })),
    evidence: list(
      obj<CardEvidenceRow>({ required: { statement: str, basis }, optional: { refs: list(ref) } }),
    ),
    links: list(obj<CardLink>({ required: { label: str, ref }, optional: none })),
    generated_at: timestamp,
  },
  optional: {
    summary: str,
    confidence: oneOf(CARD_CONFIDENCE_LEVELS),
    repo: obj<{ id: string; name: string }>({ required: { id: str, name: str }, optional: none }),
  },
};

/** A kind's spec: the base checks plus the kind's own, typed against the card. */
function kindSpec<T extends CardBase>(
  required: Omit<CardSpec<T>["required"], keyof CardBase>,
  optional: Omit<CardSpec<T>["optional"], keyof CardBase>,
): Check {
  return obj<T>({
    required: { ...BASE.required, ...required },
    optional: { ...BASE.optional, ...optional },
  } as CardSpec<T>);
}

const CARD_CHECKS: { [K in EvidenceCardKind]: Check } = {
  answer: kindSpec<CardOf<"answer">>({ question: str }, none),
  change: kindSpec<CardOf<"change">>({ change: ref }, none),
  component: kindSpec<CardOf<"component">>({ component: ref }, none),
  scope: kindSpec<CardOf<"scope">>({ request: str }, none),
  suspects: kindSpec<SuspectsCard>(
    { incident: str },
    {
      window: obj<NonNullable<SuspectsCard["window"]>>({
        required: { since: timestamp, until: timestamp },
        optional: none,
      }),
    },
  ),
  event: kindSpec<CardOf<"event">>({ event_type: str, occurred_at: timestamp }, none),
};

/** Validates one card, returning every problem found rather than the first. */
export function validateEvidenceCard(input: unknown): CardValidation {
  const errors: string[] = [];
  const kind = isRec(input) && Object.hasOwn(input, "kind") ? input.kind : undefined;
  if (typeof kind !== "string" || !(EVIDENCE_CARD_KINDS as readonly string[]).includes(kind)) {
    errors.push(`card.kind: expected one of ${EVIDENCE_CARD_KINDS.join(", ")}`);
    return { ok: false, errors };
  }
  CARD_CHECKS[kind as EvidenceCardKind](input, "card", errors);
  return errors.length ? { ok: false, errors } : { ok: true, card: input as EvidenceCard };
}

/**
 * Keeps the valid cards of an untrusted list and drops the rest. Only the first
 * `MAX_CARDS_PER_MESSAGE` items are read.
 */
export function parseEvidenceCards(input: unknown): EvidenceCard[] {
  if (!Array.isArray(input)) return [];
  return input.slice(0, MAX_CARDS_PER_MESSAGE).flatMap((item) => {
    const result = validateEvidenceCard(item);
    return result.ok ? [result.card] : [];
  });
}
