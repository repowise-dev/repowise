import type { ReactNode } from "react";
import { getTranslations } from "next-intl/server";
import { PageFrame, PAGE_SHELL_DESCRIPTION, PAGE_SHELL_TITLE } from "@repowise-dev/ui/shared/page-shell";
import { settingsGroupTitles, type SettingsGroupId } from "./settings-groups";
import { SettingsNav } from "./settings-nav";

/**
 * The frame every settings sub-page shares: the page title, the rail of
 * groups, and one group's rows beside it. Rows keep a form's measure even
 * though the frame is wider, so a control stays near its label.
 */
export async function SettingsFrame({
  title,
  description,
  base,
  groups,
  children,
}: {
  title: string;
  description: string;
  base: string;
  groups: readonly SettingsGroupId[];
  children: ReactNode;
}) {
  const tg = await getTranslations("settings.groups");
  return (
    <PageFrame maxWidth="default">
      <header className="min-w-0 space-y-1">
        <h1 className={PAGE_SHELL_TITLE}>{title}</h1>
        <p className={`${PAGE_SHELL_DESCRIPTION} [text-wrap:pretty]`}>{description}</p>
      </header>
      <div className="flex flex-col gap-6 lg:flex-row lg:gap-10">
        <SettingsNav base={base} groups={groups} titles={settingsGroupTitles(tg)} label={tg("navLabel")} />
        <div className="min-w-0 max-w-3xl flex-1">{children}</div>
      </div>
    </PageFrame>
  );
}
