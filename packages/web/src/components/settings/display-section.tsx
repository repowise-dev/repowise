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
import { Switch } from "@repowise-dev/ui/ui/switch";
import { DEFAULT_WEEKEND_PRESET, WEEKEND_PRESETS } from "@repowise-dev/ui/stats";
import {
  CHAT_DOCK_VISIBILITY_EVENT,
  config,
  setChatAskControlsHidden,
  setChatDockHidden,
  setChatSelectionAskHidden,
} from "@/lib/config";

/** The three chat switches, in the order a reader meets them: the dock first,
 *  then the two controls it gates. Each hint names what switching off removes. */
const CHAT_SWITCHES = [
  {
    key: "dock",
    label: "Ask Repowise",
    hint: "The chat pill in the bottom-right of every repository page. Hiding it also hides the two controls below, and does not affect the full chat page.",
    ariaLabel: "Show the Ask Repowise chat pill",
    read: () => !config.getChatDockHidden(),
    write: (shown: boolean) => setChatDockHidden(!shown),
  },
  {
    key: "ask",
    label: "Ask about this",
    hint: "The small chat button beside a file, symbol, finding, decision or commit. Hiding it removes those buttons; the pill and the full chat page still work.",
    ariaLabel: "Show the Ask about this buttons",
    read: () => !config.getChatAskControlsHidden(),
    write: (shown: boolean) => setChatAskControlsHidden(!shown),
  },
  {
    key: "selection",
    label: "Ask about a selection",
    hint: "The control that appears when you select text in code or documentation. Hiding it removes that control; selecting text behaves normally.",
    ariaLabel: "Show the Ask about selection control",
    read: () => !config.getChatSelectionAskHidden(),
    write: (shown: boolean) => setChatSelectionAskHidden(!shown),
  },
] as const;

type ChatSwitchKey = (typeof CHAT_SWITCHES)[number]["key"];

/** Reader-local display preferences for the stats surfaces. */
export function DisplaySection() {
  const [weekend, setWeekend] = useState(DEFAULT_WEEKEND_PRESET.id);
  const [chatShown, setChatShown] = useState<Record<ChatSwitchKey, boolean>>({
    dock: true,
    ask: true,
    selection: true,
  });
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const savedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Read after mount so SSR and the first client render agree.
  useEffect(() => {
    setWeekend(config.getWeekend() || DEFAULT_WEEKEND_PRESET.id);
  }, []);

  // The same two listeners the affordances themselves use: `storage` covers
  // another tab, the custom event covers this one. Without them these switches
  // would keep showing a choice another surface has already changed.
  useEffect(() => {
    const sync = () =>
      setChatShown({
        dock: CHAT_SWITCHES[0].read(),
        ask: CHAT_SWITCHES[1].read(),
        selection: CHAT_SWITCHES[2].read(),
      });
    sync();
    window.addEventListener(CHAT_DOCK_VISIBILITY_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(CHAT_DOCK_VISIBILITY_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);

  useEffect(
    () => () => {
      if (savedTimer.current) clearTimeout(savedTimer.current);
    },
    [],
  );

  function markSaved() {
    setSaveState("saved");
    if (savedTimer.current) clearTimeout(savedTimer.current);
    savedTimer.current = setTimeout(() => setSaveState("idle"), 2000);
  }

  function handleChange(v: string) {
    setWeekend(v);
    config.setWeekend(v);
    markSaved();
  }

  function handleChatChange(
    entry: (typeof CHAT_SWITCHES)[number],
    shown: boolean,
  ) {
    // Goes through the helper, not `config` directly: the affordances are
    // mounted on a different route and need the event to notice. The event
    // also drives this component's own sync, so there is no optimistic state
    // that could drift from storage.
    entry.write(shown);
    markSaved();
  }

  return (
    <OverviewSection
      title="Display"
      description="What this browser shows and how it presents it. Nothing here changes the index."
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
        {CHAT_SWITCHES.map((entry) => {
          // The dock gates the other two, so their switches cannot act while it
          // is hidden. Say why rather than leaving a live-looking control.
          const gated = entry.key !== "dock" && !chatShown.dock;
          return (
            <SettingsRow
              key={entry.key}
              label={entry.label}
              hint={
                gated
                  ? `${entry.hint} Unavailable while Ask Repowise is hidden.`
                  : entry.hint
              }
            >
              <Switch
                checked={chatShown[entry.key] && !gated}
                disabled={gated}
                onCheckedChange={(shown) => handleChatChange(entry, shown)}
                aria-label={entry.ariaLabel}
              />
            </SettingsRow>
          );
        })}
      </SettingsRows>
    </OverviewSection>
  );
}
