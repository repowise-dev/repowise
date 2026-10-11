"use client";

/**
 * The contracts that need a reader's attention, from the extraction
 * diagnostics the page already holds.
 *
 * Two lists with different weights. Client calls that resolve to no provider
 * are grouped by the reason the matcher recorded, actionable reasons first and
 * open; the reasons that need nothing (a third-party host, a call inside one
 * service) start closed. Providers nothing calls are summarised per repository
 * and type rather than listed, because most of them are ordinary exported code
 * and a thousand rows would bury the few that matter; each summary row filters
 * the full list below instead.
 */

import { memo, useMemo, useState, type ElementType } from "react";
import type { OrphanProvider, UnmatchedConsumer } from "@repowise-dev/types/workspace";

import { AiPromptButton } from "../health/ai-prompt-button";
import { AiPromptModal } from "../health/ai-prompt-modal";
import type { AiPromptFlavor } from "../health/ai-prompt-builder";
import { formatNumber } from "../lib/format";
import {
  buildOrphanProvidersAiPrompt,
  buildUnmatchedConsumersAiPrompt,
  providerNoun,
} from "./contract-ai-prompt";
import { ContractIdentity, FilePathText } from "./contract-identity";
import { sortUnmatchedReasons, unmatchedReasonCopy, type OrphanGroup } from "./contract-facts";
import type { ContractRef } from "./contract-drawer";

/** Rows an open group shows before "Show all". */
const GROUP_PREVIEW = 5;
/** Summary rows shown before "Show all". */
const ORPHAN_PREVIEW = 8;

export interface ContractAttentionProps {
  unmatched: UnmatchedConsumer[];
  /** From `groupOrphanProviders`, so the host can ship a sample per group. */
  orphanGroups: OrphanGroup<OrphanProvider>[];
  /** Open one consumer in the contract drawer. */
  onSelect: (ref: ContractRef) => void;
  /** Where the full list is filtered to one repository's unused providers of one type. */
  browseHref: (repo: string, contractType: string) => string;
  /** Router link for `browseHref`; defaults to a plain anchor. */
  LinkComponent?: ElementType | undefined;
}

interface PromptState {
  title: string;
  description: string;
  build: (flavor: AiPromptFlavor) => string;
}

