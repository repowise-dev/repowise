/**
 * The honest label on an item the server demoted instead of dropping.
 *
 * The server sends the whole reason ("lower priority: long, but its control
 * flow is simple"); this shows it muted so a demoted row never reads as a
 * must-do. Renders nothing when the field is absent or null, which covers an
 * older server.
 */
export function LowerPriorityTag({ reason }: { reason?: string | null | undefined }) {
  const text = reason?.trim().replace(/^lower priority:\s*/i, "").trim();
  if (!text) return null;
  return (
    <span
      data-lower-priority
      className="text-xs text-[var(--color-text-tertiary)]"
      title="Listed after the rest because this can wait. It is still true."
    >
      Lower priority: {text} (listed after the rest)
    </span>
  );
}
