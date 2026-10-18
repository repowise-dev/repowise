import type { ReactNode } from "react";
import { getTranslations } from "next-intl/server";
import { OverviewSection } from "@repowise-dev/ui/overview";
import { FindingsByFile } from "@repowise-dev/ui/commits/findings-by-file";
import { HealthDeltaLede } from "@repowise-dev/ui/commits/health-delta-lede";
import { worseCount } from "@repowise-dev/ui/commits/health-delta";
import type { ChangePromptInput } from "@repowise-dev/ui/health/ai-prompt-builder";
import type { ChangeHealthResponse } from "@repowise-dev/types/history";
import { filePromptInput, healthDeltaLabels } from "@/lib/changes";
import { ChangePromptAction } from "./change-prompt-action";

type Health = ChangeHealthResponse | null | undefined;

/**
 * The change page's lede, figure first: how many findings the change made
 * worse (or, when it only helped, how many it resolved), the dot-and-word
 * mark beside it, one reading, and the primary action when there is work to
 * hand over. A change none of whose commits was analysed says so. The shared
 * `HealthDeltaLede` draws it; the commit sheet uses the same one.
 */
export async function ChangeHealthLede({ health, action }: { health: Health; action?: ReactNode }) {
  const t = await getTranslations("views.changes");
  return (
    <HealthDeltaLede
      delta={health ?? null}
      markLabels={healthDeltaLabels(t)}
      action={action}
      copy={{
        label: t("verdictLabel"),
        unitWorse: (worse, better) => t("ledeUnitWorse", { worse, better }),
        unitBetter: (better) => t("ledeUnitBetter", { better }),
        unitNeutral: t("ledeUnitNeutral"),
        worse: t("verdictWorseReading"),
        improved: (better) => t("verdictImproved", { better }),
        neutral: t("verdictNeutral"),
        unknown: t("verdictUnknown"),
      }}
    >
      {health?.status === "partial" && (
        <p>{t("verdictPartial", { unscanned: health.unscanned_commits })}</p>
      )}
    </HealthDeltaLede>
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
