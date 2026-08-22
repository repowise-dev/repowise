"use client";

import useSWR from "swr";
import type { ReachedVia, TestsReachingFile } from "@repowise-dev/types/health";

import { Skeleton } from "../ui/skeleton";

export interface TestsReachingListProps {
  filePath: string;
  /**
   * Fetches the answer for one file. Optional so a host that has not wired the
   * endpoint renders nothing rather than an error.
   */
  fetcher?: (filePath: string) => Promise<TestsReachingFile>;
  /** Namespaces the SWR key so two repos never share a cached answer. */
  cacheKey: string;
  /** Opens a test file. Omitted where there is nowhere to go. */
  onSelect?: (path: string) => void;
  /**
   * Renders the heading and the framing sentence. The file page wants them; the
   * drill-down under the chart already sits beneath a heading that says it.
   */
  heading?: boolean;
}

/**
 * The tests that reach one file, per the dependency graph.
 *
 * This is a *relationship*, and it is stated as one: "6 tests run this file",
 * naming them. It is deliberately never a score with a bar beside it. Reaching
 * has no line attribution behind it, so a number on a scale would be a coverage
 * figure the data cannot support, and the moment it becomes one we have rebuilt
 * the confusion the core spent its whole design avoiding.
 *
 * `via` is carried through rather than flattened because the two tiers are
 * different claims. A test whose calls run into the file is strong evidence; a
 * test that merely imports it is real but much cruder, and a reader deciding
 * whether this file is guarded needs to know which one they have.
 */
export function TestsReachingList({
  filePath,
  fetcher,
  cacheKey,
  onSelect,
  heading = true,
}: TestsReachingListProps) {
  const { data, isLoading } = useSWR<TestsReachingFile | null>(
    fetcher ? `tests-reaching:${cacheKey}:${filePath}` : null,
    () => fetcher!(filePath).catch(() => null),
    { revalidateOnFocus: false },
  );

  if (!fetcher) return null;
  if (isLoading) return <Skeleton className="h-16 w-full max-w-[52ch] rounded-md" />;

  // A file nothing reaches is the honest answer, not an error, and it is the
  // one the reader most needs: it is the whole left column of the chart.
  if (!data || !data.reached || data.tests.length === 0) {
    return (
      <div className="flex flex-col gap-1.5">
        {heading ? <Heading>Tests reaching this file</Heading> : null}
        <p className="max-w-[62ch] text-[13px] leading-relaxed text-[var(--color-text-secondary)] [text-wrap:pretty]">
          No test in the repository calls into this file, and none imports it.
          That is a static reading of the dependency graph, so a test that
          reaches it only through a framework hook or a name looked up at run
          time would not show up here.
        </p>
      </div>
    );
  }

  // The walk's count, not the length of what survived the cap. A file reached by
  // 124 tests ships 50 of them, and printing that 50 as the answer would be a
  // silent truncation of exactly the kind this tab exists to avoid.
  const count = data.total ?? data.tests.length;
  const shown = data.tests.length;

  return (
    <div className="flex flex-col gap-2">
      {heading ? <Heading>Tests reaching this file</Heading> : null}
      <p className="max-w-[62ch] text-[13px] leading-relaxed text-[var(--color-text-secondary)] [text-wrap:pretty]">
        <strong className="font-semibold text-[var(--color-text-primary)]">
          {count} {count === 1 ? "test file" : "test files"}
        </strong>{" "}
        {viaSentence(data.via)}
      </p>
      <ul className="flex flex-col">
        {data.tests.map((t) => (
          <li key={t} className="border-t border-[var(--color-border-default)]">
            {onSelect ? (
              <button
                type="button"
                onClick={() => onSelect(t)}
                className="w-full truncate py-1.5 text-left font-mono text-xs text-[var(--color-text-secondary)] transition-colors hover:text-[var(--color-accent-primary)]"
                title={t}
              >
                {t}
              </button>
            ) : (
              <span
                className="block truncate py-1.5 font-mono text-xs text-[var(--color-text-secondary)]"
                title={t}
              >
                {t}
              </span>
            )}
          </li>
        ))}
      </ul>
      <p className="border-t border-[var(--color-border-default)] pt-2 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
        {data.via === "call-graph" ? "call graph" : "import graph"} · no report
        needed
        {count > shown
          ? ` · listing ${shown} of ${count}, cut alphabetically`
          : ""}
      </p>
    </div>
  );
}

function Heading({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="text-[13px] font-semibold text-[var(--color-text-primary)]">
      {children}
    </h3>
  );
}

/**
 * Names the claim rather than the tier. "Reached via call-graph" is our word for
 * it; "runs into this file" is what it means to the person reading.
 */
function viaSentence(via: ReachedVia | null): string {
  if (via === "import-graph") {
    return "import this file. Nothing calls into it, so they load it without necessarily running any of it.";
  }
  return "run into this file: their calls reach code defined here, within three hops.";
}
