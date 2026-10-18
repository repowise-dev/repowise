import type { ReactNode } from "react";
import { getTranslations } from "next-intl/server";
import { PageLede } from "@repowise-dev/ui/shared/page-lede";
import { OverviewSection } from "@repowise-dev/ui/overview";
import { FindingsByFile } from "@repowise-dev/ui/commits/findings-by-file";
import { HealthDeltaMark } from "@repowise-dev/ui/commits/health-delta-mark";
import { betterCount, healthDeltaKind, worseCount } from "@repowise-dev/ui/commits/health-delta";
import type { ChangePromptInput } from "@repowise-dev/ui/health/ai-prompt-builder";
import type { ChangeHealthResponse } from "@repowise-dev/types/history";
import { filePromptInput, healthDeltaLabels } from "@/lib/changes";
import { ChangePromptAction } from "./change-prompt-action";

type Health = ChangeHealthResponse | null | undefined;

/**
 * The change page's lede, figure first: how many findings the change made
 * worse (or, when it only helped, how many it resolved), the dot-and-word
 * mark beside it, one reading, and the primary action when there is work to
 * hand over. A change none of whose commits was analysed says so.
 */
export async function ChangeHealthLede({ health, action }: { health: Health; action?: ReactNode }) {
  const t = await getTranslations("views.changes");
  const labels = healthDeltaLabels(t);
  if (!health) {
    return (
      <PageLede
        label={t("verdictLabel")}
        value="—"
        badge={<HealthDeltaMark delta={null} labels={labels} />}
        action={action}
      >
        <p>{t("verdictUnknown")}</p>
      </PageLede>
    );
  }
  const kind = healthDeltaKind(health);
  const better = betterCount(health);
  const worse = worseCount(health);
  const lead =
    kind === "improved"
      ? { value: better, unit: t("ledeUnitBetter", { better }), reading: t("verdictImproved", { better }) }
      : kind === "neutral"
        ? { value: 0, unit: t("ledeUnitNeutral"), reading: t("verdictNeutral") }
        : { value: worse, unit: t("ledeUnitWorse", { worse, better }), reading: t("verdictWorseReading") };
  return (
    <PageLede
      label={t("verdictLabel")}
      value={String(lead.value)}
      unit={lead.unit}
      badge={<HealthDeltaMark delta={health} labels={labels} figures={false} />}
      action={action}
    >
      <p>{lead.reading}</p>
      {health.status === "partial" && (
        <p>{t("verdictPartial", { unscanned: health.unscanned_commits })}</p>
      )}
    </PageLede>
  );
}

/**
 * "Got worse": the findings the change's commits introduced or made more
 * severe, grouped by file, each file with a quiet prompt for its findings.
 * What got better is the lede's count; a resolved finding has no location.
 */
export async function ChangeGotWorse({ health, prompt }: { health: Health; prompt: ChangePromptInput }) {
  const findings = health?.findings ?? [];
  if (!health || findings.length === 0) return null;
  const t = await getTranslations("views.changes");
  const worse = worseCount(health);
  return (
    <OverviewSection
      title={t("gotWorse")}
      description={
        findings.length < worse
          ? `${t("gotWorseDescription")} ${t("gotWorseShown", { shown: findings.length, total: worse })}`
          : t("gotWorseDescription")
      }
    >
      <FindingsByFile
        findings={findings}
        labels={{
          count: (n) => t("fileFindings", { count: n }),
          more: (n) => t("moreFiles", { count: n }),
          was: (severity) => t("findingWas", { severity }),
          indirect: t("findingIndirect"),
          moved: (from) => (from ? t("findingMovedFrom", { from }) : t("findingMoved")),
          callEdge: t("findingCallEdge"),
          unexplained: t("findingUnexplained"),
        }}
        fileAction={(group) => (
          <ChangePromptAction quiet subject={group.path} change={filePromptInput(prompt, group.path)} />
        )}
      />
    </OverviewSection>
  );
}
