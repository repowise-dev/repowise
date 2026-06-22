"use client";

import { parseAsStringLiteral, useQueryState } from "nuqs";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@repowise-dev/ui/ui/tabs";
import { ErrorBoundary } from "@repowise-dev/ui/shared";
import type { StatsHighlights } from "@repowise-dev/types/stats";
import { ByTheNumbersTab } from "./by-the-numbers-tab";
import { GrowthTab } from "./growth-tab";
import { PeopleTab } from "./people-tab";
import { QualityTab } from "./quality-tab";
import { ArchitectureTab } from "./architecture-tab";

const TAB_VALUES = ["numbers", "growth", "people", "quality", "architecture"] as const;

export function StatsTabs({ data }: { data: StatsHighlights }) {
  const [tab, setTab] = useQueryState(
    "tab",
    parseAsStringLiteral(TAB_VALUES).withDefault("numbers"),
  );

  return (
    <Tabs value={tab} onValueChange={(v) => setTab(v as (typeof TAB_VALUES)[number])}>
      <TabsList>
        <TabsTrigger value="numbers">By the Numbers</TabsTrigger>
        <TabsTrigger value="growth">Growth &amp; Activity</TabsTrigger>
        <TabsTrigger value="people">People</TabsTrigger>
        <TabsTrigger value="quality">Code &amp; Quality</TabsTrigger>
        <TabsTrigger value="architecture">Architecture</TabsTrigger>
      </TabsList>
      <TabsContent value="numbers" className="mt-4">
        <ErrorBoundary title="Couldn't load stats">
          <ByTheNumbersTab data={data} />
        </ErrorBoundary>
      </TabsContent>
      <TabsContent value="growth" className="mt-4">
        <ErrorBoundary title="Couldn't load activity">
          <GrowthTab data={data} />
        </ErrorBoundary>
      </TabsContent>
      <TabsContent value="people" className="mt-4">
        <ErrorBoundary title="Couldn't load contributors">
          <PeopleTab data={data} />
        </ErrorBoundary>
      </TabsContent>
      <TabsContent value="quality" className="mt-4">
        <ErrorBoundary title="Couldn't load quality">
          <QualityTab data={data} />
        </ErrorBoundary>
      </TabsContent>
      <TabsContent value="architecture" className="mt-4">
        <ErrorBoundary title="Couldn't load architecture">
          <ArchitectureTab data={data} />
        </ErrorBoundary>
      </TabsContent>
    </Tabs>
  );
}
