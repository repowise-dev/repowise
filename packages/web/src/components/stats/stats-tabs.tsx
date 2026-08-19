"use client";

import { parseAsStringLiteral, useQueryState } from "nuqs";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@repowise-dev/ui/ui/tabs";
import { ErrorBoundary } from "@repowise-dev/ui/shared";
import type { StatsHighlights } from "@repowise-dev/types/stats";
import { ByTheNumbersTab } from "./by-the-numbers-tab";
import { RhythmTab } from "./rhythm-tab";
import { PeopleTab } from "./people-tab";

/**
 * Three tabs, down from five.
 *
 * "Code & Quality" and "Architecture" were removed outright: every figure on
 * them had a richer counterpart on Code Health, Architecture or Knowledge
 * Graph, and "Growth & Activity" was almost entirely the Commits page. The
 * legacy tab values still resolve so an old bookmark lands somewhere sensible
 * rather than on an empty page.
 */
const TAB_VALUES = ["numbers", "rhythm", "people"] as const;
type TabValue = (typeof TAB_VALUES)[number];

const LEGACY_TABS: Record<string, TabValue> = {
  growth: "rhythm",
  quality: "numbers",
  architecture: "numbers",
};

export function StatsTabs({ data, repoId }: { data: StatsHighlights; repoId: string }) {
  const [raw, setTab] = useQueryState(
    "tab",
    parseAsStringLiteral([...TAB_VALUES, ...Object.keys(LEGACY_TABS)]).withDefault("numbers"),
  );
  const tab = (LEGACY_TABS[raw] ?? raw) as TabValue;

  return (
    <Tabs value={tab} onValueChange={(v) => setTab(v as TabValue)}>
      <TabsList>
        <TabsTrigger value="numbers">By the Numbers</TabsTrigger>
        <TabsTrigger value="rhythm">Rhythm</TabsTrigger>
        <TabsTrigger value="people">People</TabsTrigger>
      </TabsList>
      <TabsContent value="numbers" className="mt-4">
        <ErrorBoundary title="Couldn't load stats">
          <ByTheNumbersTab data={data} />
        </ErrorBoundary>
      </TabsContent>
      <TabsContent value="rhythm" className="mt-4">
        <ErrorBoundary title="Couldn't load rhythm">
          <RhythmTab data={data} />
        </ErrorBoundary>
      </TabsContent>
      <TabsContent value="people" className="mt-4">
        <ErrorBoundary title="Couldn't load contributors">
          <PeopleTab data={data} repoId={repoId} />
        </ErrorBoundary>
      </TabsContent>
    </Tabs>
  );
}
