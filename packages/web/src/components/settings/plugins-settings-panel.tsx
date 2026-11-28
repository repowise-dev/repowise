"use client";

import useSWR from "swr";
import { useTranslations } from "next-intl";
import { PluginsPanel, type PluginsPanelLabels } from "@repowise-dev/ui/settings";
import { Button } from "@repowise-dev/ui/ui/button";
import { getPlugins, syncSources } from "@/lib/api/plugins";
import { HostedNudgeSlot } from "@/components/hosted/hosted-nudge-slot";
import { repoSettingsHref } from "./settings-groups";

export const PLUGIN_GUIDE_HREF =
  "https://github.com/repowise-dev/repowise/blob/main/docs/reference/PLUGINS.md";

/** How often the list is read again while a sync started here runs. */
const SYNC_POLL_MS = 3000;

function usePluginsPanelLabels(): Partial<PluginsPanelLabels> {
  const t = useTranslations("settings.plugins");
  return {
    sourcesTitle: t("sourcesTitle"),
    pluginsTitle: t("pluginsTitle"),
    ready: t("ready"),
    failed: t("failed"),
    notConfigured: t("notConfigured"),
    status: (status) => (t.has(`status.${status}`) ? t(`status.${status}`) : status),
    settingsOf: (plugin) => t("settingsOf", { plugin }),
    set: t("set"),
    notSet: t("notSet"),
    secret: (set) => (set ? t("secretSet") : t("secretNotSet")),
    builtIn: t("builtIn"),
    fromPlugin: (plugin) => t("fromPlugin", { plugin }),
    neverSynced: t("neverSynced"),
    stored: (batches) => t("stored", { batches }),
    syncState: (state) => (t.has(`syncStates.${state}`) ? t(`syncStates.${state}`) : state),
    syncedAt: t("syncedAt"),
    syncing: t("syncing"),
    syncNow: t("syncNow"),
    syncNowAria: (source) => t("syncNowAria", { source }),
    syncFailed: t("syncFailed"),
    emptyTitle: t("emptyTitle"),
    emptyBody: t("emptyBody"),
    guide: t("guide"),
    emptyBuild: t("emptyBuild"),
  };
}

/** Installed plugins and their sources for one repository, with Sync now. */
export function PluginsSettingsPanel({ repoId }: { repoId: string }) {
  const t = useTranslations("settings.plugins");
  const labels = usePluginsPanelLabels();
  const { data, error, isLoading, mutate } = useSWR(["plugins", repoId], () => getPlugins(repoId), {
    // Follow a running sync until it ends; idle, read once.
    refreshInterval: (latest) => ((latest?.syncing?.length ?? 0) > 0 ? SYNC_POLL_MS : 0),
  });

  if (error) {
    return (
      <div className="flex flex-col items-start gap-2 text-sm text-[var(--color-text-secondary)]">
        <p>{error instanceof Error && error.message ? error.message : t("loadFailed")}</p>
        <Button variant="outline" size="sm" onClick={() => void mutate()}>
          {t("retry")}
        </Button>
      </div>
    );
  }
  if (isLoading || !data) {
    return <p className="text-sm text-[var(--color-text-tertiary)]">{t("loading")}</p>;
  }

  return (
    <PluginsPanel
      data={data}
      onSync={async (source) => {
        await syncSources(repoId, source);
        await mutate();
      }}
      settingHref={(key) => `${repoSettingsHref(repoId, "all")}?q=${encodeURIComponent(key)}`}
      guideHref={PLUGIN_GUIDE_HREF}
      emptyExtra={
        <HostedNudgeSlot candidates={["plugins"]} text={t("hostedIntegrations")} action={null} />
      }
      labels={labels}
    />
  );
}
