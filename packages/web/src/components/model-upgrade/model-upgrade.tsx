"use client";

import { useMemo, useRef, useState } from "react";
import useSWR, { useSWRConfig } from "swr";
import { toast } from "sonner";
import { useTranslations } from "next-intl";
import {
  ModelUpgradeSheet,
  type ModelUpgradeSheetLabels,
} from "@repowise-dev/ui/settings/model-upgrade-sheet";
import {
  defaultModelSource,
  orderModelSources,
  pasteKeyProviders,
  type ModelSource,
} from "@repowise-dev/ui/settings";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import { BulkGenerateConfirm } from "@/components/docs/bulk-generate-confirm";
import { BulkGenerateProgress } from "@/components/docs/bulk-generate-progress";
import { useProviderFormLabels } from "@/components/settings/use-provider-form-labels";
import { addProviderKey, getProviders, setActiveProvider } from "@/lib/api/providers";
import { generateEstimate } from "@/lib/api/repos";
import { formatEstimateCost } from "@/lib/generate-format";
import { hostedLink } from "@/lib/hosted";
import { useBulkGenerate } from "@/lib/hooks/use-bulk-generate";

/** The same pages the docs header's bulk action writes. */
const UNWRITTEN = { selection: { kind: "unwritten" as const }, cascade: "none" as const };

function useSheetLabels(): Partial<ModelUpgradeSheetLabels> {
  const t = useTranslations("modelUpgrade");
  return useMemo(
    () => ({
      eyebrow: t("eyebrow"),
      title: t("title"),
      description: t("description"),
      close: t("close"),
      sourcesLabel: t("sourcesLabel"),
      cliSource: (name) => t("cliSource", { name }),
      keySource: (name) => t("keySource", { name }),
      localSource: (name) => t("localSource", { name }),
      pasteSource: t("pasteSource"),
      hostedSource: t("hostedSource"),
      hostedNote: t("hostedNote"),
      hostedAction: t("hostedAction"),
      detected: {
        signed_in: t("detected.signedIn"),
        env: t("detected.env"),
        saved: t("detected.saved"),
      },
      pasteProvider: t("pasteProvider"),
      pages: (count) => t("pages", { count }),
      counting: t("counting"),
      signedInNote: (name) => t("signedInNote", { name }),
      costNote: (cost) => t("costNote", { cost }),
      pricing: t("pricing"),
      keyNote: t("keyNote"),
      localNote: t("localNote"),
      pasteNote: t("pasteNote"),
      write: t("write"),
      sameAs: t("sameAs"),
    }),
    [t],
  );
}

/** Every cached providers list (Settings, chat, this sheet) after a change. */
function useRefreshProviders() {
  const { mutate } = useSWRConfig();
  return () =>
    mutate(
      (key) =>
        key === "/api/providers" ||
        (Array.isArray(key) && key[0] === "/api/providers") ||
        (typeof key === "string" && key.startsWith("providers:")),
    );
}

/** Sources detected on this machine, fetched only while the sheet is open:
 *  detection asks each agent CLI whether it is signed in. A failed fetch
 *  still offers "Paste a key" and hosted. */
function useModelSources(repoId: string, open: boolean) {
  const { data, isLoading } = useSWR(
    open ? ["/api/providers", repoId, "detect"] : null,
    () => getProviders(repoId, { detect: true }),
    { revalidateOnFocus: false },
  );
  const providers = useMemo(() => data?.providers ?? [], [data]);
  const sources = useMemo(() => orderModelSources(providers), [providers]);
  return {
    sources,
    keyProviders: useMemo(() => pasteKeyProviders(providers), [providers]),
    activeProvider: data?.active.provider ?? null,
    loading: isLoading,
  };
}

/** Page count, priced with the selected key's provider when there is one.
 *  The page count does not depend on the provider, so the last one counted
 *  stays up while another source is priced. */
