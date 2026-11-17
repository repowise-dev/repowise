import type { Metadata } from "next";
import type { ReactNode } from "react";
import { getTranslations } from "next-intl/server";
import { SettingsFrame } from "@/components/settings/settings-frame";
import { GLOBAL_SETTINGS_BASE, GLOBAL_SETTINGS_GROUPS } from "@/components/settings/settings-groups";

export const metadata: Metadata = { title: "Settings" };

/** Global settings: one group per sub-page, everything autosaves. */
export default async function SettingsLayout({ children }: { children: ReactNode }) {
  const t = await getTranslations("views.globalSettings");
  return (
    <SettingsFrame
      title={t("title")}
      description={t("description")}
      base={GLOBAL_SETTINGS_BASE}
      groups={GLOBAL_SETTINGS_GROUPS}
    >
      {children}
    </SettingsFrame>
  );
}
