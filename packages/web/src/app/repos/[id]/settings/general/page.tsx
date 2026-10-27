import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import { SettingsRow, SettingsRows } from "@repowise-dev/ui/settings";
import { getRepo } from "@/lib/api/repos";
import { RepoSettingsFormWrapper as RepoSettingsForm } from "@/components/repos/repo-settings-form-wrapper";
import { DeleteRepoButton } from "@/components/repos/delete-repo-button";
import { PublishRow } from "@/components/hosted/publish";
import { SettingsGroup } from "@/components/settings/settings-groups";

interface Props {
  params: Promise<{ id: string }>;
}

/** Name, branch, what to skip, the docs voice, publishing, then deletion. */
export default async function RepoGeneralSettingsPage({ params }: Props) {
  const { id } = await params;
  const t = await getTranslations("views.repoSettings");
  const tg = await getTranslations("settings.groups");
  let repo;
  try {
    repo = await getRepo(id);
  } catch {
    notFound();
  }

  return (
    <div className="flex flex-col gap-10">
      <SettingsGroup title={tg("general.title")} description={t("generalDescription")}>
        <RepoSettingsForm repo={repo} />
        <SettingsRows className="border-t border-[var(--color-border-default)]">
          <PublishRow repoId={id} />
        </SettingsRows>
      </SettingsGroup>

      {/* Not a tinted card. A destructive action earns emphasis on the verb and
          the sentence, not on a coloured ground the eye reads before either. */}
      <section aria-labelledby="danger-zone" className="flex flex-col gap-2">
        <h2 id="danger-zone" className="text-base font-semibold text-[var(--color-error)]">
          {t("dangerTitle")}
        </h2>
        <SettingsRows className="border-t border-[var(--color-border-default)]">
          <SettingsRow label={t("deleteTitle")} hint={t("deleteDescription")}>
            <div className="flex sm:justify-end">
              <DeleteRepoButton repoId={id} repoName={repo.name} variant="button" redirectTo="/" />
            </div>
          </SettingsRow>
        </SettingsRows>
      </section>
    </div>
  );
}
