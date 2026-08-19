import type { EpisodeSummary } from "@repowise-dev/types/episodes";

/**
 * Facts about the checkout itself, as opposed to claims somebody made about
 * it. These are the structural-tier episodes: things true of the tree right
 * now, derived without an API key, without transcripts, and without any git
 * history at all, which is what makes them the one part of this page that
 * says something on a repository indexed an hour ago.
 *
 * Deliberately not a timeline and deliberately not paged. The store also holds
 * a git tier, and on this repository that tier is 289 rows of one fix commit
 * each, against a Commits page two items up the nav. A list of them here would
 * be that page with a filter on it, so the git tier is not rendered.
 */

/**
 * One heading per kind. A kind with no entry here still renders, under its own
 * raw name: the store does not constrain `kind` and producers add to it, so
 * falling back is the difference between a new detector showing up unlabelled
 * and it not showing up at all.
 */
const KIND_HEADING: Record<string, string> = {
  formatter_drift: "This tree is not formatter-clean",
  editable_shadow: "An editable install shadows an installed command",
  nested_repos: "The walk stops at nested repositories",
  config_override: "Config changes how a command behaves",
};

/**
 * `kind` is an unconstrained string on the wire, so a plain index reaches
 * `Object.prototype`: a row of kind `constructor` would hand React a function
 * as a child and throw.
 */
function heading(kind: string): string {
  return Object.hasOwn(KIND_HEADING, kind)
    ? (KIND_HEADING[kind] as string)
    : kind;
}

export interface CheckoutFactsProps {
  /**
   * Structural-tier episodes. The caller filters by tier; passing git-tier
   * rows here would render commit subjects under checkout headings.
   */
  facts: EpisodeSummary[];
  /**
   * `false` when the server could not read an episode store. Usually a cold
   * start, but *not* only that: the router degrades to the same value when a
   * read fails, and its own docstring names `SQLITE_BUSY` during an index as
   * the realistic case. So the copy behind this flag must not promise a
   * future index will fill the section, because the store may be full right
   * now and merely unreadable this second.
   */
  available: boolean;
  /**
   * Measured total behind the page, when the caller has one. Used only to say
   * so when the page is short of it.
   */
  total?: number;
}

export function CheckoutFacts({ facts, available, total }: CheckoutFactsProps) {
  if (facts.length === 0) {
    return (
      <p className="text-sm text-[var(--color-text-secondary)]">
        {available
          ? "Nothing unusual about this checkout. Facts appear here when the tree drifts from what its own tooling expects, such as an unformatted tree or an editable install shadowing an installed command."
          : "Nothing to show for this checkout right now. Facts land here once an index has looked: an unformatted tree, an editable install shadowing an installed command, or a walk that stops at a nested repository."}
      </p>
    );
  }

  const order: string[] = [];
  const groups = new Map<string, EpisodeSummary[]>();
  for (const fact of facts) {
    const existing = groups.get(fact.kind);
    if (existing) {
      existing.push(fact);
    } else {
      order.push(fact.kind);
      groups.set(fact.kind, [fact]);
    }
  }

  const hidden = Math.max(0, (total ?? facts.length) - facts.length);

  return (
    <>
    <ul className="divide-y divide-[var(--color-border-default)]">
      {order.map((kind) => {
        const rows = groups.get(kind) ?? [];
        // One editable install shadowing three console scripts is three rows
        // carrying one identical verdict, and a note every row repeats says
        // nothing. Hoist it when the whole group agrees; keep it per row when
        // they differ, because then it is telling them apart.
        const shared =
          rows.length > 1 &&
          rows[0]?.still_true &&
          rows.every((r) => r.still_true === rows[0]?.still_true)
            ? (rows[0]?.still_true ?? null)
            : null;
        return (
          <li key={kind} className="py-3 first:pt-0 last:pb-0">
            <p className="text-sm font-medium text-[var(--color-text-primary)]">
              {heading(kind)}
            </p>
            <ul className="mt-1.5 space-y-1.5">
              {rows.map((row) => (
                <FactRow key={row.id} fact={row} hideVerdict={shared !== null} />
              ))}
            </ul>
            {shared && (
              <p className="mt-1.5 text-[11px] text-[var(--color-text-tertiary)]">
                {shared}
              </p>
            )}
          </li>
        );
      })}
    </ul>
    {hidden > 0 && (
      <p className="mt-3 text-xs text-[var(--color-text-tertiary)]">
        Showing {facts.length} of {total} recorded.
      </p>
    )}
    </>
  );
}

function FactRow({
  fact,
  hideVerdict,
}: {
  fact: EpisodeSummary;
  hideVerdict: boolean;
}) {
  // `nodes` is trimmed for the wire and `node_count` is the untrimmed total,
  // so a scope wider than the list has to say so rather than quietly showing
  // the first twelve as if they were all of them.
  const shown = fact.nodes.length;
  const hidden = Math.max(0, fact.node_count - shown);

  return (
    <li className="text-xs">
      <p className="font-mono text-[var(--color-text-secondary)]">
        {fact.evidence}
      </p>
      {shown > 0 && (
        <p className="mt-0.5 font-mono text-[11px] text-[var(--color-text-tertiary)] break-all">
          {fact.nodes.join(", ")}
          {hidden > 0 && (
            <span className="tabular-nums"> and {hidden} more</span>
          )}
        </p>
      )}
      {/* Absent means unchecked, never stale, so nothing is rendered for it.
          Printing "unverified" on the one fact that cannot vouch for itself
          for free would read as a doubt about the fact rather than about the
          check. */}
      {fact.still_true && !hideVerdict && (
        <p className="mt-0.5 text-[11px] text-[var(--color-text-tertiary)]">
          {fact.still_true}
        </p>
      )}
    </li>
  );
}
