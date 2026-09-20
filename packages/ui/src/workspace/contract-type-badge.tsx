"use client";

import { contractTypeLabel } from "./contract-type-label";

// Re-exported so client callers that already reach for the label through this
// module keep working; server components must import it from the source.
export { contractTypeLabel };

/**
 * The contract's transport, as a word.
 *
 * This was a filled pill with a tinted ground per type, which is the shape
 * `SEVERITY_CHIP` was retired for: a ground, a colour and a border spent on a
 * token that repeats once per row tile into stripes down a table and outweigh
 * the contract ids they label. Adding a sixth ground for the largest type
 * would have made the loudest column the one carrying the least, and following
 * the colour goes nowhere — the type is what the filter already selects on. A
 * quiet word says the same thing, and a new type costs nothing.
 */
export function ContractTypeBadge({ type }: { type: string }) {
  return (
    <span className="text-xs text-[var(--color-text-secondary)]">{contractTypeLabel(type)}</span>
  );
}

/**
 * Which side of the contract a row is.
 *
 * Green for provider against amber for consumer broke two rules at once. Those
 * hues are reserved for readouts that carry a health band, and neither role is
 * one. And providers outnumber consumers by better than two to one, so the
 * green was the default state wearing the colour of a verdict — a mark every
 * row carries says nothing.
 */
export function RoleBadge({ role }: { role: string }) {
  return (
    <span className="text-xs text-[var(--color-text-secondary)]">
      {role === "provider" ? "Provider" : "Consumer"}
    </span>
  );
}
