"use client";

import type { ReactNode } from "react";
import { X, ArrowRight, CornerDownRight, Flame, Skull } from "lucide-react";
import { Badge } from "../ui/badge";
import { ScrollArea } from "../ui/scroll-area";
import { Skeleton } from "../ui/skeleton";
import { CollapsibleSection } from "../shared/collapsible-section";
import { InfoTip } from "../shared/info-tip";
import { truncatePath } from "../lib/format";
// Shared band function, never a local threshold: two surfaces disagreeing
// about where "Good" starts is worse than the import.
import { healthBand } from "../overview/health-lede";
import type { CommunityDetail } from "@repowise-dev/types/graph";

/**
 * What the rolled-up health score means for an *area* rather than a file.
 *
 * The number alone is not actionable: 6.2 is a fine file and a group of files
 * averaging 6.2 is a different claim. These sentences say what the reader
 * should do about it, per the design language's "lead with one figure and a
 * plain-language interpretation".
 *
 * Every one is phrased as a claim about the *average*, because that is all a
 * LOC-weighted mean supports. "Nothing here is risky" would be the natural
 * reading of a healthy score and it is not one this figure can make: a single
 * bad file inside a healthy group is exactly the file such a sentence would
 * tell the reader to skip.
 *
 * Keyed on `healthBand`'s label, the same five bands the Code Health lede and
 * the file health drawer show, so one score never gets two vocabularies.
 */
const HEALTH_READING: Record<string, string> = {
  Excellent:
    "On average this area scores well. A mean hides its worst file, so check the flags below.",
  Good: "On average this area scores well. A mean hides its worst file, so check the flags below.",
  Fair: "This area averages into the middle; some files here carry real defect risk.",
  "Needs work": "This area averages low. Read it before you change it.",
  Critical: "This area averages into the worst band. Read it before you change it.",
};

export interface GraphCommunityPanelProps {
  /** Community id surfaced in the empty/loading title fallback. */
  communityId: number;
  /** Pre-fetched community detail; `null`/`undefined` while loading. */
  community: CommunityDetail | null | undefined;
  /** Loading flag from the consumer's data hook. */
  isLoading: boolean;
  onClose: () => void;
  /** Optional: draw this community's own scoped file graph. The primary action,
   *  and the same thing double-clicking the hub does. */
  onEnterCommunity?: (() => void) | undefined;
  /** Optional: open a neighbouring community's panel and select its hub. Makes
   *  the neighbour list navigable; without it the rows render as plain text
   *  rather than as buttons that do nothing. */
  onNeighborSelect?: ((communityId: number) => void) | undefined;
  /** Build a member href (typically the canonical file page). */
  memberHref?: ((path: string) => string) | undefined;
  /** Where a *hot* member goes: its row in the Code Health triage map. */
  healthHrefFor?: ((path: string) => string) | undefined;
  /** Where a *dead* member goes: the dead-code findings list. Repo-scoped,
   *  because there is no per-file dead-code route. */
  deadCodeHref?: string | undefined;
  /** Repo-wide Code Health, for the panel's "leave for the right page" link. */
  codeHealthHref?: string | undefined;
}

