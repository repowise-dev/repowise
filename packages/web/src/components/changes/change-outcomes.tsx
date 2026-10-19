import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { OverviewSection } from "@repowise-dev/ui/overview";
import type { EvidenceItem } from "@repowise-dev/types/changes";
import { evidenceValueText } from "@/lib/changes";

type Row = Record<string, unknown>;

const LINK = "text-[var(--color-accent-primary)] hover:underline";

/** The later commit or change a link row names, as a link to it. */
function LaterRef({ r, repoId }: { r: Row; repoId: string }) {
  const sha = String(r.sha ?? "");
  const id = typeof r.change_id === "string" ? r.change_id : null;
  if (!id) {
    return (
      <Link href={`/repos/${repoId}/commits?commit=${encodeURIComponent(sha)}`} className={`font-mono ${LINK}`}>
        {sha.slice(0, 12)}
      </Link>
    );
  }
  return (
    <Link href={`/repos/${repoId}/changes/${encodeURIComponent(id)}`} className={LINK}>
      {id} <span className="font-mono">{sha.slice(0, 12)}</span>
    </Link>
  );
}

/** A revert or a later fix naming this change as its cause, in its own words. */
async function LinkRow({ r, repoId }: { r: Row; repoId: string }) {
  const t = await getTranslations("views.changes");
  return (
    <li className="space-y-0.5">
      <div className="text-[var(--color-text-primary)]">
        {r.kind === "revert" ? t("outcomesRevertedBy") : t("outcomesCause")} <LaterRef r={r} repoId={repoId} />
        {r.kind === "revert" && r.in_force === false && (
          <span className="text-[var(--color-text-tertiary)]"> ({t("outcomesNoLongerInForce")})</span>
        )}
      </div>
      <blockquote className="border-l-2 border-[var(--color-border-default)] pl-2">{String(r.evidence ?? "")}</blockquote>
    </li>
  );
}

/** Where fixes later landed among the change's files. */
async function FileRateRow({ rate }: { rate: Row }) {
  const t = await getTranslations("views.changes");
  return (
    <li>
      {t("outcomesFileRate", { fixed: Number(rate.files_fixed), total: Number(rate.files), days: Number(rate.days) })}
      {rate.window_complete === false && (
        <span className="text-[var(--color-text-tertiary)]"> ({t("outcomesWindowOpen")})</span>
      )}
    </li>
  );
}

/** What later history says about the change, in the later commits' own words. */
export async function ChangeOutcomes({ item, repoId }: { item: EvidenceItem; repoId: string }) {
  const t = await getTranslations("views.changes");
  if (item.status === "unknown") {
    return (
      <OverviewSection title={t("detailOutcomes")}>
        <p className="text-xs text-[var(--color-text-tertiary)]">{evidenceValueText(t, item)}</p>
      </OverviewSection>
    );
  }
  const rows = item.details as Row[];
  const links = rows.filter((r) => r.kind === "revert" || r.kind === "fix_reference");
  const rate = rows.find((r) => r.kind === "file_fix_rate");
  return (
    <OverviewSection title={t("detailOutcomes")} description={t("outcomesNote")}>
      <ul className="space-y-2 text-xs text-[var(--color-text-secondary)]">
        {links.length === 0 && <li>{t("outcomesNone")}</li>}
        {links.map((r) => (
          <LinkRow key={`${String(r.kind)}-${String(r.sha)}`} r={r} repoId={repoId} />
        ))}
        {rate && <FileRateRow rate={rate} />}
      </ul>
    </OverviewSection>
  );
}
