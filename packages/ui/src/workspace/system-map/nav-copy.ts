/**
 * The words the map's navigation says: search, the Map | List switch, the
 * services table, the shortcut help and the level announcements. English
 * defaults here; a host with a message catalog passes its own through
 * `SystemMap`'s `navCopy` prop. Plain data and string builders, no React.
 */

export interface SystemMapNavCopy {
  searchLabel: string;
  searchPlaceholder: string;
  searchServices: string;
  searchGroups: string;
  searchRoutes: string;
  searchNone: string;
  searchRoutesLoading: string;
  /** Under a route hit: who calls it, e.g. "webapp calls it" or "webapp and 2 more call it". */
  routeCallers: (consumer: string, others: number) => string;
  /** Under a route hit nobody else calls: the provider alone. */
  routeProvider: (provider: string) => string;
  displayLabel: string;
  displayMap: string;
  displayList: string;
  helpOpen: string;
  helpTitle: string;
  keySearch: string;
  keyBack: string;
  keyFocus: string;
  keyBetween: string;
  keyList: string;
  keyHelp: string;
  keySteps: string;
  keyPick: string;
  listCaption: string;
  colService: string;
  colGroup: string;
  colHealth: string;
  colIn: string;
  colOut: string;
  colContracts: string;
  noScore: string;
  /** "provided · consumed" in the Contracts cell. */
  contractsCell: (provided: number, consumed: number) => string;
  announceEstate: (services: number, groups: number) => string;
  announceGroup: (label: string, services: number) => string;
  announceFocus: (name: string, callers: number, dependencies: number) => string;
  announceBetween: (a: string, b: string) => string;
  announceList: (services: number) => string;
  announcePick: (name: string) => string;
}

const n = (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`;

export const DEFAULT_NAV_COPY: SystemMapNavCopy = {
  searchLabel: "Search services, groups and routes",
  searchPlaceholder: "Search the map",
  searchServices: "Services",
  searchGroups: "Groups",
  searchRoutes: "Routes",
  searchNone: "Nothing matches",
  searchRoutesLoading: "Searching routes",
  routeCallers: (consumer, others) => (others > 0 ? `${consumer} and ${others} more call it` : `${consumer} calls it`),
  routeProvider: (provider) => `served by ${provider}`,
  displayLabel: "Display",
  displayMap: "Map",
  displayList: "List",
  helpOpen: "Keyboard shortcuts",
  helpTitle: "System map keyboard shortcuts",
  keySearch: "Search services, groups and routes",
  keyBack: "Close details, then go back a level",
  keyFocus: "Focus the selected service",
  keyBetween: "Compare two services, starting from the selected one",
  keyList: "Switch between the map and the list",
  keyHelp: "Show or hide this help",
  keySteps: "Step through search results",
  keyPick: "Go to the highlighted result",
  listCaption: "Services in the estate, one row each",
  colService: "Service",
  colGroup: "Group",
  colHealth: "Health",
  colIn: "Runtime in",
  colOut: "Runtime out",
  colContracts: "Contracts",
  noScore: "No score",
  contractsCell: (provided, consumed) => `${provided} provided · ${consumed} consumed`,
  announceEstate: (services, groups) =>
    groups > 0 ? `Estate: ${n(groups, "group", "groups")}, ${n(services, "service", "services")}` : `Estate: ${n(services, "service", "services")}`,
  announceGroup: (label, services) => `${label} opened: ${n(services, "service", "services")}`,
  announceFocus: (name, callers, dependencies) =>
    `Focused on ${name}: ${n(callers, "caller", "callers")}, ${n(dependencies, "dependency", "dependencies")}`,
  announceBetween: (a, b) => `Showing the links between ${a} and ${b}`,
  announceList: (services) => `List view: ${n(services, "service", "services")}`,
  announcePick: (name) => `Pick a second service to compare with ${name}`,
};

/** The defaults with whatever the host overrides. */
export function navCopyWith(overrides: Partial<SystemMapNavCopy> | undefined): SystemMapNavCopy {
  return overrides ? { ...DEFAULT_NAV_COPY, ...overrides } : DEFAULT_NAV_COPY;
}
