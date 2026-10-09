/**
 * The contract type's display name.
 *
 * Server-safe on purpose. This lives apart from `contract-type-badge` because
 * that module is a client boundary, and a server component calling into a
 * `"use client"` module throws at render rather than at build.
 */

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
