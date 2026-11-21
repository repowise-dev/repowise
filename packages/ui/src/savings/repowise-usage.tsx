"use client";

import * as React from "react";

import { Segmented } from "../shared/segmented";
import { formatCost, formatDate, formatNumber, formatTokens } from "../lib/format";
import type { SavingsCopy } from "./copy";
import type { SpendGrouping, SpendRowView, SpendView } from "./types";

/** Day rows shown at most; the note under the table says so when it cuts. */
export const USAGE_DAY_LIMIT = 30;

export interface RepowiseUsageProps {
  copy: SavingsCopy;
  summary: SpendView | undefined;
  rows: SpendRowView[] | undefined;
  grouping: SpendGrouping;
  onGroupingChange: (grouping: SpendGrouping) => void;
  /** Rendered in place of the figures while the host loads or after it failed. */
  fallback?: React.ReactNode;
}

/**
 * Repowise's own model spend for the same window, beside savings and never
 * subtracted from them. A call priced at zero because it ran on the user's
 * subscription or machine says so instead of printing $0.00, and a window
 * with no calls says Repowise cost nothing.
 */
export function RepowiseUsage({ copy, summary, rows, grouping, onGroupingChange, fallback }: RepowiseUsageProps) {
  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2.5 border-b border-[var(--color-border-default)] pb-3">
        <div className="min-w-0">
          <h2 className="text-[18px] font-semibold text-[var(--color-text-primary)]">{copy.usageTitle}</h2>
          <p className="mt-0.5 max-w-[80ch] text-xs text-[var(--color-text-tertiary)]">{copy.usageSub}</p>
        </div>
        {summary && summary.total_calls > 0 && !fallback && (
          <Segmented<SpendGrouping>
            label={copy.usageGroupLabel}
            value={grouping}
            onChange={onGroupingChange}
            options={(["operation", "model", "day"] as const).map((value) => ({
              value,
              label: copy.usageGroups[value],
            }))}
          />
        )}
      </div>
      {fallback ?? (summary ? <UsageBody copy={copy} summary={summary} rows={rows} grouping={grouping} /> : null)}
    </section>
  );
}

function UsageBody({
  copy,
  summary,
  rows,
  grouping,
}: {
  copy: SavingsCopy;
  summary: SpendView;
  rows: SpendRowView[] | undefined;
  grouping: SpendGrouping;
}) {
  if (summary.total_calls === 0) {
    return <p className="text-[15px] text-[var(--color-text-secondary)]">{copy.noModelCalls}</p>;
  }
  const shown = grouping === "day" ? [...(rows ?? [])].sort((a, b) => b.group.localeCompare(a.group)) : rows ?? [];
  const capped = grouping === "day" && shown.length > USAGE_DAY_LIMIT;
  const visible = capped ? shown.slice(0, USAGE_DAY_LIMIT) : shown;

  return (
    <>
      <dl className="grid grid-cols-2 gap-x-8 gap-y-4 sm:grid-cols-4">
        <Figure label={copy.spent} value={spentLabel(copy, summary)} />
        <Figure label={copy.calls} value={formatNumber(summary.total_calls)} />
        <Figure label={copy.inputTokens} value={formatTokens(summary.total_input_tokens)} />
        <Figure label={copy.outputTokens} value={formatTokens(summary.total_output_tokens)} />
      </dl>
      {rows && (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] border-collapse text-[15px]">
            <thead>
              <tr>
                {[copy.usageGroups[grouping], copy.calls, copy.inputTokens, copy.outputTokens, copy.cost].map(
                  (h, i) => (
                    <th
                      key={i}
                      className={`whitespace-nowrap border-b border-[var(--color-border-default)] pb-2.5 pt-3 font-mono text-[10px] font-medium uppercase tracking-[0.08em] text-[var(--color-text-tertiary)] ${i === 0 ? "text-left" : "pl-4 text-right"}`}
                    >
                      {h}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {visible.map((row) => (
                <tr key={row.group} className="hover:bg-[var(--color-bg-elevated)]">
                  <td className="border-b border-[var(--color-border-subtle)] py-2.5 text-left text-[var(--color-text-primary)]">
                    <GroupCell copy={copy} grouping={grouping} group={row.group} />
                  </td>
                  <Num>{formatNumber(row.calls)}</Num>
                  <Num>{formatTokens(row.input_tokens)}</Num>
                  <Num>{formatTokens(row.output_tokens)}</Num>
                  <Num>{costLabel(copy, row)}</Num>
                </tr>
              ))}
            </tbody>
            {!capped && (
              <tfoot>
                <tr className="font-medium">
                  <td className="border-t border-[var(--color-border-default)] pt-3 text-left">{copy.total}</td>
                  <Num foot>{formatNumber(summary.total_calls)}</Num>
                  <Num foot>{formatTokens(summary.total_input_tokens)}</Num>
                  <Num foot>{formatTokens(summary.total_output_tokens)}</Num>
                  <Num foot>{spentLabel(copy, summary)}</Num>
                </tr>
              </tfoot>
            )}
          </table>
        </div>
      )}
      {capped && <p className="text-xs text-[var(--color-text-tertiary)]">{copy.daysCapped(USAGE_DAY_LIMIT)}</p>}
    </>
  );
}

function GroupCell({ copy, grouping, group }: { copy: SavingsCopy; grouping: SpendGrouping; group: string }) {
  if (grouping === "model") return <span className="font-mono text-xs">{group}</span>;
  if (grouping === "day") return <>{formatDate(group)}</>;
  const label = copy.activity(group);
  return (
    <>
      {label}
      {label !== group && (
        <span className="block font-mono text-[10px] text-[var(--color-text-tertiary)]">{group}</span>
      )}
    </>
  );
}

/** Why a zero cost is zero: calls on the user's plan, on their machine, or
 *  both. Null when nothing ran unbilled, so a real $0.00 still shows. */
function unbilledLabel(copy: SavingsCopy, sub = 0, local = 0): string | null {
  if (sub > 0 && local > 0) return copy.noApiCharges;
  if (sub > 0) return copy.onSubscription;
  if (local > 0) return copy.onMachine;
  return null;
}

function costLabel(copy: SavingsCopy, row: SpendRowView): string {
  if (row.cost_usd > 0) return formatCost(row.cost_usd);
  return unbilledLabel(copy, row.subscription_calls, row.local_calls) ?? formatCost(0);
}

function spentLabel(copy: SavingsCopy, summary: SpendView): string {
  if (summary.total_cost_usd > 0) return formatCost(summary.total_cost_usd);
  return (summary.subscription_calls ?? 0) + (summary.local_calls ?? 0) > 0 ? copy.noApiCharges : formatCost(0);
}

function Figure({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <dt className="font-mono text-[10px] uppercase tracking-[0.08em] text-[var(--color-text-tertiary)]">{label}</dt>
      <dd className="m-0 text-[22px] font-medium leading-none tracking-[-0.01em] tabular-nums text-[var(--color-text-primary)]">
        {value}
      </dd>
    </div>
  );
}

function Num({ children, foot }: { children: React.ReactNode; foot?: boolean }) {
  return (
    <td
      className={`whitespace-nowrap pl-4 text-right tabular-nums ${foot ? "border-t border-[var(--color-border-default)] pt-3" : "border-b border-[var(--color-border-subtle)] py-2.5"}`}
    >
      {children}
    </td>
  );
}
