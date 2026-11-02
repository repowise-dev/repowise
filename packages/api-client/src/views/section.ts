/**
 * The `section` view kind. The parts it names are checked where the whole
 * list is visible (`validateViewParts`, through `references`); here only
 * their shape.
 */

import { VIEW_LIMITS, type NarrativeBlock, type SectionView } from "@repowise-dev/types/views";

import { duplicates, list, none, obj, str, type Rec, type Spec } from "../validation";
import { baseSpec, partId, type ViewKindEntry } from "./base";

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
  for (const id of duplicates(ids)) e.push(`${p}.view_ids: duplicate ${String(id)}`);
}

export const SECTION_KIND: ViewKindEntry<"section"> = {
  check: obj(SECTION_SPEC, sectionRules),
  isData: false,
  references: (part) => [
    ...part.view_ids.map((id, j) => [id, `.view_ids[${j}]`] as const),
    ...part.narrative.flatMap((block, j) =>
      block.cites.map((id, k) => [id, `.narrative[${j}].cites[${k}]`] as const),
    ),
  ],
};
