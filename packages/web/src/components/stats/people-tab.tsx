"use client";

import Link from "next/link";
import { AlertTriangle, Users } from "lucide-react";
import type { StatsHighlights } from "@repowise-dev/types/stats";
import { ArrivalsTimeline, ChronotypeList, StatCallout } from "@repowise-dev/ui/stats";
import { formatNumber } from "@repowise-dev/ui/lib/format";
import { useWeekendDays } from "@/lib/hooks/use-weekend";

/**
 * Tab 3 — the human shape of the repo.
 *
 * Repo-level concentration and human-interest angles only. Per-person ownership
 * share, hotspots owned and dead-code burden all belong to the Contributors
 * page, which opens with a distribution bar and links straight into each
 * profile — a "top owners" list here would just be a worse copy of it, so this
 * tab points there instead.
 */
export function PeopleTab({ data, repoId }: { data: StatsHighlights; repoId: string }) {
  const { people } = data;
  const utcMode = data.rhythm.punch_card.timezone_mode === "utc";
  // Same preset the punch card reads, so a person's "weekend warrior" and the
  // repo's weekend share can never disagree about which days those are.
  const weekendDays = useWeekendDays();

  return (
    <div className="flex flex-col gap-8">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCallout
          label="Contributors"
          value={formatNumber(people.contributor_count)}
          icon={<Users className="h-4 w-4" />}
          sub={`${formatNumber(people.owner_count)} own at least one file`}
          href={`/repos/${repoId}/owners`}
          LinkComponent={Link}
        />
        {people.truck_factor != null && (
          <StatCallout
            label="Truck factor"
            value={formatNumber(people.truck_factor)}
            icon={<AlertTriangle className="h-4 w-4" />}
            tone={people.truck_factor <= 2 ? "warning" : "default"}
            sub="people who together own most of the code"
            hint="The fewest primary owners who between them hold more than half the owned files. A factor of 1 means one person owns most of the codebase."
          />
        )}
        <StatCallout
          label="Single-owner files"
          value={formatNumber(people.single_owner_files)}
          tone="warning"
          sub="only one person has ever really touched them"
        />
        <StatCallout
          label="Knowledge silos"
          value={formatNumber(people.silo_count)}
          tone={people.silo_count > 0 ? "warning" : "default"}
          sub="modules where one person owns over 80%"
        />
      </div>

      {people.chronotypes.length > 0 ? (
        <ChronotypeList people={people.chronotypes} weekendDays={weekendDays} />
      ) : (
        utcMode && (
          <p className="rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-4 text-sm text-[var(--color-text-secondary)]">
            Commit-hour habits need each commit&apos;s local timezone, which this index was built
            before repowise captured. Run{" "}
            <code className="rounded bg-[var(--color-bg-muted)] px-1.5 py-0.5 font-mono text-xs">
              repowise update
            </code>{" "}
            to backfill it, no re-index required.
          </p>
        )
      )}

      <ArrivalsTimeline arrivals={people.arrivals} />
    </div>
  );
}
