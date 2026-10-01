"use client";

/**
 * Fix first: the ranked list of what to fix in this repository, as core built
 * it. The header states the scope exactly (shown of eligible, and what each
 * rule excluded); the list is the payload's order. Shared by every host: the
 * host fetches, routes and writes, this renders.
 */

import { useState, type ElementType } from "react";
import type { FixFirstQueue, FixItem, FixScope } from "@repowise-dev/types/fix-first";

import { Skeleton, SkeletonRegion } from "../../ui/skeleton";
import { ApiError } from "../../shared/api-error";
import { toFriendlyMessage } from "../../lib/errors";
import { OverviewSection } from "../../overview/section";
import { AiPromptModal } from "../ai-prompt-modal";
import { buildFixItemPrompt } from "../ai-prompts/fix-first-prompt";
import { FixFirstItem, type FixTriageStatus } from "./fix-first-item";
import { fixFirstScopeSentence, fixLocation } from "./scope";

export interface FixFirstListProps {
  queue: FixFirstQueue | undefined;
  loading?: boolean | undefined;
  error?: unknown;
  onRetry?: (() => void) | undefined;
  /** `production` leaves test files out; `all` keeps them. Omit the setter to hide the toggle. */
  scope: FixScope;
  onScopeChange?: ((scope: FixScope) => void) | undefined;
  fileHref?: ((path: string, line: number | null) => string | undefined) | undefined;
  planHref?: ((item: FixItem) => string | null) | undefined;
  onTriage?: ((item: FixItem, status: FixTriageStatus) => Promise<void>) | undefined;
  repoName?: string | undefined;
  LinkComponent?: ElementType | undefined;
  /** Drop the section's top hairline when it opens the page. */
  flush?: boolean | undefined;
}

export function FixFirstList({
  queue,
  loading = false,
  error,
  onRetry,
  scope,
  onScopeChange,
  fileHref,
  planHref,
  onTriage,
  repoName,
  LinkComponent,
  flush = true,
}: FixFirstListProps) {
  // The lead opens expanded: it is the one thing this section says to do.
  const [open, setOpen] = useState<ReadonlySet<string> | null>(null);
  const expanded = open ?? new Set(queue?.lead ? [queue.lead.id] : []);
  const toggle = (id: string) => {
    const next = new Set(expanded);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setOpen(next);
  };
  const [promptFor, setPromptFor] = useState<FixItem | null>(null);

  const toggleTests = onScopeChange ? (
    <label className="inline-flex cursor-pointer items-center gap-1.5 text-xs text-[var(--color-text-secondary)]">
      <input
        type="checkbox"
        checked={scope === "all"}
        onChange={(e) => onScopeChange(e.target.checked ? "all" : "production")}
        className="h-3.5 w-3.5 accent-[var(--color-accent-primary)]"
      />
      Include tests
    </label>
  ) : undefined;

  const description = queue
    ? `${fixFirstScopeSentence(queue)}${scope === "all" ? " Test files are included." : ""}`
    : undefined;

  return (
    <OverviewSection
      title="Fix first"
      flush={flush}
      action={toggleTests}
      {...(description ? { description } : {})}
    >
      {error ? (
        <ApiError
          title="Couldn't load Fix first"
          message={toFriendlyMessage(error)}
          {...(onRetry ? { onRetry } : {})}
        />
      ) : loading && !queue ? (
        <SkeletonRegion className="flex flex-col gap-3" label="Loading Fix first">
          <Skeleton className="h-4 w-80 max-w-full" />
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </SkeletonRegion>
      ) : queue && queue.items.length === 0 ? (
        <p className="text-sm text-[var(--color-text-secondary)]">
          Nothing eligible to fix first. Every candidate was excluded by a rule listed above, or the
          index has no health analysis yet.
        </p>
      ) : queue ? (
        <ol className="flex flex-col divide-y divide-[var(--color-border-default)]">
          {queue.items.map((item) => (
            <FixFirstItem
              key={item.id}
              item={item}
              expanded={expanded.has(item.id)}
              onToggle={() => toggle(item.id)}
              fileHref={fileHref}
              planHref={planHref}
              onAiPrompt={setPromptFor}
              onTriage={onTriage}
              LinkComponent={LinkComponent}
            />
          ))}
        </ol>
      ) : null}

      <AiPromptModal
        open={promptFor !== null}
        onOpenChange={(next) => {
          if (!next) setPromptFor(null);
        }}
        getPrompt={
          promptFor
            ? (flavor) =>
                buildFixItemPrompt({
                  item: promptFor,
                  flavor,
                  ...(repoName ? { repoName } : {}),
                })
            : null
        }
        filePath={promptFor ? fixLocation(promptFor) : null}
        title="Prompt for an agent"
        description="The change, why it ranks first, the steps in order, and how to verify it."
      />
    </OverviewSection>
  );
}
