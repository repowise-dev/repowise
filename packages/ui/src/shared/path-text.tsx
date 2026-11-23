import * as React from "react";
import { cn } from "../lib/cn";

/**
 * A repository path with a break opportunity after each separator.
 *
 * Without it a 72-character path in a narrow column breaks mid-identifier and
 * wraps to ten lines; with it, it breaks between directories and wraps to two.
 * `<wbr>` rather than a zero-width space so the path is still clean when it is
 * copied out of the page. Turns accent under a `group` link on hover.
 */
export function PathText({ path, className }: { path: string; className?: string }) {
  const segments = path.split("/");
  return (
    <span
      className={cn(
        "font-mono text-xs text-[var(--color-text-primary)] transition-colors group-hover:text-[var(--color-accent-primary)] [overflow-wrap:anywhere]",
        className,
      )}
    >
      {segments.map((seg, i) => (
        <React.Fragment key={i}>
          {i > 0 && (
            <>
              /<wbr />
            </>
          )}
          {seg}
        </React.Fragment>
      ))}
    </span>
  );
}
