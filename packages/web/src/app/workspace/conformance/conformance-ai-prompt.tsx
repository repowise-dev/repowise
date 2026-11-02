"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import {
  AiPromptButton,
  AiPromptModal,
  buildConformanceAiPrompt,
} from "@repowise-dev/ui/health";
import type { ConformanceViolation } from "@repowise-dev/api-client/types";

/**
 * The one piece of state on this page: whether the prompt dialog is open.
 *
 * Kept in its own boundary so the report itself stays server-rendered — the
 * page used to be a client component in full, which put a hydration boundary
 * above every figure on it for the sake of one modal.
 */
export function ConformanceAiPrompt({
  violations,
}: {
  violations: ConformanceViolation[];
}) {
  const [open, setOpen] = useState(false);
  const t = useTranslations("conformance");

  return (
    <>
      <AiPromptButton label={t("aiPrompt.label")} onClick={() => setOpen(true)} />
      <AiPromptModal
        open={open}
        onOpenChange={setOpen}
        getPrompt={(flavor) =>
          buildConformanceAiPrompt({
            violations: violations.map((v) => ({
              source: v.source,
              target: v.target,
              source_name: v.source_name,
              target_name: v.target_name,
              edge_kind: v.edge_kind,
              rule_source: v.rule_source,
              rule_target: v.rule_target,
              rule_description: v.rule_description,
            })),
            flavor,
          })
        }
        title={t("aiPrompt.title")}
        description={t("aiPrompt.description")}
      />
    </>
  );
}
