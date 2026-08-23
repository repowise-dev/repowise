"use client";

/**
 * Display label per contract type.
 *
 * `code` was absent while being the single largest type in the corpus, so
 * every row of it fell through to the raw lowercase string.
 */
const TYPE_LABELS: Record<string, string> = {
  http: "HTTP",
  grpc: "gRPC",
  socket: "Socket",
  topic: "Topic",
  data: "Table",
  code: "Code",
};

/** The type's display name, or the raw value when a new type appears. */
export function contractTypeLabel(type: string): string {
  return TYPE_LABELS[type] ?? type;
}

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
