"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
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

/** Keys of the three chat switches, in the order a reader meets them: the dock
 *  first, then the two controls it gates. The copy lives under
 *  `settings.display.chat*` and is read inside the component, so the entries
 *  follow the active locale instead of being frozen at module load. */
const CHAT_SWITCH_KEYS = ["dock", "ask", "selection"] as const;

type ChatSwitchKey = (typeof CHAT_SWITCH_KEYS)[number];

/** Storage behaviour per switch: how each reads and writes its hidden flag. */
const CHAT_SWITCH_BEHAVIOUR: Record<
  ChatSwitchKey,
  { read: () => boolean; write: (shown: boolean) => void }
> = {
  dock: {
    read: () => !config.getChatDockHidden(),
    write: (shown) => setChatDockHidden(!shown),
  },
  ask: {
    read: () => !config.getChatAskControlsHidden(),
    write: (shown) => setChatAskControlsHidden(!shown),
  },
  selection: {
    read: () => !config.getChatSelectionAskHidden(),
    write: (shown) => setChatSelectionAskHidden(!shown),
  },
};

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
  const t = useTranslations("settings");
  /** The three switches, with their copy taken from the active locale. */
  const chatSwitches: {
    key: ChatSwitchKey;
    label: string;
    hint: string;
    ariaLabel: string;
    read: () => boolean;
    write: (shown: boolean) => void;
  }[] = [
    {
      key: "dock",
      label: t("display.chatDockLabel"),
      hint: t("display.chatDockHint"),
      ariaLabel: t("display.chatDockAria"),
      ...CHAT_SWITCH_BEHAVIOUR.dock,
    },
    {
      key: "ask",
      label: t("display.chatAskLabel"),
      hint: t("display.chatAskHint"),
      ariaLabel: t("display.chatAskAria"),
      ...CHAT_SWITCH_BEHAVIOUR.ask,
    },
    {
      key: "selection",
      label: t("display.chatSelectionLabel"),
      hint: t("display.chatSelectionHint"),
      ariaLabel: t("display.chatSelectionAria"),
      ...CHAT_SWITCH_BEHAVIOUR.selection,
    },
  ];

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
        dock: CHAT_SWITCH_BEHAVIOUR.dock.read(),
        ask: CHAT_SWITCH_BEHAVIOUR.ask.read(),
        selection: CHAT_SWITCH_BEHAVIOUR.selection.read(),
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
    entry: { key: ChatSwitchKey; write: (shown: boolean) => void },
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
      title={t("display.title")}
      description={t("display.description")}
      action={<SaveIndicator state={saveState} />}
    >
      <SettingsRows>
        <SettingsRow
          label={t("display.weekendLabel")}
          hint={t("display.weekendHint")}
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
        {chatSwitches.map((entry) => {
          // The dock gates the other two, so their switches cannot act while it
          // is hidden. Say why rather than leaving a live-looking control.
          const gated = entry.key !== "dock" && !chatShown.dock;
          return (
            <SettingsRow
              key={entry.key}
              label={entry.label}
              hint={
                gated
                  ? t("display.chatGatedHint", { hint: entry.hint })
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
