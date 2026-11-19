import * as React from "react";

import { formatCost, formatDate, formatNumber, formatPercent, formatTokens } from "../lib/format";
import { BENCHMARK_FEWER_FILES_PCT, type SavingsCopy } from "./copy";
import { filesTrackedSince, type SavingsView } from "./types";

/** Below this many, skipped file opens stay out of the lede: "skipped 2 file
 *  opens" undersells the sentence. The proof row still shows the count. */
const LEDE_MIN_FILES = 10;

const MICRO = "font-mono text-[10px] uppercase tracking-[0.08em] text-[var(--color-text-tertiary)]";

/** The faint two-corner wash on the one contained object. Derived from the
 *  accent and model tokens so it follows the theme; never a raw colour. */
const HERO_WASH: React.CSSProperties = {
  backgroundImage:
    "radial-gradient(70% 120% at 0% 0%, color-mix(in srgb, var(--color-accent-primary) 5%, transparent), transparent 65%), radial-gradient(60% 120% at 100% 100%, color-mix(in srgb, var(--color-model) 5%, transparent), transparent 65%)",
};

/**
 * The page's subject: how much less agents had to read.
 *
 * Leads with the input reduction because it holds from the first session and
 * does not depend on plan or pricing. The before/after strips sit beside it,
 * read straight off the report's baseline fields. When no call carried a
 * baseline the figure falls back to tokens saved and the strips are left out,
 * since a ratio over nothing is not a zero.
 */
export function SavingsHero({ data, copy }: { data: SavingsView; copy: SavingsCopy }) {
  const ratio = data.input_reduction_ratio ?? null;
  const base = data.baseline_input_tokens ?? 0;
  const read = Math.max(base - (data.baseline_saved_input_tokens ?? 0), 0);
  const since = data.first_event_at ? formatDate(data.first_event_at) : null;
  const files = data.files_not_opened ?? 0;
  const micro =
    data.window_days != null ? copy.heroWindow(data.window_days) : since ? copy.heroSince(since) : null;
  const showStrips = ratio !== null && base > 0;

  return (
    <div
      className="relative overflow-hidden rounded-2xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)]"
      style={HERO_WASH}
    >
      <div className="grid grid-cols-1 gap-7 px-5 py-6 lg:grid-cols-2 lg:gap-12 lg:px-9 lg:py-8">
        <div className="min-w-0">
          {micro && <div className={MICRO}>{micro}</div>}
          <p className="mt-3 flex flex-wrap items-baseline gap-x-2 text-[52px] font-light leading-none tracking-[-0.035em] tabular-nums text-[var(--color-text-primary)]">
            {ratio !== null ? formatPercent(ratio) : formatTokens(data.saved_input_tokens ?? 0)}
            <span className="text-[15px] font-normal tracking-normal text-[var(--color-text-tertiary)]">
              {ratio !== null ? copy.heroUnit : copy.heroTokensUnit}
            </span>
          </p>
          <p className="mt-3.5 max-w-[48ch] text-[15px] text-[var(--color-text-secondary)]">
            {copy.heroLede(
              formatNumber(data.successful_or_usable_partial_events ?? 0),
              formatTokens(data.saved_input_tokens ?? 0),
              files >= LEDE_MIN_FILES ? formatNumber(files) : null,
            )}
          </p>
          <p className="mt-4 text-xs text-[var(--color-text-tertiary)]">{copy.heroBasis}</p>
        </div>
        {showStrips && (
          <div className="grid min-w-0 gap-4 self-center" aria-label={`${copy.stripBase}, ${copy.stripWith}`}>
            <Strip label={copy.stripBase} value={copy.stripTokens(formatTokens(base))} share={1} base />
            <Strip
              label={copy.stripWith}
              value={copy.stripTokens(formatTokens(read))}
              share={base > 0 ? read / base : 0}
            />
            <p className="border-t border-[var(--color-border-subtle)] pt-3.5 text-xs text-[var(--color-text-tertiary)]">
              {copy.stripCoverage(formatNumber(data.reducing_events ?? 0))}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

function Strip({ label, value, share, base }: { label: string; value: string; share: number; base?: boolean }) {
  return (
    <div className="grid gap-1.5">
      <div className="flex justify-between gap-3 text-xs text-[var(--color-text-secondary)]">
        <span className="min-w-0">{label}</span>
        <span className="font-mono tabular-nums text-[var(--color-text-primary)]">{value}</span>
      </div>
      <div className="relative h-2 rounded bg-[color-mix(in_srgb,var(--color-text-primary)_10%,transparent)]">
        {!base && (
          <i
            className="absolute inset-y-0 left-0 rounded bg-[var(--color-text-primary)] opacity-80"
            style={{ width: `${Math.min(Math.max(share, 0), 1) * 100}%` }}
          />
        )}
      </div>
    </div>
  );
}

/**
 * Tokens, files and dollars under the hero. Without a priced saving the dollar
 * column says why there is no figure instead of printing $0.00, which would
 * read as "saved nothing" to someone on a subscription.
 */
export function SavingsProof({ data, copy }: { data: SavingsView; copy: SavingsCopy }) {
  const outputTokens = data.output_tokens_saved_estimated ?? 0;
  const outputUsd = data.output_usd_saved_estimated ?? 0;
  const usd = data.total_usd_saved_estimated ?? data.priced_input_savings_usd ?? 0;
  const filesSince = filesTrackedSince(data);
  return (
    <dl className="grid grid-cols-1 gap-5 lg:grid-cols-3 lg:gap-0">
      <Proof
        label={copy.tokensLabel}
        value={formatTokens(data.saved_input_tokens ?? 0)}
        sub={outputTokens > 0 ? copy.tokensSubOutput(formatTokens(outputTokens)) : copy.tokensSub}
      />
      <Proof
        label={copy.filesLabel}
        value={formatNumber(data.files_not_opened ?? 0)}
        note={filesSince ? copy.filesSinceShort(formatDate(filesSince)) : undefined}
        sub={copy.filesSub(BENCHMARK_FEWER_FILES_PCT)}
      />
      {usd > 0 ? (
        <Proof
          label={copy.billLabel}
          value={formatCost(usd)}
          sub={outputUsd > 0 ? copy.billSubOutput(formatCost(outputUsd)) : copy.billSub}
        />
      ) : (
        <Proof label={copy.billLabel} sub={copy.billNone} />
      )}
    </dl>
  );
}

function Proof({ label, value, note, sub }: { label: string; value?: string; note?: string | undefined; sub: string }) {
  return (
    <div className="min-w-0 border-t border-[var(--color-border-default)] pt-4 first:border-t-0 first:pt-0 lg:border-l lg:border-t-0 lg:px-7 lg:pt-0.5 lg:first:border-l-0 lg:first:pl-0">
      <dt className={MICRO}>{label}</dt>
      <dd className="m-0">
        {value !== undefined && (
          <div className="mb-0.5 mt-1.5 text-[22px] font-medium tracking-[-0.01em] tabular-nums text-[var(--color-text-primary)]">
            {value}
            {note && (
              <span className="ml-2 text-xs font-normal tracking-normal text-[var(--color-text-tertiary)]">{note}</span>
            )}
          </div>
        )}
        <p className={`max-w-[36ch] text-xs text-[var(--color-text-secondary)] ${value === undefined ? "mt-2" : ""}`}>
          {sub}
        </p>
      </dd>
    </div>
  );
}
