"use client";

import { useEffect, useRef, useState } from "react";
import { OverviewSection } from "@repowise-dev/ui/overview";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@repowise-dev/ui/ui/select";
import {
  SettingsRow,
  SettingsRows,
  SaveIndicator,
  type SaveState,
} from "@repowise-dev/ui/settings";
import { DEFAULT_WEEKEND_PRESET, WEEKEND_PRESETS } from "@repowise-dev/ui/stats";
import { config } from "@/lib/config";

/** Reader-local display preferences for the stats surfaces. */
export function DisplaySection() {
  const [weekend, setWeekend] = useState(DEFAULT_WEEKEND_PRESET.id);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const savedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Read after mount so SSR and the first client render agree.
  useEffect(() => {
    setWeekend(config.getWeekend() || DEFAULT_WEEKEND_PRESET.id);
  }, []);

  useEffect(
    () => () => {
      if (savedTimer.current) clearTimeout(savedTimer.current);
    },
    [],
  );

  function handleChange(v: string) {
    setWeekend(v);
    config.setWeekend(v);
    setSaveState("saved");
    if (savedTimer.current) clearTimeout(savedTimer.current);
    savedTimer.current = setTimeout(() => setSaveState("idle"), 2000);
  }

  return (
    <OverviewSection
      title="Display"
      description="How stats are presented in this browser. Nothing here changes the index."
      action={<SaveIndicator state={saveState} />}
    >
      <SettingsRows>
        <SettingsRow
          label="Weekend days"
          hint="Drives the “on weekends” share on the coding-rhythm heatmap."
        >
          <Select value={weekend} onValueChange={handleChange}>
            <SelectTrigger className="w-full sm:w-64">
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
        </SettingsRow>
      </SettingsRows>
    </OverviewSection>
  );
}
