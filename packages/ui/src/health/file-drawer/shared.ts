/**
 * Constants the file drawer's sections share: the triage vocabulary, the
 * size of the lead, and the two text treatments that keep the accent off
 * everything but the primary action.
 */

/** Bucket for one-off file-level markers, kept pooled so a file with several
 *  distinct singletons still reads as one collapsed row. Not a biomarker name,
 *  and never compared against one. */
export const POOLED_FILE_KEY = "__file_level__";

/** Kinds of problem the "What's wrong" list leads with. The rest are one click away. */
export const TOP_FINDINGS = 3;

export const TRIAGE_STATUSES: { value: string; label: string }[] = [
  { value: "open", label: "Open" },
  { value: "acknowledged", label: "Acknowledged" },
  { value: "resolved", label: "Resolved" },
  { value: "false_positive", label: "False positive" },
];

export const STATUS_LABEL: Record<string, string> = Object.fromEntries(
  TRIAGE_STATUSES.map((s) => [s.value, s.label]),
);

/** Closed statuses leave "What's wrong": the reader already decided on them. */
export const CLOSED_STATUSES = new Set(["resolved", "false_positive"]);

/** A link in ink, not accent: the accent belongs to the one primary action. */
export const QUIET_LINK =
  "inline-flex w-fit items-center gap-1.5 rounded-sm text-xs font-medium text-[var(--color-text-secondary)] underline-offset-2 hover:text-[var(--color-text-primary)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]";

export const MICRO_LABEL =
  "font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]";
