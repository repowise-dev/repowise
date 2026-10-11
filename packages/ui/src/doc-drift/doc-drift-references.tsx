"use client";

/**
 * The reverse view of doc drift, for a file page: which documents name this
 * file, at which lines, and which of those documents carry drift somewhere.
 *
 * A reference is weaker than a description. It says a document names the file,
 * not that it explains it, so every word here stays at "names". Drift on a
 * listed document is a fact about the document: a drifted reference resolves
 * to nothing, so it can never mean the passage about this file is wrong.
 */

import useSWR from "swr";
import type { DocDriftReferencesResponse } from "@repowise-dev/types/doc-drift";

import { Skeleton } from "../ui/skeleton";
import type { DocDriftAdapter } from "./doc-drift-adapter";
import { docDriftUnavailableCopy } from "./doc-drift-unavailable";
import { groupByDocument } from "./group-by-document";
import { RouterAnchor } from "./router-anchor";

export interface DocDriftReferencesProps {
  /** Repo-relative path of the file the page is about. */
  target: string;
  /** Without `listReferences` the panel renders nothing. */
  adapter: Pick<DocDriftAdapter, "cacheKey" | "listReferences" | "documentHref" | "navigate">;
  /** Link to a document's drift findings. Omitted, the drift mark is plain text. */
  driftHref?: ((document: string) => string) | undefined;
}

export function DocDriftReferences({ target, adapter, driftHref }: DocDriftReferencesProps) {
  const { documentHref, navigate } = adapter;
  const { data, isLoading } = useSWR<DocDriftReferencesResponse | null>(
    adapter.listReferences ? `doc-drift-references:${adapter.cacheKey}:${target}` : null,
    // A failed read renders nothing: this panel is a side note on a file page,
    // and an error card here would outshout the page it sits on.
    () => adapter.listReferences!(target).catch(() => null),
    { revalidateOnFocus: false },
  );

  if (isLoading) return <Skeleton className="h-16 w-full max-w-[52ch] rounded-md" />;
  if (!data) return null;

  if (data.unavailable) {
    const copy = docDriftUnavailableCopy(data.unavailable);
    return (
      <Frame>
        <Prose>{copy.title}. {copy.description}</Prose>
      </Frame>
    );
  }

  if (data.references.length === 0) {
    return (
      <Frame>
        <Prose>No document names this file. {data.references_basis}</Prose>
      </Frame>
    );
  }

  const drift = new Map(data.documents_with_drift.map((d) => [d.document, d.findings]));
  const groups = groupByDocument(data.references, (r) => r.document, (r) => r.line);

  return (
    <Frame>
      <Prose>
        <strong className="font-semibold text-[var(--color-text-primary)]">
          {data.documents} {data.documents === 1 ? "document names" : "documents name"}
        </strong>{" "}
        this file, in {data.references_total}{" "}
        {data.references_total === 1 ? "place" : "places"}. {data.references_basis}
      </Prose>
      <ul className="flex flex-col">
        {groups.map((group) => {
          const findings = drift.get(group.document);
          return (
            <li
              key={group.document}
              className="flex flex-col gap-1 border-t border-[var(--color-border-default)] py-2"
            >
              <div className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
                <RouterAnchor
                  href={documentHref(group.document)}
                  navigate={navigate}
                  title={group.document}
                  className="min-w-0 truncate font-mono text-xs text-[var(--color-text-primary)] transition-colors hover:text-[var(--color-accent-primary)]"
                >
                  {group.document}
                </RouterAnchor>
                {findings ? (
                  driftHref ? (
                    <RouterAnchor
                      href={driftHref(group.document)}
                      navigate={navigate}
                      className="shrink-0 text-xs text-[var(--color-warning)] hover:underline"
                    >
                      {driftLabel(findings)}
                    </RouterAnchor>
                  ) : (
                    <span className="shrink-0 text-xs text-[var(--color-warning)]">
                      {driftLabel(findings)}
                    </span>
                  )
                ) : null}
              </div>
              <ul className="flex flex-col gap-0.5">
                {group.items.map((r) => (
                  <li
                    key={`${r.line}:${r.kind}`}
                    className="flex min-w-0 items-baseline gap-2 text-xs"
                  >
                    <RouterAnchor
                      href={documentHref(group.document, r.line)}
                      navigate={navigate}
                      className="shrink-0 font-mono tabular-nums text-[var(--color-text-secondary)] hover:text-[var(--color-accent-primary)]"
                    >
                      line {r.line}
                    </RouterAnchor>
                    {r.section ? (
                      <span
                        className="min-w-0 truncate text-[var(--color-text-tertiary)]"
                        title={r.section}
                      >
                        {r.section}
                      </span>
                    ) : null}
                  </li>
                ))}
              </ul>
            </li>
          );
        })}
      </ul>
      {data.references_emitted < data.references_total && (
        <p className="border-t border-[var(--color-border-default)] pt-2 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
          listing {data.references_emitted} of {data.references_total} references
        </p>
      )}
    </Frame>
  );
}

/** Drift is the document's, counted over the whole document. */
function driftLabel(findings: number): string {
  return `${findings} drifted ${findings === 1 ? "assertion" : "assertions"} in this document`;
}

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-[13px] font-semibold text-[var(--color-text-primary)]">
        Documents naming this file
      </h3>
      {children}
    </section>
  );
}

function Prose({ children }: { children: React.ReactNode }) {
  return (
    <p className="max-w-[62ch] text-[13px] leading-relaxed text-[var(--color-text-secondary)] [text-wrap:pretty]">
      {children}
    </p>
  );
}
