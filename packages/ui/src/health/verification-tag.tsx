/**
 * The mark a provisional finding type carries wherever it is shown.
 *
 * A provisional type has not yet earned a place on default surfaces, so it
 * appears only when someone asked for it by name. The server labels those rows
 * (`verification: "unverified"`); this says so on the row instead of letting
 * the finding read like a validated one. Renders nothing for a validated type.
 */
export function VerificationTag({ verification }: { verification?: string | null | undefined }) {
  if (!verification) return null;
  return (
    <span
      className="rounded border border-[var(--color-border-default)] px-1 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-secondary)]"
      title="A provisional finding type, not yet validated against bug-fix history. Shown because it was asked for by name."
    >
      {verification}
    </span>
  );
}
