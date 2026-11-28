import type { Metadata } from "next";
import type { ReactNode } from "react";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import { getRepo } from "@/lib/api/repos";
import { SettingsFrame } from "@/components/settings/settings-frame";
import { REPO_SETTINGS_GROUPS, repoSettingsBase } from "@/components/settings/settings-groups";

interface Props {
  params: Promise<{ id: string }>;
  children: ReactNode;
}

export async function generateMetadata({ params }: Omit<Props, "children">): Promise<Metadata> {
  const { id } = await params;
  try {
    const repo = await getRepo(id);
    return { title: `${repo.name} · Settings` };
  } catch {
    return { title: "Settings" };
  }
}

/**
 * Per-repository settings: one group per sub-page, under the same group
 * names as global settings. Options shared by every repository live in global
 * settings, and these pages link there instead of repeating them.
 */
export default async function RepoSettingsLayout({ params, children }: Props) {
  const { id } = await params;
  const t = await getTranslations("views.repoSettings");
  let repo;
  try {
    repo = await getRepo(id);
  } catch {
    notFound();
  }
  return (
    <SettingsFrame
      title={t("title")}
      description={t("description", { repo: repo.name })}
      base={repoSettingsBase(id)}
      groups={REPO_SETTINGS_GROUPS}
    >
      {children}
    </SettingsFrame>
  );
}