export const ContractAttention = memo(function ContractAttention({
  unmatched,
  orphanGroups,
  onSelect,
  browseHref,
  LinkComponent,
}: ContractAttentionProps) {
  const [prompt, setPrompt] = useState<PromptState | null>(null);

  const groups = useMemo(() => {
    const byReason = new Map<string, UnmatchedConsumer[]>();
    for (const u of unmatched) {
      const bucket = byReason.get(u.reason);
      if (bucket) bucket.push(u);
      else byReason.set(u.reason, [u]);
    }
    return sortUnmatchedReasons(
      [...byReason.entries()].map(([reason, rows]) => ({ reason, rows, count: rows.length })),
    );
  }, [unmatched]);

  const orphanTotal = orphanGroups.reduce((sum, g) => sum + g.count, 0);

  if (unmatched.length === 0 && orphanTotal === 0) {
    return (
      <p className="text-xs text-[var(--color-text-secondary)]">
        Every consumer matched a provider and every provider has a caller in this workspace.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-10">
      {unmatched.length > 0 ? (
        <div className="flex flex-col gap-4">
          <SubHeading
            title={`${formatNumber(unmatched.length)} client ${unmatched.length === 1 ? "call resolves" : "calls resolve"} to no provider`}
            body="Grouped by the reason the matcher recorded. Open a row for the call site and the next step."
          />
          <div className="flex flex-col">
            {groups.map((g) => (
              <UnmatchedGroup
                key={g.reason}
                reason={g.reason}
                rows={g.rows}
                onSelect={onSelect}
                onPrompt={() =>
                  setPrompt({
                    title: "AI prompt for these calls",
                    description: `${g.count} client ${g.count === 1 ? "call" : "calls"} recorded as ${unmatchedReasonCopy(g.reason).short}, with what to verify for each.`,
                    build: (flavor) =>
                      buildUnmatchedConsumersAiPrompt({ reason: g.reason, rows: g.rows, flavor }),
                  })
                }
              />
            ))}
          </div>
        </div>
      ) : null}

      {orphanTotal > 0 ? (
        <div className="flex flex-col gap-4">
          <SubHeading
            title={`${formatNumber(orphanTotal)} ${orphanTotal === 1 ? "provider has" : "providers have"} no caller in this workspace`}
            body="Not the same as unused: a caller may be outside the workspace, in the same service, or written in a form extraction cannot follow. Most exported code looks like this."
          />
          <OrphanTable
            groups={orphanGroups}
            browseHref={browseHref}
            LinkComponent={LinkComponent}
            onPrompt={(g) =>
              setPrompt({
                title: "AI prompt for these providers",
                description: `${g.count} ${providerNoun(g.contractType, g.count)} in ${g.repo} with no caller here, with how to tell used from unused.`,
                build: (flavor) =>
                  buildOrphanProvidersAiPrompt({
                    repo: g.repo,
                    contractType: g.contractType,
                    rows: g.rows,
                    total: g.count,
                    flavor,
                  }),
              })
            }
          />
        </div>
      ) : null}

      <AiPromptModal
        open={prompt !== null}
        onOpenChange={(open) => {
          if (!open) setPrompt(null);
        }}
        title={prompt?.title ?? "AI prompt"}
        {...(prompt ? { description: prompt.description } : {})}
        getPrompt={prompt?.build ?? null}
      />
    </div>
  );
});

function SubHeading({ title, body }: { title: string; body: string }) {
  return (
    <div className="flex flex-col gap-1">
      <h3 className="text-[15px] font-semibold text-[var(--color-text-primary)]">{title}</h3>
      <p className="max-w-[72ch] text-xs leading-relaxed text-[var(--color-text-secondary)]">
        {body}
      </p>
    </div>
  );
}

function UnmatchedGroup({
  reason,
  rows,
  onSelect,
  onPrompt,
}: {
  reason: string;
  rows: UnmatchedConsumer[];
  onSelect: (ref: ContractRef) => void;
  onPrompt: () => void;
}) {
  const copy = unmatchedReasonCopy(reason);
  // Reasons with nothing to fix start closed: their rows are information, not work.
  const [shown, setShown] = useState(copy.actionable ? Math.min(GROUP_PREVIEW, rows.length) : 0);
  const hidden = rows.length - shown;

  return (
    <section className="border-t border-[var(--color-border-default)] py-4">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1">
          <h4 className="flex items-baseline gap-2 text-xs font-medium text-[var(--color-text-primary)]">
            {copy.title}
            <span className="font-mono tabular-nums text-[var(--color-text-tertiary)]">
              {formatNumber(rows.length)}
            </span>
          </h4>
          <p className="mt-0.5 max-w-[72ch] text-xs leading-relaxed text-[var(--color-text-secondary)]">
            {copy.meaning}
          </p>
        </div>
        <AiPromptButton onClick={onPrompt} label="AI prompt" />
      </div>

      {shown > 0 ? (
        <ul className="m-0 mt-3 flex list-none flex-col p-0">
          {rows.slice(0, shown).map((u) => (
            <li key={`${u.repo}|${u.file_path}|${u.contract_id}`}>
              <button
                type="button"
                onClick={() =>
                  onSelect({ repo: u.repo, file_path: u.file_path, contract_id: u.contract_id })
                }
                className="grid w-full grid-cols-1 gap-x-4 gap-y-0.5 rounded-md px-2 py-1.5 text-left hover:bg-[var(--color-bg-elevated)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--color-accent-primary)] md:grid-cols-[minmax(0,5fr)_minmax(0,1fr)_minmax(0,5fr)]"
              >
                <ContractIdentity contractId={u.contract_id} />
                <span className="text-xs text-[var(--color-text-secondary)]">{u.repo}</span>
                <FilePathText path={u.file_path} />
              </button>
            </li>
          ))}
        </ul>
      ) : null}

      {hidden > 0 || shown > GROUP_PREVIEW ? (
        <div className="mt-2 flex gap-4">
          {hidden > 0 ? (
            <button
              type="button"
              onClick={() => setShown(rows.length)}
              className="text-xs font-medium text-[var(--color-accent-primary)] hover:underline"
            >
              {shown === 0
                ? `Show the ${formatNumber(rows.length)} ${rows.length === 1 ? "call" : "calls"}`
                : `Show all ${formatNumber(rows.length)}`}
            </button>
          ) : null}
          {shown > GROUP_PREVIEW ? (
            <button
              type="button"
              onClick={() => setShown(GROUP_PREVIEW)}
              className="text-xs font-medium text-[var(--color-accent-primary)] hover:underline"
            >
              Show fewer
            </button>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function OrphanTable({
  groups,
  browseHref,
  LinkComponent,
  onPrompt,
}: {
  groups: OrphanGroup<OrphanProvider>[];
  browseHref: (repo: string, contractType: string) => string;
  LinkComponent?: ElementType | undefined;
  onPrompt: (group: OrphanGroup<OrphanProvider>) => void;
}) {
  const [all, setAll] = useState(false);
  const shown = all ? groups : groups.slice(0, ORPHAN_PREVIEW);
  const A = LinkComponent ?? "a";

  return (
    <div className="flex flex-col">
      <table className="w-full border-collapse text-left">
        <caption className="sr-only">Providers with no caller, per repository and type</caption>
        <thead>
          <tr className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
            <th className="py-2 pr-3 font-normal">Repository</th>
            <th className="py-2 pr-3 font-normal">Kind</th>
            <th className="py-2 pr-3 text-right font-normal">No caller</th>
            <th className="py-2 font-normal">
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {shown.map((g) => (
            <tr key={`${g.repo}|${g.contractType}`} className="border-t border-[var(--color-border-default)]">
              <td className="py-2 pr-3 text-xs font-medium text-[var(--color-text-primary)]">
                {g.repo}
              </td>
              <td className="py-2 pr-3 text-xs text-[var(--color-text-secondary)]">
                {providerNoun(g.contractType, 2)}
              </td>
              <td className="py-2 pr-3 text-right font-mono text-xs tabular-nums text-[var(--color-text-secondary)]">
                {formatNumber(g.count)}
              </td>
              <td className="py-2">
                <div className="flex items-center justify-end gap-3">
                  <A
                    href={browseHref(g.repo, g.contractType)}
                    className="text-xs font-medium text-[var(--color-accent-primary)] hover:underline"
                  >
                    Browse
                  </A>
                  <AiPromptButton
                    variant="icon"
                    onClick={() => onPrompt(g)}
                    label={`AI prompt for ${g.repo} ${providerNoun(g.contractType, 2)} with no caller`}
                  />
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {groups.length > ORPHAN_PREVIEW ? (
        <button
          type="button"
          onClick={() => setAll((v) => !v)}
          className="mt-2 self-start text-xs font-medium text-[var(--color-accent-primary)] hover:underline"
        >
          {all ? "Show fewer" : `Show all ${formatNumber(groups.length)} groups`}
        </button>
      ) : null}
    </div>
  );
}
