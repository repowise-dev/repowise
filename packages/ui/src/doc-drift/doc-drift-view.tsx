"use client";

/**
 * Documentation drift view, on the section design language.
 *
 * The shape is: a lede that leads with the count of drifted assertions and
 * says in prose what the count does and does not cover, then the drill-down
 * table with its own filters in the section header that owns them.
 *
 * Three things this view must not do, each of which is an easy and specific
 * lie:
 *
 * - Report an unfilled store as a clean repository. The engine answers with a
 *   cause instead of an empty list, and that cause is rendered here rather
 *   than pre-gated by each host, so the wording cannot fork.
 * - Show a count without its basis. Most references in a real tree are
 *   uncheckable by design, so a bare figure claims a coverage this detector
 *   does not have. `findings_basis` is prose in the lede, not a footnote.
 * - Read as a list of broken code files. A finding is filed against the
 *   *document*, which is why the document leads every row.
 *
 * Presentation and orchestration only: the host injects fetching and links
 * through a {@link DocDriftAdapter}, so web and hosted render one source.
 */

import { useState } from "react";
import useSWR from "swr";
import { FileCheck2 } from "lucide-react";
import {
  DOC_DRIFT_CONFIDENCE,
  docDriftKindLabel,
  type DocDriftResponse,
} from "@repowise-dev/types/doc-drift";

import { Skeleton } from "../ui/skeleton";
import { ApiError } from "../shared/api-error";
import { EmptyState } from "../shared/empty-state";
import { OverviewSection } from "../overview/section";
import { toFriendlyMessage } from "../lib/errors";

import { DocDriftLede } from "./doc-drift-lede";
import { DriftFindingsTable } from "./drift-findings-table";
import { DocDriftUnavailableState } from "./doc-drift-unavailable";
import type { DocDriftAdapter } from "./doc-drift-adapter";

/**
 * Server ceiling for one page of findings. Reaching it means the table shows a
 * slice, and the lede says so rather than letting the slice read as the repo.
 */
const FINDINGS_LIMIT = 500;

/**
 * Reference classes for the filter, from what this repository actually has
 * rather than from the vocabulary: an option for a class with no findings is a
 * control that cannot act.
 *
 * Read off the *unfiltered* distribution, and the selected class is always
 * included. Read off the filtered response, selecting one class would leave it
 * as the only option and there would be no way back.
 */
function filterKinds(
  distribution: DocDriftResponse | undefined,
  selected: string,
): string[] {
  const keys = new Set(Object.keys(distribution?.summary?.by_kind ?? {}));
  if (selected) keys.add(selected);
  return [...keys].sort();
}

