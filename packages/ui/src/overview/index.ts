/**
 * Overview page vocabulary.
 *
 * A small set of section-shaped primitives, kept here rather than in the web
 * app so downstream consumers get them unchanged.
 *
 * The rules they encode: group with hairlines and vertical rhythm instead of
 * cards, spend colour on one accent plus the health bands and nothing else,
 * keep a wide type scale so hierarchy does not depend on borders, and put a
 * sentence next to every figure — a number with no frame is not information.
 */

export { OverviewSection, SectionLink } from "./section";
export type { OverviewSectionProps } from "./section";

export { RepoIdentityHeader } from "./repo-identity-header";
export type { RepoIdentityHeaderProps, RepoIdentityMeta } from "./repo-identity-header";

export { RepoAvatar, githubOwnerFromRemote } from "./repo-avatar";
export type { RepoAvatarProps } from "./repo-avatar";

export { HealthLede, healthBand } from "./health-lede";
export type { HealthLedeProps } from "./health-lede";

export { ReadsColumn } from "./reads-column";
export type { ReadItem, ReadBarSegment } from "./reads-column";

export { ChangeLine } from "./change-line";
export type { ChangeLineProps, ChangeStat } from "./change-line";

export { CommitRows, DecisionRows } from "./activity-lists";
export type { CommitRow, DecisionRow } from "./activity-lists";

export { AttentionRows } from "./attention-rows";
export type { AttentionRowItem } from "./attention-rows";

export { HotspotTable } from "./hotspot-table";
export { ExploreList } from "./explore-list";
export type { ExploreEntry } from "./explore-list";
export { LanguageBar } from "./language-bar";