function useSheetEstimate(repoId: string, open: boolean, source: ModelSource | undefined) {
  const priced = source?.kind === "key" ? source.providerId : null;
  const { data, isLoading } = useSWR(
    open ? ["model-upgrade-estimate", repoId, priced] : null,
    () => generateEstimate(repoId, UNWRITTEN, priced ? { provider: priced } : {}),
    { revalidateOnFocus: false },
  );
  // Per open: a count from an earlier open may be out of date.
  const lastPages = useRef<number | null>(null);
  if (!open) lastPages.current = null;
  else if (data) lastPages.current = data.total_pages;
  // The request named the provider, so a priced estimate is that provider's.
  return {
    pages: lastPages.current,
    costText: priced != null && data ? formatEstimateCost(data.estimate) : null,
    loading: isLoading,
  };
}

/**
 * The model upgrade sheet for one repo, with the run it starts. "Write the
 * docs" makes the chosen source the repo's provider (its
 * `.repowise/config.yaml`, which the CLI reads too), then runs the docs
 * header's bulk generate flow: a key source confirms its price first, a
 * sign-in or local model starts right away. The confirm and progress dialogs
 * stay mounted after the sheet closes.
 */
export function ModelUpgrade({
  repoId,
  open,
  onOpenChange,
  onGenerated,
}: {
  repoId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Called once a run completes so the host can refresh what it shows. */
  onGenerated?: () => void;
}) {
  const ts = useTranslations("settings");
  const t = useTranslations("modelUpgrade");
  const labels = useSheetLabels();
  const keyLabels = useProviderFormLabels(true);
  const refreshProviders = useRefreshProviders();
  const bulk = useBulkGenerate(repoId);
  const { sources, keyProviders, activeProvider, loading } = useModelSources(repoId, open);

  // null until the user picks: the default follows the detected sources.
  const [picked, setPicked] = useState<string | null>(null);
  const selectedId = picked ?? defaultModelSource(sources, activeProvider);
  const selected = sources.find((s) => s.id === selectedId);
  const estimate = useSheetEstimate(repoId, open, selected);
  const [writing, setWriting] = useState(false);

  function changeOpen(next: boolean) {
    if (!next) setPicked(null);
    onOpenChange(next);
  }

  async function addKey(providerId: string, key: string) {
    try {
      await addProviderKey(providerId, key, repoId);
    } catch (e) {
      toast.error(ts("providerPanel.keySaveFailed"), { description: toFriendlyMessage(e) });
      throw e;
    }
    await refreshProviders();
    setPicked(providerId);
  }

  async function write() {
    const source = selected;
    const providerId = source?.providerId;
    if (!source || !providerId) return;
    setWriting(true);
    try {
      // The repo's current provider keeps its chosen model.
      if (providerId !== activeProvider) {
        await setActiveProvider(providerId, undefined, repoId);
        await refreshProviders();
      }
      changeOpen(false);
      if (source.kind === "key") bulk.open();
      else await bulk.confirm();
    } catch (e) {
      toast.error(t("startFailed"), { description: toFriendlyMessage(e) });
    } finally {
      setWriting(false);
    }
  }

  return (
    <>
      <ModelUpgradeSheet
        open={open}
        onOpenChange={changeOpen}
        sources={sources}
        loading={loading}
        selectedId={selectedId}
        onSelect={setPicked}
        keyProviders={keyProviders}
        onAddKey={addKey}
        estimate={estimate}
        onWrite={() => void write()}
        writing={writing}
        hostedHref={hostedLink("model_sheet", "keys")}
        labels={labels}
        keyLabels={keyLabels}
      />
      <BulkGenerateConfirm flow={bulk} repoId={repoId} title={t("title")} />
      <BulkGenerateProgress flow={bulk} {...(onGenerated ? { onGenerated } : {})} />
    </>
  );
}

/**
 * Open the model upgrade sheet from anywhere in a repo: render `sheet` once
 * and call `openSheet` from a button. Nothing is fetched until it opens.
 */
export function useModelUpgrade(repoId: string, options: { onGenerated?: () => void } = {}) {
  const [open, setOpen] = useState(false);
  const sheet = (
    <ModelUpgrade
      repoId={repoId}
      open={open}
      onOpenChange={setOpen}
      {...(options.onGenerated ? { onGenerated: options.onGenerated } : {})}
    />
  );
  return { openSheet: () => setOpen(true), sheet };
}
