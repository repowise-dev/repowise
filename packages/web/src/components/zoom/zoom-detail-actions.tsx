"use client";

/**
 * The detail sheet's action row: read the node's docs, open its file page,
 * ask chat about it, copy a link that reopens the map on it. Each action shows
 * only when its destination exists for this node (a docs page, a file path,
 * a chat host), so the row never carries a button that leads nowhere. Folders
 * have no Files destination: `/files` takes no path, and a file's own page is
 * the one entity route.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { BookOpen, Check, FileCode, Link2, MessageCircleQuestion } from "lucide-react";
import { useTranslations } from "next-intl";
import { useChatHandoff } from "@repowise-dev/ui/chat";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import type { ZoomNode } from "@repowise-dev/ui/zoom";
import { pageHref } from "@/lib/utils/page-href";

const ACTION =
  "inline-flex items-center gap-1.5 rounded-md border border-[var(--color-border-default)] px-2.5 py-1.5 text-[12px] font-medium text-[var(--color-text-primary)] hover:bg-[var(--color-bg-wash-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]";

/** This page's URL focused on `id`, the form the map restores on load. */
function focusLink(id: string): string {
  const url = new URL(window.location.href);
  url.searchParams.set("focus", id);
  return url.toString();
}

export function ZoomDetailActions({ node, repoId }: { node: ZoomNode; repoId: string }) {
  const t = useTranslations("zoom.detail");
  const { request, askEnabled } = useChatHandoff();
  const isFile = node.kind === "file";

  const ask = () =>
    request({
      context: {
        kind: "graph",
        label: node.name,
        ...(node.path ? { target: node.path, targetKind: "path" as const } : {}),
      },
      question: t("askQuestion", { name: node.name }),
    });

  return (
    <div className="flex flex-wrap gap-1.5">
      {/* This card is named after a docs page, so that page is reachable from it. */}
      {node.page_id && (
        <Link href={pageHref(repoId, node.page_id)} className={ACTION}>
          <BookOpen className="h-3.5 w-3.5" aria-hidden />
          {t("openDocs")}
        </Link>
      )}
      {isFile && node.path && (
        <Link href={fileEntityPath(`/repos/${repoId}`, node.path)} className={ACTION}>
          <FileCode className="h-3.5 w-3.5" aria-hidden />
          {t("openFile")}
        </Link>
      )}
      {askEnabled && (
        <button type="button" onClick={ask} className={ACTION}>
          <MessageCircleQuestion className="h-3.5 w-3.5" aria-hidden />
          {t("ask")}
        </button>
      )}
      <CopyLinkButton nodeId={node.id} />
    </div>
  );
}

/** Copies a link that reopens the map on this node; the confirmation is announced politely. */
function CopyLinkButton({ nodeId }: { nodeId: string }) {
  const t = useTranslations("zoom.detail");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(timer);
  }, [copied]);
  // A new node starts with a fresh Copy link, not the last one's tick.
  useEffect(() => setCopied(false), [nodeId]);

  const copy = async () => {
    // `navigator.clipboard` is absent on an insecure origin (plain http on a LAN host).
    try {
      await navigator.clipboard.writeText(focusLink(nodeId));
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  return (
    <>
      <button type="button" onClick={copy} className={ACTION}>
        {copied ? (
          <Check className="h-3.5 w-3.5 text-[var(--color-success)]" aria-hidden />
        ) : (
          <Link2 className="h-3.5 w-3.5" aria-hidden />
        )}
        {copied ? t("linkCopied") : t("copyLink")}
      </button>
      <span role="status" className="sr-only">
        {copied ? t("linkCopied") : ""}
      </span>
    </>
  );
}
