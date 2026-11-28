/**
 * The English strings the web decisions page shows, copied from its message
 * catalog (`views.decisions.*` and `decisions.*` in packages/web/messages/en.json)
 * so the panel's labels match the page word for word. The webview has no
 * next-intl, so the catalog's plural forms are spelled out as functions.
 */

function plural(count: number, one: string, other: string): string {
  return (count === 1 ? one : other).replace("#", String(count));
}

export const VIEW = {
  title: "Decisions",
  description:
    "What this codebase has settled on and what is true of the checkout you are standing in: the constraints, the tradeoffs, and the facts that catch people out.",
  ledeAccepted: "In force",
  ledeConfirmed: "Confirmed",
  ledeEmptyBefore:
    "Decisions land here as the indexer mines them from pull requests, commit messages, code comments and your own sessions, and as you record them yourself — below, or with ",
  ledeEmptyCode: "repowise decision add",
  ledeEmptyAfter: ". Each one keeps the quote it was drawn from and the files it governs.",
  ledeNoGovernance:
    "Nothing is in force yet. A decision an agent records comes into force when the commit it cites merges to the default branch.",
  ledeCandidates: (count: number) =>
    `${plural(count, "# background record is", "# background records are")} below, mined from pull requests, commits, comments and sessions. ${plural(count, "It is", "They are")} not in force; agents ask about ${plural(count, "it", "them")} through get_why for the why behind the code.`,
  ledeAcceptedRecords: (count: number) =>
    `${plural(count, "# decision governs", "# decisions govern")} this repository. An agent editing a file one of them names is told about it before it changes anything.`,
  ledeAcceptedMore: (count: number) =>
    `${plural(count, "# more is a background record", "# more are background records")} mined from history. Not in force; agents ask about them through get_why for the why.`,
  checkoutTitle: "About this checkout",
  checkoutDescription:
    "Facts about the working tree rather than claims anybody made about it, derived at index time with no model call and no history. These are the ones that trip up a newcomer, human or agent.",
  reviewTitle: "All decisions",
  reviewDescription:
    "What is in force, with pinned decisions first; what your agents decided this week; background mined from history; and what has been retired. Hand any one to your agent to pin, veto or change it.",
} as const;

export const AGENT = {
  agreements: "Move working agreements to CLAUDE.md",
  review: "Check with your agent",
  edit: "Change with your agent",
  veto: "Veto with your agent",
  pin: "Pin with your agent",
  unpin: "Unpin with your agent",
} as const;

export const ADD = {
  trigger: "Record a decision",
  title: "Record a decision",
  description:
    "Something the team has settled on. Name the files it governs and it is recorded as confirmed, because you are the person confirming it.",
} as const;

export const REVIEW = {
  accepted: "In force. It governs the files it names.",
  dismissed: "Dismissed. Reindexing will not mine it again.",
  acceptFailed: (message: string) => `Couldn't put it in force: ${message}`,
  dismissFailed: (message: string) => `Couldn't dismiss: ${message}`,
  reaffirmed: "Reaffirmed. It is current again for the files it names.",
  reaffirmFailed: (message: string) => `Couldn't reaffirm: ${message}`,
  bulkDismissed: (count: number) => `Dismissed ${plural(count, "# record", "# records")}.`,
  actionFailed: "That action failed.",
  previous: "Previous",
  next: "Next",
  pinned: "Pinned. An agent asks a person before replacing it.",
  unpinned: "Unpinned. It stays in force.",
  pinFailed: (message: string) => `Couldn't pin: ${message}`,
  unpinFailed: (message: string) => `Couldn't unpin: ${message}`,
} as const;

export const HERO = {
  discoverHeading: (count: number) =>
    `Check ${plural(count, "# background record", "# background records")} on the busiest files`,
  discoverDescription:
    "Your agent reads each record against the code and its commits, puts the real ones in force and dismisses the rest. You see what it did under This week.",
  addHeading: "Record the decisions this repository already follows",
  addDescription:
    "Your agent reads the code and history and records the rules it clearly follows, each with the quote and commit it came from. You see each one here as it lands.",
  copy: "Copy prompt for your agent",
  copied: "Copied. Paste it into your agent.",
  copyFailed: "Couldn't copy. Select the preview and copy it by hand.",
  footnoteBefore: "Same text as ",
  footnoteCode: "repowise decision discover",
  footnoteAfter: ".",
  recordMissed: "Record missed decisions",
  addByHand: "Add one by hand",
  reviewAll: "Review all records",
} as const;

export const BOARD = {
  movedOnly: (count: number) =>
    `Showing the ${plural(count, "# decision", "# decisions")} in force whose code changed after ${plural(count, "it was", "they were")} accepted.`,
  showAll: "Show everything in force",
} as const;
