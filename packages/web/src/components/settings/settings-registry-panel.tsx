"use client";

import useSWR from "swr";
import { useTranslations } from "next-intl";
import {
  SettingsRegistryPage,
  type RegistryLink,
  type RegistrySetting,
  type SettingsRegistryLabels,
} from "@repowise-dev/ui/settings";
import { Button } from "@repowise-dev/ui/ui/button";
import { getSettings, resetSetting, setSetting } from "@/lib/api/settings";
import { repoSettingsHref, type SettingsGroupId } from "./settings-groups";

/** The settings sub-page that has a richer editor for *key*, if one does. */
function ownerGroup(setting: RegistrySetting): SettingsGroupId | null {
  const { key, area } = setting;
  if (area === "providers" || key === "provider" || key === "model") return "models";
  if (key.startsWith("refactoring.") || area === "decisions") return "models";
  if (key.startsWith("coverage.")) return "coverage";
  if (key === "mcp.tools") return "agents";
  return null;
}

function useSettingsRegistryLabels(areas: string[]): Partial<SettingsRegistryLabels> {
  const t = useTranslations("settings.registry");
  return {
    search: t("search"),
    searchPlaceholder: t("searchPlaceholder"),
    changedOnly: t("changedOnly"),
    showInternal: t("showInternal"),
    count: (shown, total) => (shown === total ? t("countAll", { total }) : t("count", { shown, total })),
    noMatches: t("noMatches"),
    suggestionsTitle: t("suggestionsTitle"),
    suggestionsDescription: t("suggestionsDescription"),
    apply: t("apply"),
    applyAria: (key) => t("applyAria", { key }),
    suggested: (value) => t("suggested", { value }),
    changed: t("changed"),
    defaultValue: (value) => t("defaultValue", { value }),
    sources: {
      default: t("sources.default"),
      env: t("sources.env"),
      repo_file: t("sources.repo_file"),
      user_file: t("sources.user_file"),
    },
    envOverride: (name) => t("envOverride", { name }),
    envOnly: (name) => t("envOnly", { name }),
    userSetting: (key) => t("userSetting", { key }),
    lockedReason: t("lockedReason"),
    reset: t("reset"),
    resetAria: (key) => t("resetAria", { key }),
    copyPrompt: t("copyPrompt"),
    promptTitle: (key) => t("promptTitle", { key }),
    promptDescription: t("promptDescription"),
    saveFailed: t("saveFailed"),
    unset: t("unset"),
    none: t("none"),
    listPlaceholder: t("listPlaceholder"),
    // An area added to the registry before its translation shows its name.
    areaTitles: Object.fromEntries(
      areas.filter((a) => t.has(`areas.${a}`)).map((a) => [a, t(`areas.${a}`)]),
    ),
  };
}

/** Every setting for one repository, fetched and written through the settings API. */
export function SettingsRegistryPanel({ repoId, repoName }: { repoId: string; repoName: string }) {
  const t = useTranslations("settings.registry");
  const tg = useTranslations("settings.groups");
  const { data, error, isLoading, mutate } = useSWR(["settings-registry", repoId], () =>
    // Internal settings come along so the page's "show internal" needs no refetch.
    getSettings(repoId, { includeInternal: true }),
  );
  const labels = useSettingsRegistryLabels(data?.areas.map((a) => a.area) ?? []);

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

  const linkFor = (setting: RegistrySetting): RegistryLink | null => {
    const group = ownerGroup(setting);
    if (!group) return null;
    return {
      href: repoSettingsHref(repoId, group),
      label: t("moreOn", { page: tg(`${group}.title`) }),
    };
  };

  return (
    <SettingsRegistryPage
      areas={data.areas}
      suggestions={data.suggestions ?? []}
      repoName={repoName}
      onSet={(key, value) => setSetting(repoId, key, value)}
      onReset={(key) => resetSetting(repoId, key)}
      linkFor={linkFor}
      labels={labels}
    />
  );
}
