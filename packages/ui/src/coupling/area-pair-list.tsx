"use client";

import { memo } from "react";
import type { CouplingAreaPair, CouplingAreasResponse } from "@repowise-dev/types/coupling";
import { PromptForRowsButton, RowPromptAction } from "../health/row-prompt-action";
import { CleanState } from "../shared/clean-state";
import { PageLede } from "../shared/page-lede";
import type { AreaCouplingCopy } from "./area-copy";
import { areaLabel, type AreaPairRef } from "./area-view-state";

/**
 * The row's reading. Only a hidden pair (no dependency between the two areas)
 * is marked, with a dot and a word; a layered pair that imports across is
 * expected coupling and reads as plain figures. The share bar is neutral ink,
 * never a warning or accent colour on every row.
 */
function PairReading({ pair, copy }: { pair: CouplingAreaPair; copy: AreaCouplingCopy }) {
  const pct = Math.round(pair.unexplained_share * 100);
  return (
    <span className="flex min-w-0 flex-col items-start gap-1 sm:items-end">
      {pair.hidden && (
        <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-xs font-medium text-[var(--color-text-primary)]">
          <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-[var(--color-warning)]" />
          {copy.hiddenMark}
        </span>
      )}
      <span className="whitespace-nowrap text-xs tabular-nums text-[var(--color-text-secondary)]">
        {pair.unexplained_pairs > 0 ? copy.unexplained(pair.unexplained_pairs, pct) : copy.allExplained}
      </span>
      <span aria-hidden className="block h-1 w-24 overflow-hidden rounded-full bg-[var(--color-bg-elevated)]">
        <span className="block h-full" style={{ width: `${pct}%`, background: "var(--color-text-tertiary)" }} />
      </span>
    </span>
  );
}

interface RowProps {
  pair: CouplingAreaPair;
  copy: AreaCouplingCopy;
  onOpen: (pair: AreaPairRef) => void;
  onPrompt: (pairs: CouplingAreaPair[]) => void;
}

const AreaPairRow = memo(function AreaPairRow({ pair, copy, onOpen, onPrompt }: RowProps) {
  const name = `${areaLabel(pair.source, copy.rootLabel)} ↔ ${areaLabel(pair.target, copy.rootLabel)}`;
  const meta = [
    copy.filePairs(pair.pairs),
    copy.coChanges(pair.co_changes),
    pair.last_co_change ? copy.lastTogether(pair.last_co_change.slice(0, 10)) : null,
  ].filter(Boolean);
  return (
    <li className="group/row flex flex-col gap-2 border-b border-[var(--color-border-default)] py-3 last:border-b-0 sm:flex-row sm:items-center sm:gap-4">
      <button
        type="button"
        onClick={() => onOpen({ source: pair.source, target: pair.target })}
        className="flex min-w-0 flex-1 flex-col gap-3 rounded-md px-1 py-1 text-left hover:bg-[var(--color-bg-elevated)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-focus-ring)] sm:flex-row sm:items-center"
      >
        <span className="flex min-w-0 flex-1 flex-col gap-1">
          <span className="break-all font-mono text-[15px] text-[var(--color-text-primary)] group-hover/row:text-[var(--color-accent-primary)]">
            {name}
          </span>
          <span className="text-xs tabular-nums text-[var(--color-text-secondary)]">
            {meta.join(" · ")}
          </span>
        </span>
        <PairReading pair={pair} copy={copy} />
      </button>
      {pair.hidden && (
        <RowPromptAction
          label={copy.promptLabel}
          subject={name}
          onClick={() => onPrompt([pair])}
          className="self-start sm:self-center"
        />
      )}
    </li>
  );
});

export interface CouplingAreaPairListProps {
  data: CouplingAreasResponse;
  copy: AreaCouplingCopy;
  testsIncluded: boolean;
  onOpen: (pair: AreaPairRef) => void;
  onPrompt: (pairs: CouplingAreaPair[]) => void;
  /** Rendered beside the "showing" line: the escape to every file pair, a workspace link. */
  footer?: React.ReactNode;
}

/** The lede: hidden pairs when there are any, else a clean or an unchecked state. */
function Lede({
  data,
  copy,
  testsIncluded,
  hiddenRows,
  onPrompt,
}: Omit<CouplingAreaPairListProps, "onOpen" | "footer"> & { hiddenRows: CouplingAreaPair[] }) {
  const { totals, depth } = data;
  const hidden = totals.hidden_area_pairs;
  if (hidden > 0) {
    return (
      <PageLede
        label={copy.ledeLabel}
        value={hidden.toLocaleString()}
        unit={copy.ledeUnit(hidden)}
        action={
          <PromptForRowsButton
            count={hiddenRows.length}
            label={copy.bulkPromptLabel}
            onClick={() => onPrompt(hiddenRows)}
          />
        }
      >
        <p className="text-[15px] text-[var(--color-text-primary)]">{copy.verdict(hidden)}</p>
        <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
          {copy.scope(totals.pairs, depth, testsIncluded)}
        </p>
      </PageLede>
    );
  }
  // Some kept pairs predate the dependency check: "all clear" would claim more
  // than was checked.
  if (totals.unlabelled_pairs > 0) {
    return <p className="text-xs text-[var(--color-text-secondary)]">{copy.unchecked}</p>;
  }
  return (
    <CleanState
      align="start"
      title={copy.cleanTitle(depth)}
      description={copy.cleanBody(totals.pairs)}
    />
  );
}

/**
 * The area view's body: a verdict lede, then area pairs, hidden ones first. An
 * area pair is hidden only when no dependency joins the two areas at all;
 * layered areas that import each other are expected coupling, listed but not
 * marked. A ranked list rather than a matrix: on real indexes a few dozen of
 * a few hundred area pairs carry the finding, so a grid would be mostly empty.
 */
export function CouplingAreaPairList({
  data,
  copy,
  testsIncluded,
  onOpen,
  onPrompt,
  footer,
}: CouplingAreaPairListProps) {
  const pairs = data.area_pairs ?? [];
  const hiddenRows = pairs.filter((p) => p.hidden);
  return (
    <div className="space-y-6">
      <Lede
        data={data}
        copy={copy}
        testsIncluded={testsIncluded}
        hiddenRows={hiddenRows}
        onPrompt={onPrompt}
      />

      {pairs.length > 0 && (
        <ul className="border-t border-[var(--color-border-default)]">
          {pairs.map((pair) => (
            <AreaPairRow
              key={`${pair.source}|${pair.target}`}
              pair={pair}
              copy={copy}
              onOpen={onOpen}
              onPrompt={onPrompt}
            />
          ))}
        </ul>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-[var(--color-text-secondary)]">
        <span>
          {data.totals.area_pairs > pairs.length
            ? copy.showing(pairs.length, data.totals.area_pairs)
            : null}
        </span>
        {footer}
      </div>
    </div>
  );
}
