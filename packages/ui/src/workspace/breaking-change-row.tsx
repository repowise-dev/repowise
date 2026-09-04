"use client";

/**
 * One breaking change, rendered the same way wherever it appears: the System
 * Map rail and the contracts page both draw this row rather than each keeping
 * their own copy.
 *
 * The row is pure presentation. It knows a change carries a provider symbol and
 * a consumer symbol, but not how this application routes to code, so the host
 * passes href builders in. With none supplied the row renders exactly the plain
 * text it always did, which is what the map rail wants.
 */

import type { BreakingChange, BreakingChangeConsumer, BreakingChangeReport } from "@repowise-dev/types";
import { RailChip } from "./system-map/system-map-rail";

/**
 * How the host turns a change's code references into links. `repo` is the
 * workspace alias on the change; the host owns the alias-to-repo-id map and the
 * route shape. Returning null means "not routable", and the row falls back to
 * plain text rather than a dead link.
 */
export interface BreakingChangeLinks {
  symbolHref?: (repo: string, symbolId: string) => string | null;
  fileHref?: (repo: string, file: string) => string | null;
}

export interface BreakingChangeRowProps {
  change: BreakingChange;
  /** Focus a provider/consumer service on the map. */
  onSelectNode?: (id: string) => void;
  /** Open the contract itself. Takes precedence over `onSelectNode` on the contract id. */
  onSelectContract?: (contractId: string, change: BreakingChange) => void;
  links?: BreakingChangeLinks;
}

export function severityColor(severity: string): string {
  return severity === "breaking" ? "var(--color-risk-high)" : "var(--color-warning)";
}

/** Breaking first, warnings after; otherwise the report's own order is kept. */
export function sortChangesBySeverity(changes: BreakingChange[]): BreakingChange[] {
  return [...changes].sort((a, b) =>
    a.severity === b.severity ? 0 : a.severity === "breaking" ? -1 : 1,
  );
}

/** The one-line count both surfaces show above the rows. */
export function breakingChangeSummary(report: BreakingChangeReport): string {
  const repos = report.impacted_repos.length;
  return `${report.breaking_count} breaking, ${report.warning_count} ${
    report.warning_count === 1 ? "warning" : "warnings"
  } across ${repos} ${repos === 1 ? "repo" : "repos"}`;
}

/** Stable key for a change inside a report. */
export function breakingChangeKey(change: BreakingChange): string {
  return `${change.contract_id}:${change.kind}:${change.field_name ?? ""}`;
}

/** Prefer the symbol page, fall back to the file page, then to plain text. */
function codeHref(
  links: BreakingChangeLinks | undefined,
  repo: string,
  symbolId: string | null | undefined,
  file: string,
): string | undefined {
  if (symbolId && links?.symbolHref) {
    const href = links.symbolHref(repo, symbolId);
    if (href) return href;
  }
  if (links?.fileHref) return links.fileHref(repo, file) ?? undefined;
  return undefined;
}

export function BreakingChangeRow({
  change,
  onSelectNode,
  onSelectContract,
  links,
}: BreakingChangeRowProps) {
  const color = severityColor(change.severity);
  const providerHref = codeHref(links, change.provider_repo, change.provider_symbol_id, change.provider_file);
  const onContract = onSelectContract
    ? () => onSelectContract(change.contract_id, change)
    : onSelectNode
      ? () => onSelectNode(change.provider_node_id)
      : undefined;

  return (
    <div className="border-b border-[var(--color-border-subtle)] px-3 py-2">
      <div className="flex items-center gap-1.5">
        <RailChip color={color}>{change.severity}</RailChip>
        <button
          type="button"
          onClick={onContract}
          disabled={!onContract}
          title={`Provider: ${change.provider_repo} · ${change.provider_file}`}
          className={`min-w-0 flex-1 truncate text-left font-semibold text-[var(--color-text-primary)] ${
            onContract ? "cursor-pointer hover:underline" : "cursor-default"
          }`}
        >
          {change.contract_id}
        </button>
      </div>
      <div className="mt-[3px] text-[var(--color-text-secondary)]">{change.detail}</div>
      <div className="mt-0.5 text-[10px] text-[var(--color-text-tertiary)]">
        {change.provider_repo} ·{" "}
        {providerHref ? (
          <a
            href={providerHref}
            title={change.provider_symbol_id ?? change.provider_file}
            className="cursor-pointer hover:underline"
          >
            {change.provider_file}
          </a>
        ) : (
          change.provider_file
        )}
      </div>
      {change.impacted_consumers.length > 0 && (
        <div className="mt-1.5">
          <div className="text-[10px] font-bold text-[var(--color-text-tertiary)]">
            {change.impacted_consumers.length === 1
              ? "Endangers 1 consumer"
              : `Endangers ${change.impacted_consumers.length} consumers`}
          </div>
          {change.impacted_consumers.map((c) => (
            <ConsumerLine
              key={`${c.node_id}:${c.file}`}
              consumer={c}
              {...(onSelectNode ? { onSelectNode } : {})}
              {...(links ? { links } : {})}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * A link wins over the node-selection button when the host supplied one: an
 * anchor cannot sit inside a button, and code is the more useful destination
 * when there is no map to focus.
 */
function ConsumerLine({
  consumer,
  onSelectNode,
  links,
}: {
  consumer: BreakingChangeConsumer;
  onSelectNode?: (id: string) => void;
  links?: BreakingChangeLinks;
}) {
  const href = codeHref(links, consumer.repo, consumer.symbol_id, consumer.file);
  const className = `block w-full truncate py-0.5 pl-2 text-left text-[11px] text-[var(--color-text-secondary)] ${
    href || onSelectNode ? "cursor-pointer hover:underline" : "cursor-default"
  }`;
  const title = `Consumer: ${consumer.repo} · ${consumer.file}`;
  const body = (
    <>
      <span className="text-[var(--color-text-primary)]">{consumer.repo}</span> · {consumer.file}
    </>
  );

  if (href) {
    return (
      <a href={href} title={title} className={className}>
        {body}
      </a>
    );
  }
  return (
    <button
      type="button"
      onClick={() => onSelectNode?.(consumer.node_id)}
      disabled={!onSelectNode}
      title={title}
      className={className}
    >
      {body}
    </button>
  );
}
