/** Change ids in shareable links, kept apart from the view so a light surface (the Overview nudge) can build one. */

const SHA = /^[0-9a-f]{40}$/;
/**
 * A merge or pull request's id and link value alike: `mr:12` or `pr:12`, or
 * pinned to its two commits, `mr:12@<base sha>...<head sha>`. `:` never
 * appears in a branch name.
 */
const CHANGE_REQUEST = /^(mr|pr):[1-9]\d{0,8}(@[0-9a-f]{40}\.\.\.[0-9a-f]{40})?$/;

/**
 * A change's id in a shareable link: `wip`, a branch name, a full sha, or a
 * merge or pull request (`mr:12`, pinned once opened).
 * Stable across reloads and machines, unlike a list position.
 */
export function changeLinkParam(changeId: string): string {
  if (changeId === "working-tree") return "wip";
  if (CHANGE_REQUEST.test(changeId)) return changeId;
  if (changeId.startsWith("commit:")) return changeId.slice("commit:".length);
  const branch = changeId.replace(/^branch:/, "");
  // A branch named like the other two forms keeps its prefix; `:` never
  // appears in a git branch name, so the prefixed form is unambiguous.
  return branch === "wip" || branch === "working-tree" || SHA.test(branch) ? `branch:${branch}` : branch;
}

/**
 * The change id a link's `change` value names. A picker id pasted as is
 * (`commit:<sha>`, `branch:<name>`, `mr:<n>`, `pr:<n>`, `working-tree`) means itself.
 */
export function changeIdFromLink(param: string): string {
  if (param.startsWith("branch:") || CHANGE_REQUEST.test(param) || param === "working-tree") return param;
  if (param.startsWith("commit:") && SHA.test(param.slice("commit:".length))) return param;
  if (param === "wip") return "working-tree";
  return SHA.test(param) ? `commit:${param}` : `branch:${param}`;
}
