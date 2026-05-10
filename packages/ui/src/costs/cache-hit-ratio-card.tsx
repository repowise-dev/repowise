"use client";

import { Zap } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { formatCost } from "../lib/format";

export interface CacheHitRatioCardProps {
  /** Optional metric — when missing, the card renders a neutral "not yet wired" state. */
  metrics?: {
    hits: number;
    misses: number;
    estimated_savings_usd: number;
  };
}

export function CacheHitRatioCard({ metrics }: CacheHitRatioCardProps) {
  const total = metrics ? metrics.hits + metrics.misses : 0;
  const ratio = total > 0 ? (metrics!.hits / total) * 100 : 0;
  const dasharray = `${(ratio / 100) * 188.5} 188.5`;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <Zap className="h-4 w-4 text-yellow-500" />
          Cache hits & savings
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        <div className="flex items-center gap-5">
          <svg width={80} height={80} viewBox="0 0 80 80" className="shrink-0 -rotate-90">
            <circle
              cx={40}
              cy={40}
              r={30}
              fill="none"
              strokeWidth={10}
              stroke="var(--color-bg-inset)"
            />
            {metrics && (
              <circle
                cx={40}
                cy={40}
                r={30}
                fill="none"
                strokeWidth={10}
                stroke="var(--color-accent-primary)"
                strokeDasharray={dasharray}
                strokeLinecap="round"
              />
            )}
            <text
              x={40}
              y={45}
              textAnchor="middle"
              transform="rotate(90 40 40)"
              className="fill-[var(--color-text-primary)]"
              fontSize={metrics ? 16 : 11}
              fontWeight={600}
            >
              {metrics ? `${Math.round(ratio)}%` : "—"}
            </text>
          </svg>
          <div className="space-y-1 min-w-0">
            {metrics ? (
              <>
                <div className="text-xs text-[var(--color-text-secondary)]">
                  <span className="font-medium text-[var(--color-text-primary)] tabular-nums">
                    {metrics.hits.toLocaleString()}
                  </span>{" "}
                  hits / {total.toLocaleString()} calls
                </div>
                <div className="text-xs text-[var(--color-text-secondary)]">
                  Estimated savings:{" "}
                  <span className="font-medium text-green-500 tabular-nums">
                    {formatCost(metrics.estimated_savings_usd)}
                  </span>
                </div>
              </>
            ) : (
              <p className="text-xs text-[var(--color-text-tertiary)] leading-snug max-w-[260px]">
                Cache analytics not wired yet — once provider cache stats land we&apos;ll show
                hit ratio and dollar savings here.
              </p>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
