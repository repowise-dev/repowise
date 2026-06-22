"use client";

import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import type { StatsMonthlyBucket } from "@repowise-dev/types/stats";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";

interface ActivityTrendChartProps {
  monthly: StatsMonthlyBucket[];
  title?: string;
}

/** Stacked monthly commit volume, split into agent-authored vs human. The
 *  "when did AI start writing this code" view. */
export function ActivityTrendChart({ monthly, title = "Commits per month" }: ActivityTrendChartProps) {
  if (monthly.length === 0) return null;

  const data = monthly.map((b) => ({
    month: b.month,
    human: Math.max(b.total - b.agent, 0),
    agent: b.agent,
  }));

  return (
    <Card className="h-full">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">{title}</CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        <div className="h-[220px] w-full">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
              <XAxis
                dataKey="month"
                tick={{ fontSize: 10, fill: "var(--color-text-tertiary)" }}
                tickLine={false}
                axisLine={false}
                interval="preserveStartEnd"
                minTickGap={24}
              />
              <YAxis
                tick={{ fontSize: 10, fill: "var(--color-text-tertiary)" }}
                tickLine={false}
                axisLine={false}
                allowDecimals={false}
                width={40}
              />
              <Tooltip
                cursor={{ fill: "var(--color-bg-muted)", opacity: 0.4 }}
                contentStyle={{
                  background: "var(--color-bg-overlay)",
                  border: "1px solid var(--color-border-default)",
                  borderRadius: 8,
                  fontSize: 11,
                  color: "var(--color-text-primary)",
                }}
              />
              <Bar dataKey="human" stackId="c" fill="var(--color-accent-primary)" radius={[0, 0, 0, 0]} />
              <Bar dataKey="agent" stackId="c" fill="var(--color-info)" radius={[2, 2, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
        <div className="mt-2 flex items-center gap-4 text-[11px] text-[var(--color-text-secondary)]">
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full" style={{ background: "var(--color-accent-primary)" }} />
            Human
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full" style={{ background: "var(--color-info)" }} />
            Agent
          </span>
        </div>
      </CardContent>
    </Card>
  );
}
