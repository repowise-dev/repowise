/**
 * The `section` view kind. Its references to other parts are checked where
 * the whole list is visible (`validateViewParts`); here only their shape.
 */

import { VIEW_LIMITS, type NarrativeBlock, type SectionView } from "@repowise-dev/types/views";

import { list, none, obj, str, type Rec, type Spec } from "../validation";
import { baseSpec, duplicates, partId } from "./base";

export const NARRATIVE_SPEC: Spec<NarrativeBlock> = {
  required: { text: str, cites: list(partId, VIEW_LIMITS.parts) },
  optional: none,
};

const base = baseSpec("section");
export const SECTION_SPEC: Spec<SectionView> = {
  required: {
    ...base.required,
    narrative: list(obj(NARRATIVE_SPEC), VIEW_LIMITS.narrative_blocks),
    view_ids: list(partId, VIEW_LIMITS.section_views),
  },
  optional: base.optional,
};

function sectionRules(v: Rec, p: string, e: string[]): void {
  const narrative = Array.isArray(v.narrative) ? v.narrative : [];
  const ids = Array.isArray(v.view_ids) ? v.view_ids : [];
  if (narrative.length === 0 && ids.length === 0) e.push(`${p}: a section needs narrative or views`);
  for (const id of new Set(duplicates(ids))) e.push(`${p}.view_ids: duplicate ${String(id)}`);
}

export const section = obj(SECTION_SPEC, sectionRules);
