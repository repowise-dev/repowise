import * as React from "react";
import type { FileDetailResponse } from "@repowise-dev/types/files";
import { formatLOC } from "../lib/format";

/**
 * One thing worth knowing about a file, as a dot plus a word.
 *
 * The shape is `SeverityMark`'s and so is the reasoning: a tinted ground, a
 * border and coloured text are three ways of saying one thing, and four of them
 * across a header tile into stripes that outweigh the path they belong to. The
 * word stays, because colour is not the name of a state.
 *
 * The hue is deliberately **not** the health ramp. The score sits forty pixels
 * above these, painted on `bandForScore`, and rule 19 is exactly the case where
 * a second mark reaches for green/amber/red and teaches the reader a rule that
 * makes them confidently wrong about the first. Health owns the band colours
 * here; a role mark is one accent hue meaning "there is something here", and
 * the word carries which thing.
 */
export function FileMark({
  children,
  title,
}: {
  children: React.ReactNode;
  title?: string | undefined;
}) {
  return (
    <span
      className="inline-flex shrink-0 items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-accent-primary)]"
      {...(title ? { title } : {})}
    >
      <span
        className="h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--color-accent-primary)]"
        aria-hidden
      />
      {children}
    </span>
  );
}

/**
 * The header's marker row. Renders **nothing** for a file with nothing to
 * report — rule 10, the same convention `FreshnessDot` and `StubDot` follow, so
 * a quiet header is a healthy file rather than a header that failed to load.
 */
export function FileMarks({ data }: { data: FileDetailResponse }) {
  const deadLines = data.dead_code.reduce((s, f) => s + f.lines, 0);
  const freshness = data.wiki_page?.freshness_status;
  const docStale = freshness === "stale" || freshness === "outdated";

  const marks: React.ReactNode[] = [];
  if (data.graph?.is_entry_point) {
    marks.push(
      <FileMark key="entry" title="Something outside the repository calls into this file.">
        Entry point
      </FileMark>,
    );
  }
  if (data.git?.is_hotspot) {
    marks.push(
      <FileMark key="hotspot" title="High churn against high complexity, from full git history.">
        Hotspot
      </FileMark>,
    );
  }
  if (data.dead_code.length > 0) {
    marks.push(
      <FileMark key="dead" title="Symbols the dead-code pass found no reachable caller for.">
        {data.dead_code.length} unreachable · {formatLOC(deadLines)} lines
      </FileMark>,
    );
  }
  if (docStale) {
    marks.push(
      <FileMark key="doc" title="The file has changed since its documentation page was written.">
        Doc {freshness}
      </FileMark>,
    );
  }

  if (marks.length === 0) return null;
  return <div className="flex flex-wrap items-center gap-x-5 gap-y-2">{marks}</div>;
}
