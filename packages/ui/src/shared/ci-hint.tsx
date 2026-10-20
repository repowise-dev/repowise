/**
 * One line pointing an empty or quiet view at the CI gate that keeps it
 * current. Coverage, documentation drift and security each have a gate that
 * judges every pull request, and the guide covers all three.
 */

export const CI_GUIDE_URL = "https://github.com/repowise-dev/repowise/blob/main/docs/start/CI.md";

export interface CiHintProps {
  /** The gate command, e.g. `repowise coverage check`. */
  command: string;
  /** What the gate judges on each pull request, completing "checks ...". */
  checks: string;
}

export function CiHint({ command, checks }: CiHintProps) {
  return (
    <p className="text-xs leading-relaxed text-[var(--color-text-tertiary)] [text-wrap:pretty]">
      In CI, <code className="font-mono text-[var(--color-text-secondary)]">{command}</code>{" "}
      checks {checks} on every pull request.{" "}
      <a
        href={CI_GUIDE_URL}
        target="_blank"
        rel="noreferrer"
        className="underline underline-offset-2 hover:text-[var(--color-text-primary)]"
      >
        Set it up
      </a>
    </p>
  );
}