export function DocDriftView({
  adapter,
  renderError,
  renderLoading,
}: {
  adapter: DocDriftAdapter;
  /** Host-specific failure card — an auth toast, a permissions notice. */
  renderError?: (error: unknown, retry: () => void) => React.ReactNode;
  /** Host-specific skeleton, for a shell that has its own loading language. */
  renderLoading?: () => React.ReactNode;
}) {
  /** Which slice the table shows. Both narrow the summary too, server-side,
   *  so the figures and the rows always describe one population. */
  const [minConfidence, setMinConfidence] = useState<number>(
    DOC_DRIFT_CONFIDENCE.MEDIUM,
  );
  const [kind, setKind] = useState<string>("");

  const { data, isLoading, error, mutate } = useSWR<DocDriftResponse>(
    `doc-drift:${adapter.cacheKey}:${minConfidence}:${kind}`,
    () =>
      adapter.listFindings({
        min_confidence: minConfidence,
        limit: FINDINGS_LIMIT,
        ...(kind ? { kind } : {}),
      }),
    { revalidateOnFocus: false, keepPreviousData: true },
  );

  // The kind options come from the same query without the kind filter. When
  // nothing is selected that is the key above, so SWR serves both from one
  // request; only a narrowed view pays for a second.
  const { data: distribution } = useSWR<DocDriftResponse>(
    kind ? `doc-drift:${adapter.cacheKey}:${minConfidence}:` : null,
    () =>
      adapter.listFindings({
        min_confidence: minConfidence,
        limit: FINDINGS_LIMIT,
      }),
    { revalidateOnFocus: false },
  );

  const retry = () => void mutate();

  if (error && !data) {
    return (
      <>
        {renderError?.(error, retry) ?? (
          <ApiError
            title="Couldn't load documentation drift"
            message={toFriendlyMessage(error)}
            onRetry={retry}
          />
        )}
      </>
    );
  }

  if (isLoading && !data) {
    return <>{renderLoading?.() ?? <DocDriftSkeleton />}</>;
  }

  if (!data) return null;

  // The refusal outranks everything below it: with no summary there is no
  // honest figure to lead with, and an empty table here would be the exact
  // false clean the cause exists to prevent.
  if (data.unavailable) {
    // With a retry: nothing revalidates on focus, so a transient read failure
    // would otherwise strand the tab until a full page reload.
    return (
      <DocDriftUnavailableState
        reason={data.unavailable}
        titleAs="h2"
        onRetry={retry}
      />
    );
  }

  const summary = data.summary;
  if (!summary) return null;

  const truncated = data.findings_emitted < summary.findings_total;
  const kinds = filterKinds(kind ? (distribution ?? data) : data, kind);
  const filtered = minConfidence !== DOC_DRIFT_CONFIDENCE.MEDIUM || kind !== "";

  return (
    <div className="flex flex-col gap-8">
      <DocDriftLede
        summary={summary}
        shownCount={data.findings_emitted}
        truncated={truncated}
      />

      {summary.findings_total === 0 && !filtered ? (
        <EmptyState
          icon={<FileCheck2 className="h-6 w-6" />}
          title="No documentation drift found"
          description="Every reference this detector could resolve still resolves. It re-checks on each update, so this is worth a second look after a rename or a move."
        />
      ) : (
        <OverviewSection
          title="Drifted assertions"
          description="Each row names the document to edit and the line to edit it on. It does not claim the document describes the file it names, only that the file is no longer there."
          action={
            <Filters
              minConfidence={minConfidence}
              onMinConfidence={setMinConfidence}
              kind={kind}
              onKind={setKind}
              kinds={kinds}
            />
          }
        >
          {error ? (
            // Over rows we already hold: say so, but keep them.
            <ApiError
              title="Couldn't refresh findings"
              // Named, because the filters above already show the selection
              // that failed while the figures below still describe the last
              // one that loaded.
              message={`${toFriendlyMessage(error)} The findings below are from the previous filter.`}
              onRetry={retry}
            />
          ) : null}
          {truncated && (
            <p className="text-xs text-[var(--color-text-tertiary)]">
              Showing {data.findings_emitted} of {summary.findings_total} findings.
            </p>
          )}
          <DriftFindingsTable
            findings={data.findings}
            documentHref={adapter.documentHref}
            navigate={adapter.navigate}
          />
        </OverviewSection>
      )}
    </div>
  );
}

const SELECT_CLS =
  "h-8 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-2 text-xs text-[var(--color-text-secondary)]";

/**
 * Two filters, each on its own axis: how sure the finding is, and how it was
 * found. Both go to the server so the lede's figures narrow with the rows.
 */
function Filters({
  minConfidence,
  onMinConfidence,
  kind,
  onKind,
  kinds,
}: {
  minConfidence: number;
  onMinConfidence: (value: number) => void;
  kind: string;
  onKind: (value: string) => void;
  kinds: string[];
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <label className="sr-only" htmlFor="doc-drift-confidence">
        Minimum confidence
      </label>
      <select
        id="doc-drift-confidence"
        className={SELECT_CLS}
        value={String(minConfidence)}
        onChange={(e) => onMinConfidence(Number(e.target.value))}
      >
        <option value={String(DOC_DRIFT_CONFIDENCE.MEDIUM)}>All findings</option>
        <option value={String(DOC_DRIFT_CONFIDENCE.HIGH)}>Near-certain only</option>
      </select>

      {/* Rendered whenever a class is selected, so the filter can always be
          cleared even before the unfiltered distribution has arrived. */}
      {(kinds.length > 1 || kind !== "") && (
        <>
          <label className="sr-only" htmlFor="doc-drift-kind">
            Reference class
          </label>
          <select
            id="doc-drift-kind"
            className={SELECT_CLS}
            value={kind}
            onChange={(e) => onKind(e.target.value)}
          >
            <option value="">All references</option>
            {kinds.map((k) => (
              <option key={k} value={k}>
                {docDriftKindLabel(k)}
              </option>
            ))}
          </select>
        </>
      )}
    </div>
  );
}

function DocDriftSkeleton() {
  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-3">
        <Skeleton className="h-12 w-48" />
        <Skeleton className="h-16 w-full max-w-[62ch]" />
      </div>
      <Skeleton className="h-40 w-full rounded-lg" />
    </div>
  );
}