export function GraphCommunityPanel({
  communityId,
  community,
  isLoading,
  onClose,
  onEnterCommunity,
  onNeighborSelect,
  memberHref,
  healthHrefFor,
  deadCodeHref,
  codeHealthHref,
}: GraphCommunityPanelProps) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Header and the primary action sit OUTSIDE the scroll container, so
          "Enter this community" stays reachable however far down the reader
          has scrolled. */}
      <div className="flex shrink-0 items-start justify-between gap-2 border-b border-[var(--color-border-default)] px-4 py-3">
        <div className="min-w-0">
          <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
            Community
          </p>
          <p className="mt-0.5 truncate text-sm font-medium text-[var(--color-text-primary)]">
            {community?.label ?? `Community ${communityId}`}
          </p>
          {community && (
            // The label and the grouping are both algorithmic. Saying so is the
            // difference between a name the reader trusts too much and one they
            // read as a summary.
            <p className="text-[11px] text-[var(--color-text-secondary)]">
              {community.member_count} files, grouped automatically
              {(community.hidden_member_count ?? 0) > 0 &&
                ` · ${community.hidden_member_count} hidden by the file filter`}
            </p>
          )}
        </div>
        <button
          onClick={onClose}
          aria-label="Close community details"
          className="shrink-0 rounded p-1 transition-colors hover:bg-[var(--color-bg-elevated)]"
        >
          <X className="h-4 w-4 text-[var(--color-text-tertiary)]" />
        </button>
      </div>

      {onEnterCommunity && (
        <div className="shrink-0 border-b border-[var(--color-border-default)] px-4 py-2">
          <button
            onClick={onEnterCommunity}
            className="flex w-full items-center justify-center gap-1.5 rounded-md border border-[var(--color-accent-primary)]/30 bg-[var(--color-accent-primary)]/10 px-2 py-1.5 text-xs font-medium text-[var(--color-accent-primary)] transition-colors hover:bg-[var(--color-accent-primary)]/20"
          >
            <CornerDownRight className="h-3 w-3" />
            Enter this community
          </button>
        </div>
      )}

      {isLoading ? (
        <div className="space-y-2 p-4">
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-2/3" />
        </div>
      ) : community ? (
        <ScrollArea className="min-h-0 flex-1">
          {/*
            Reading order, and only the first two are open on arrival.

            This used to be five undifferentiated blocks of 11px prose followed
            by thirty member rows and ten neighbour rows, all expanded, so the
            figure that answers "is this area in trouble" scrolled away and the
            rest was noise. The lede and the fact grid answer the question; the
            member list is the audit trail behind those numbers and the
            neighbours are a navigation aid, so both are demoted behind a
            disclosure. Same call `CollapsibleSection` exists for on the file
            health drawer, and the same component.
          */}
          <div className="flex flex-col gap-4 px-4 py-4">
            <HealthLede community={community} codeHealthHref={codeHealthHref} />
            <FactGrid community={community} deadCodeHref={deadCodeHref} />

            <CollapsibleSection
              title="Members"
              hint={
                community.truncated
                  ? `${community.members.length} of ${community.member_count}`
                  : `${community.members.length}`
              }
            >
              {/* The counts in the grid above are over every member; this is a
                  ranked page of them. Without this line a reader counts four
                  flames under a headline of twelve. */}
              {community.truncated && (
                <p className="text-[11px] text-[var(--color-text-secondary)]">
                  The {community.members.length} most connected. The counts above
                  cover all {community.member_count}.
                </p>
              )}
              <div className="space-y-1">
                {community.members.map((m) => (
                  <MemberRow
                    key={m.path}
                    path={m.path}
                    pagerank={m.pagerank}
                    maxPagerank={community.members[0]?.pagerank || 1}
                    isEntryPoint={m.is_entry_point}
                    isHotspot={m.is_hotspot}
                    isDead={m.is_dead}
                    memberHref={memberHref}
                    healthHrefFor={healthHrefFor}
                    deadCodeHref={deadCodeHref}
                  />
                ))}
              </div>
            </CollapsibleSection>

            {community.neighboring_communities.length > 0 && (
              <CollapsibleSection
                title="Talks to"
                hint={`${community.neighboring_communities.length}`}
              >
                <p className="text-[11px] text-[var(--color-text-secondary)]">
                  Groups this one depends on or is depended on by, most-connected
                  first.
                </p>
                <div className="space-y-1">
                  {community.neighboring_communities.map((n) => (
                    <NeighborRow
                      key={n.community_id}
                      label={n.label}
                      crossEdgeCount={n.cross_edge_count}
                      onSelect={
                        onNeighborSelect
                          ? () => onNeighborSelect(n.community_id)
                          : undefined
                      }
                    />
                  ))}
                </div>
              </CollapsibleSection>
            )}
          </div>
        </ScrollArea>
      ) : (
        <div className="p-4">
          <p className="text-xs text-[var(--color-text-secondary)]">Community not found</p>
        </div>
      )}
    </div>
  );
}

/** The lead figure: is this area in trouble? */
function HealthLede({
  community,
  codeHealthHref,
}: {
  community: CommunityDetail;
  codeHealthHref?: string | undefined;
}) {
  const score = community.health_score;
  const scored = community.scored_member_count ?? 0;

  if (score == null) {
    return (
      <div>
        <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
          Health
        </p>
        <p className="mt-1 text-sm text-[var(--color-text-primary)]">Not scored</p>
        <p className="mt-0.5 text-[11px] text-[var(--color-text-secondary)]">
          None of these files carry a health score yet, so this area has no
          reading. That is missing evidence, not a clean bill.
        </p>
      </div>
    );
  }

  const band = healthBand(score);
  return (
    <div>
      <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
        Health
      </p>
      <div className="mt-1 flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span
          className="text-[32px] font-semibold leading-none tracking-tight tabular-nums"
          style={{ color: band.color }}
        >
          {score.toFixed(1)}
        </span>
        <span className="text-xs text-[var(--color-text-tertiary)]">out of 10</span>
        <span
          className="w-fit rounded-full border px-2 py-0.5 text-[11px] font-medium"
          style={{
            color: band.color,
            borderColor: `color-mix(in srgb, ${band.color} 40%, transparent)`,
            background: `color-mix(in srgb, ${band.color} 9%, transparent)`,
          }}
        >
          {band.label}
        </span>
      </div>
      <p className="mt-1.5 text-[11px] leading-relaxed text-[var(--color-text-secondary)]">
        {HEALTH_READING[band.label]}{" "}
        {/* The mean is over the files that have a score, which is rarely all of
            them. Saying how many stops the figure from claiming more coverage
            than it has. */}
        Averaged over the {scored} of {community.member_count} files that are
        scored, weighted by size.
        {codeHealthHref && (
          <>
            {" "}
            <a
              href={codeHealthHref}
              className="text-[var(--color-accent-primary)] hover:underline"
            >
              Code Health
            </a>
          </>
        )}
      </p>
    </div>
  );
}

