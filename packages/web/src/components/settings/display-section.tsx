"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@repowise-dev/ui/ui/card";
import { Label } from "@repowise-dev/ui/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@repowise-dev/ui/ui/select";
import { DEFAULT_WEEKEND_PRESET, WEEKEND_PRESETS } from "@repowise-dev/ui/stats";
import { config } from "@/lib/config";

/** Reader-local display preferences for the stats surfaces. */
export function DisplaySection() {
  const [weekend, setWeekend] = useState(DEFAULT_WEEKEND_PRESET.id);
  // Read after mount so SSR and the first client render agree.
  useEffect(() => {
    setWeekend(config.getWeekend() || DEFAULT_WEEKEND_PRESET.id);
  }, []);

  function handleChange(v: string) {
    setWeekend(v);
    config.setWeekend(v);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Display</CardTitle>
        <CardDescription>How stats are presented in this browser.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-1.5">
        <Label>Weekend days</Label>
        <Select value={weekend} onValueChange={handleChange}>
          <SelectTrigger className="w-64">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {WEEKEND_PRESETS.map((p) => (
              <SelectItem key={p.id} value={p.id}>
                {p.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <p className="text-xs text-[var(--color-text-tertiary)]">
          Drives the &ldquo;on weekends&rdquo; share on the coding-rhythm heatmap.
        </p>
      </CardContent>
    </Card>
  );
}
