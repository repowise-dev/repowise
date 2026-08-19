import { SEVERITY_LABEL, type Severity } from "./tokens";

/** Dot colour per severity. Literal class strings so Tailwind's scanner keeps them. */
const SEVERITY_DOT: Record<Severity, string> = {
  critical: "bg-[var(--color-error)]",
  high: "bg-[var(--color-warning)]",
  medium: "bg-[var(--color-caution)]",
  low: "bg-[var(--color-text-tertiary)]",
};

const SEVERITY_TEXT: Record<Severity, string> = {
  critical: "text-[var(--color-error)]",
  high: "text-[var(--color-warning)]",
  medium: "text-[var(--color-caution)]",
  low: "text-[var(--color-text-tertiary)]",
};

/**
 * A finding's severity, as a dot and a word rather than a filled pill.
 *
 * `SEVERITY_CHIP` renders a tinted ground plus a border plus coloured text:
 * three ways of saying one thing, on a token that appears several times per row
 * in lists that are already dense. In a findings list that is most of the ink on
 * screen, and the grounds tile into stripes that read as more important than the
 * marker names beside them.
 *
 * The word stays. Colour alone is not the name of a state, and "critical" and
 * "high" are red and amber, which is exactly the pair that colour-blind readers
 * cannot separate. `compact` drops the word to the dot plus a screen-reader
 * label, for rows too tight to carry it.
 */
export function SeverityMark({
  severity,
  compact = false,
  className,
}: {
  severity: Severity;
  compact?: boolean;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.12em] ${
        SEVERITY_TEXT[severity]
      } ${className ?? ""}`}
    >
      <span
        className={`h-1.5 w-1.5 shrink-0 rounded-full ${SEVERITY_DOT[severity]}`}
        aria-hidden
      />
      <span className={compact ? "sr-only" : undefined}>{SEVERITY_LABEL[severity]}</span>
    </span>
  );
}