/**
 * The state of the area as a hairline fact grid, copying `MetricGrid` on the
 * file health drawer.
 *
 * These were four prose blocks and an icon row. A grid makes "12 files" and
 * "1.4%" scannable as the same kind of news and gives each figure a label,
 * which is what stops the panel reading as a wall of sentences. Explanations
 * that were paragraphs are tooltips: the cohesion figure is unreadable without
 * one and equally unreadable with three lines of prose taking the space above
 * the member list.
 */
function FactGrid({
  community,
  deadCodeHref,
}: {
  community: CommunityDetail;
  deadCodeHref?: string | undefined;
}) {
  const hot = community.hot_count ?? 0;
  const dead = community.dead_count ?? 0;
  const decisions = community.decision_count ?? 0;
  const owner = community.primary_owner;
  const ownerFiles = community.primary_owner_file_count ?? 0;

  const fileWord = (n: number) => (n === 1 ? "file" : "files");

  const cells: { label: string; value: ReactNode; tip?: string }[] = [
    {
      label: "Changes often",
      value: (
        <FactValue muted={hot === 0}>
          {hot === 0 ? "none" : `${hot} ${fileWord(hot)}`}
        </FactValue>
      ),
      tip: "Members git says are churn hotspots. The flames in the member list mark which ones.",
    },
    {
      label: "Unreachable",
      value:
        dead > 0 && deadCodeHref ? (
          <a
            href={deadCodeHref}
            className="text-sm font-semibold tabular-nums text-[var(--color-accent-primary)] hover:underline"
          >
            {dead} {fileWord(dead)}
          </a>
        ) : (
          <FactValue muted={dead === 0}>
            {dead === 0 ? "none" : `${dead} ${fileWord(dead)}`}
          </FactValue>
        ),
      tip: "Members with an open dead-code finding.",
    },
    {
      label: "Under a decision",
      value: (
        <FactValue muted={decisions === 0}>
          {decisions === 0 ? "none" : `${decisions} ${fileWord(decisions)}`}
        </FactValue>
      ),
      tip: "Members named by a recorded architectural decision.",
    },
    // Conductance where the index has it; the old density is the fallback for
    // an index that predates it, since it decays with size.
    community.conductance != null
      ? {
          label: "Stays inside",
          value: (
            <FactValue>{Math.round((1 - community.conductance) * 100)}%</FactValue>
          ),
          tip: "The share of this group's file dependencies that stay inside it rather than reaching another group. Higher is tighter, and it does not fall as a group grows.",
        }
      : {
          label: "Cohesion",
          value: <FactValue>{(community.cohesion * 100).toFixed(1)}%</FactValue>,
          tip: "The share of possible file pairs in this group that actually depend on each other. It drops as a group grows, so it compares groups of similar size rather than a group against the repo.",
        },
  ];

  if (owner) {
    cells.push({
      label: "Mostly owned by",
      value: (
        <span
          className="block truncate text-sm text-[var(--color-text-primary)]"
          title={owner}
        >
          {owner}
        </span>
      ),
      tip: `Primary owner on ${ownerFiles} of these files, from git blame. Most files owned, not most commits.`,
    });
  }

  // A one-file group has no pairs and nothing inside to stay in.
  const visible =
    community.member_count <= 1
      ? cells.filter((c) => c.label !== "Cohesion" && c.label !== "Stays inside")
      : cells;

  return (
    <dl className="grid grid-cols-2 border-y border-[var(--color-border-default)]">
      {visible.map((c, i) => (
        <div
          key={c.label}
          className={[
            "min-w-0 px-3 py-2.5 border-[var(--color-border-default)]",
            // Hairlines between cells only; the outer edges come from border-y
            // on the wrapper, so cells never double up on a boundary.
            i % 2 === 1 ? "border-l" : "",
            i >= 2 ? "border-t" : "",
          ]
            .filter(Boolean)
            .join(" ")}
        >
          <dt className="flex items-center gap-1 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
            <span className="truncate">{c.label}</span>
            {c.tip && <InfoTip content={c.tip} label={`About ${c.label}`} />}
          </dt>
          <dd className="mt-1">{c.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** A fact value, greyed when it is a zero: "none" is news too, quieter news. */
function FactValue({ children, muted }: { children: ReactNode; muted?: boolean }) {
  return (
    <span
      className={
        muted
          ? "text-sm tabular-nums text-[var(--color-text-tertiary)]"
          : "text-sm font-semibold tabular-nums text-[var(--color-text-primary)]"
      }
    >
      {children}
    </span>
  );
}

function MemberRow({
  path,
  pagerank,
  maxPagerank,
  isEntryPoint,
  isHotspot,
  isDead,
  memberHref,
  healthHrefFor,
  deadCodeHref,
}: {
  path: string;
  pagerank: number;
  maxPagerank: number;
  isEntryPoint: boolean;
  isHotspot?: boolean | undefined;
  isDead?: boolean | undefined;
  memberHref?: ((path: string) => string) | undefined;
  healthHrefFor?: ((path: string) => string) | undefined;
  deadCodeHref?: string | undefined;
}) {
  const barWidth = maxPagerank > 0 ? Math.round((pagerank / maxPagerank) * 100) : 0;
  const healthHref = isHotspot ? healthHrefFor?.(path) : undefined;

  return (
    <div className="flex items-center gap-2 rounded px-1.5 py-1 transition-colors hover:bg-[var(--color-bg-elevated)]">
      <div className="min-w-0 flex-1">
        {memberHref ? (
          <a
            href={memberHref(path)}
            className="block truncate font-mono text-xs text-[var(--color-text-primary)] hover:text-[var(--color-accent-primary)] hover:underline"
            title={path}
          >
            {truncatePath(path)}
          </a>
        ) : (
          <p
            className="truncate font-mono text-xs text-[var(--color-text-primary)]"
            title={path}
          >
            {truncatePath(path)}
          </p>
        )}
      </div>
      {/* A flagged member links where the flag is explained, which is the only
          per-file scoped exit this panel can offer honestly. */}
      {isHotspot &&
        (healthHref ? (
          <a
            href={healthHref}
            title="Changes often. Open it in Code Health."
            aria-label={`${path} is a churn hotspot; open it in Code Health`}
            className="shrink-0 text-[var(--color-warning)] hover:opacity-80"
          >
            <Flame className="h-3 w-3" />
          </a>
        ) : (
          <Flame
            className="h-3 w-3 shrink-0 text-[var(--color-warning)]"
            aria-label="Churn hotspot"
          />
        ))}
      {isDead &&
        (deadCodeHref ? (
          <a
            href={deadCodeHref}
            title="Flagged unreachable. Open the dead-code findings."
            aria-label={`${path} is flagged unreachable; open the dead-code findings`}
            className="shrink-0 text-[var(--color-text-tertiary)] hover:opacity-80"
          >
            <Skull className="h-3 w-3" />
          </a>
        ) : (
          <Skull
            className="h-3 w-3 shrink-0 text-[var(--color-text-tertiary)]"
            aria-label="Flagged unreachable"
          />
        ))}
      <div className="h-1.5 w-12 shrink-0 overflow-hidden rounded-full bg-[var(--color-bg-elevated)]">
        <div
          className="h-full rounded-full bg-[var(--color-accent)]"
          style={{ width: `${barWidth}%` }}
        />
      </div>
      {isEntryPoint && (
        // Was "EP" at 8px. An abbreviation nothing on screen expands is not a
        // label; the title carries the rest, because a 340px rail cannot.
        <Badge
          variant="accent"
          className="h-4 shrink-0 text-[10px]"
          title="Entry point: something outside this repo calls into this file"
        >
          Entry
        </Badge>
      )}
    </div>
  );
}

function NeighborRow({
  label,
  crossEdgeCount,
  onSelect,
}: {
  label: string;
  crossEdgeCount: number;
  onSelect?: (() => void) | undefined;
}) {
  const body = (
    <>
      <div className="flex min-w-0 items-center gap-1.5">
        <ArrowRight className="h-3 w-3 shrink-0 text-[var(--color-text-tertiary)]" />
        <span className="truncate text-xs text-[var(--color-text-primary)]">{label}</span>
      </div>
      <Badge variant="outline" className="h-4 shrink-0 text-[10px]">
        {crossEdgeCount} edges
      </Badge>
    </>
  );

  // These rows looked clickable and were not: plain divs with a hover tint and
  // no handler. They are buttons when the host can navigate, and plain rows
  // when it cannot.
  return onSelect ? (
    <button
      type="button"
      onClick={onSelect}
      className="flex w-full items-center justify-between gap-2 rounded px-1.5 py-1 text-left transition-colors hover:bg-[var(--color-bg-elevated)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
    >
      {body}
    </button>
  ) : (
    <div className="flex items-center justify-between gap-2 rounded px-1.5 py-1">{body}</div>
  );
}
