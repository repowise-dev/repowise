/**
 * Evidence cards: a renderer-neutral, layout-free summary of one finding.
 *
 * A card is data, not a UI tree. It carries facts, evidence rows and links,
 * and every fact and evidence row says how it is known (`CardBasis`). There is
 * no layout, colour or host vocabulary here, and links are entity refs rather
 * than URLs, so each renderer (the web app, or a chat or ticket mapper outside
 * this repo) builds URLs for its own host. Cards describe changes and
 * components, never people: no field names an author, owner or reviewer.
 *
 * `schema_version` changes whenever a field is added or removed, because the
 * validator in `@repowise-dev/api-client/cards` rejects unknown fields.
 */

import type { CardBasis, CardRef } from "./evidence.js";

export {
  CARD_BASIS_KINDS,
  CARD_REF_TYPES,
  type CardBasis,
  type CardBasisKind,
  type CardRef,
  type CardRefType,
} from "./evidence.js";

export const EVIDENCE_CARD_SCHEMA_VERSION = 1 as const;

export const EVIDENCE_CARD_KINDS = [
  "answer",
  "change",
  "component",
  "scope",
  "suspects",
  "event",
] as const;
export type EvidenceCardKind = (typeof EVIDENCE_CARD_KINDS)[number];

export const CARD_CONFIDENCE_LEVELS = ["high", "medium", "low"] as const;
export type CardConfidence = (typeof CARD_CONFIDENCE_LEVELS)[number];

/** One labelled value. A number is left unformatted for the renderer. */
export interface CardFact {
  label: string;
  value: string | number;
  basis: CardBasis;
  ref?: CardRef;
}

/** One checkable statement and the entities it rests on. */
export interface CardEvidenceRow {
  statement: string;
  basis: CardBasis;
  refs?: CardRef[];
}

export interface CardLink {
  label: string;
  ref: CardRef;
}

interface EvidenceCardBase<K extends EvidenceCardKind> {
  schema_version: typeof EVIDENCE_CARD_SCHEMA_VERSION;
  kind: K;
  title: string;
  /** Plain text; renderers must not need markdown to show it. */
  summary?: string;
  confidence?: CardConfidence;
  facts: CardFact[];
  evidence: CardEvidenceRow[];
  links: CardLink[];
  repo?: { id: string; name: string };
  /** ISO 8601 timestamp. */
  generated_at: string;
}

/** An answer to a question about the code. */
export interface AnswerCard extends EvidenceCardBase<"answer"> {
  question: string;
}

/** One commit or change, with the evidence a reviewer checks. */
export interface ChangeCard extends EvidenceCardBase<"change"> {
  change: CardRef;
}

/** One named component and what is known about it. */
export interface ComponentCard extends EvidenceCardBase<"component"> {
  component: CardRef;
}

/** What a described piece of work would touch. */
export interface ScopeCard extends EvidenceCardBase<"scope"> {
  request: string;
}

/** Changes that could explain an incident. Evidence rows are in rank order. */
export interface SuspectsCard extends EvidenceCardBase<"suspects"> {
  incident: string;
  window?: { since: string; until: string };
}

/** Something that happened in the repo, such as a health change on a component. */
export interface EventCard extends EvidenceCardBase<"event"> {
  event_type: string;
  /** ISO 8601 timestamp. */
  occurred_at: string;
}

export type EvidenceCard =
  | AnswerCard
  | ChangeCard
  | ComponentCard
  | ScopeCard
  | SuspectsCard
  | EventCard;
