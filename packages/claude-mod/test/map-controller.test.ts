// The map's keys over a whole LensMap: the cursor walks only what the view
// draws, a new prompt or a /clear starts with nothing selected and no zoom, a
// selection the turn stopped lighting never comes back ringed, and a zoom into
// a folder the feed no longer has falls back to the whole repo.
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { HealthMapFeed } from "@repowise-dev/types/health";
import { LensMap, type MapIO, type PaneInput } from "../src/map-controller";
import { NO_STORY } from "../src/model/story";
import type { Node } from "../src/views/elements";
import { MAP_KEYS } from "../src/views/mapPane";
import { NO_LIT, type Lit } from "../src/views/overlay";
import { feed } from "./django";

const served = vi.hoisted(() => ({ feed: null as unknown }));
vi.mock("@repowise-dev/api-client/code-health", () => ({ getHealthMap: async () => served.feed }));

const io: MapIO = {
  redraw: () => undefined,
  debug: () => undefined,
  blit: async () => ({}),
  openPane: async () => ({ isPlaced: true }),
  closePane: async () => undefined,
};
const PANE: PaneInput = { surface: "terminal", notice: "", theme: "dark", bodyColumns: 180, placement: "dock", bodyRows: 60 };
const REPO = { id: "dj", root: "C:\\work\\django", updatedAt: null, caseInsensitive: true };

const MODELS = ["django/db/models/query.py", "django/db/models/manager.py", "django/db/models/base.py"];
const ELSEWHERE = ["django/conf/__init__.py", "django/http/request.py"];
const turnLit = (reads: string[]): Lit => ({ ...NO_LIT, reads, edits: [MODELS[0]!], edit: MODELS[0]!, current: MODELS[0]! });

const texts = (n: Node): string[] => (n.type === "Text" ? [n.children.join("")] : n.type === "Box" ? n.children.flatMap(texts) : []);
/** The path the detail line names, or null with nothing selected. */
const detailOf = (n: Node): string | null => texts(n).find((t) => /^django\/\S+ · /.test(t))?.split(" · ")[0] ?? null;
const crumbOf = (n: Node): string | null => texts(n).find((t) => / \/ /.test(t) && !t.includes(" · ")) ?? null;

let lit: Lit;
let map: LensMap;
const draw = () => map.paneTree(io, PANE);
const press = (k: keyof typeof MAP_KEYS) => map.press(io, MAP_KEYS[k].key);

beforeEach(async () => {
  served.feed = feed;
  lit = turnLit([...MODELS, ...ELSEWHERE]);
  map = new LensMap(false, undefined, false, () => ({ lit, story: NO_STORY }));
  map.setRepo(io, REPO);
  draw();
  await vi.waitFor(() => expect(texts(draw()).some((t) => t.includes("drawn"))).toBe(true));
});

describe("the map's cursor and zoom", () => {
  it("zoomed in, j walks only the lit files that folder draws", () => {
    press("next");
    expect(detailOf(draw())).toBe(MODELS[0]);
    press("zoom");
    const root = crumbOf(draw())!.split(" / ").join("/");
    expect(MODELS[0]!.startsWith(`${root}/`)).toBe(true);
    const seen = new Set<string>();
    for (let i = 0; i < 8; i++) {
      press("next");
      seen.add(detailOf(draw())!);
    }
    expect([...seen].every((p) => p.startsWith(`${root}/`))).toBe(true);
    expect(seen.size).toBeGreaterThan(1);
  });

  it("a new prompt starts with nothing selected and the whole repo in view", () => {
    press("next");
    press("zoom");
    expect(crumbOf(draw())).not.toBeNull();
    map.turnStarted();
    const tree = draw();
    expect([detailOf(tree), crumbOf(tree)]).toEqual([null, null]);
  });

  it("a /clear drops the selection and the zoom too", () => {
    press("next");
    press("zoom");
    map.clearConversation(io);
    const tree = draw();
    expect([detailOf(tree), crumbOf(tree)]).toEqual([null, null]);
  });

  it("a selection the turn stops lighting is dropped for good: lit again, it is not ringed again", () => {
    press("next");
    press("next");
    const chosen = detailOf(draw())!;
    lit = turnLit([...MODELS, ...ELSEWHERE].filter((p) => p !== chosen));
    expect(detailOf(draw())).toBeNull();
    lit = turnLit([...MODELS, ...ELSEWHERE]);
    expect(detailOf(draw())).toBeNull();
  });

  it("a zoom into a folder the feed no longer has falls back to the whole repo", async () => {
    press("next");
    press("zoom");
    const root = crumbOf(draw())!.split(" / ").join("/");
    // Stands in for a refreshed feed: the same map, without the zoomed folder.
    const without: HealthMapFeed = { ...feed, files: feed.files.filter((f) => !f.file_path.startsWith(`${root}/`)) };
    (map as unknown as { feed: { status: "ready"; data: HealthMapFeed } }).feed = { status: "ready", data: without };
    const tree = draw();
    expect(crumbOf(tree)).toBeNull();
    expect(texts(tree).some((t) => t.includes("drawn"))).toBe(true);
  });
});
