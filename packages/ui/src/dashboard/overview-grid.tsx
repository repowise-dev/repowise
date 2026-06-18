import * as React from "react";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";

/**
 * The Overview's primary two-column arrangement: a wide main column (health,
 * contributors) beside a narrower triage/savings rail. Pure layout so the
 * arrangement is upgradable from the package.
 */
export function OverviewGrid({
  main,
  rail,
}: {
  main: React.ReactNode;
  rail: React.ReactNode;
}) {
  return (
    <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-3">
      <div className="space-y-4 lg:col-span-2">{main}</div>
      <div className="space-y-4">{rail}</div>
    </div>
  );
}

/** Two-up panel grid used by the Overview tab bodies. */
export function OverviewPanelPair({ children }: { children: React.ReactNode }) {
  return <div className="grid grid-cols-1 gap-4 md:grid-cols-2">{children}</div>;
}

/** Card chrome around the commit-activity sparkline, with its category legend. */
export function CommitActivityCard({
  sparkline,
}: {
  sparkline: React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Commit Activity</CardTitle>
      </CardHeader>
      <CardContent>
        {sparkline}
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-[var(--color-text-tertiary)]">
          <span className="flex items-center gap-1">
            <span
              className="inline-block h-2 w-2 rounded-sm"
              style={{ background: "var(--color-info)" }}
            />{" "}
            Feature
          </span>
          <span className="flex items-center gap-1">
            <span
              className="inline-block h-2 w-2 rounded-sm"
              style={{ background: "var(--color-error)" }}
            />{" "}
            Fix
          </span>
          <span className="flex items-center gap-1">
            <span
              className="inline-block h-2 w-2 rounded-sm"
              style={{ background: "var(--color-accent-secondary)" }}
            />{" "}
            Refactor
          </span>
          <span className="flex items-center gap-1">
            <span
              className="inline-block h-2 w-2 rounded-sm"
              style={{ background: "var(--color-accent-fill)" }}
            />{" "}
            Dependency
          </span>
        </div>
      </CardContent>
    </Card>
  );
}

/** Generic titled card wrapper for the Structure tab panels (bus factor, ownership). */
export function TitledPanel({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}
