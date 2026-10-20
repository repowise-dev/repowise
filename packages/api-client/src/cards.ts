/**
 * Evidence cards: the schema re-exported, plus a dependency-free validator any
 * renderer can run on a payload before trusting it. Validation is strict; see
 * `./validation` for the shared rules.
 */

import {
  CARD_CONFIDENCE_LEVELS,
  EVIDENCE_CARD_KINDS,
  EVIDENCE_CARD_SCHEMA_VERSION,
  type CardEvidenceRow,
  type CardFact,
  type CardLink,
  type EvidenceCard,
  type EvidenceCardKind,
  type SuspectsCard,
} from "@repowise-dev/types/cards";

import {
  basis,
  constant,
  isRec,
  list,
  none,
  obj,
  oneOf,
  ref,
  str,
  timestamp,
  value,
  type Check,
  type Spec,
} from "./validation";

export * from "@repowise-dev/types/cards";

export type CardValidation =
  | { ok: true; card: EvidenceCard }
  | { ok: false; errors: string[] };

/** Most cards one message renders; a longer list is cut, not rejected. */
export const MAX_CARDS_PER_MESSAGE = 50;
/** Most facts, evidence rows, links or refs one list in a card may hold. */
export const MAX_CARD_LIST_LENGTH = 100;

/** Public name of the shared `Spec<T>`, kept for existing imports. */
export type CardSpec<T> = Spec<T>;

type CardOf<K extends EvidenceCardKind> = Extract<EvidenceCard, { kind: K }>;
type CardBase = Omit<CardOf<"answer">, "question" | "kind"> & { kind: EvidenceCardKind };

const BASE: Spec<CardBase> = {
  required: {
    schema_version: constant(EVIDENCE_CARD_SCHEMA_VERSION),
    kind: oneOf(EVIDENCE_CARD_KINDS),
    title: str,
    facts: list(
      obj<CardFact>({ required: { label: str, value, basis }, optional: { ref } }),
      MAX_CARD_LIST_LENGTH,
    ),
    evidence: list(
      obj<CardEvidenceRow>({
        required: { statement: str, basis },
        optional: { refs: list(ref, MAX_CARD_LIST_LENGTH) },
      }),
      MAX_CARD_LIST_LENGTH,
    ),
    links: list(obj<CardLink>({ required: { label: str, ref }, optional: none }), MAX_CARD_LIST_LENGTH),
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
  required: Omit<Spec<T>["required"], keyof CardBase>,
  optional: Omit<Spec<T>["optional"], keyof CardBase>,
): Check {
  return obj<T>({
    required: { ...BASE.required, ...required },
    optional: { ...BASE.optional, ...optional },
  } as Spec<T>);
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
