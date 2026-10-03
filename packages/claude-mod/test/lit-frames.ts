// The inputs the lit-frame golden covers: two turns over the recorded Django
// feed (an edit of query.py, and one of conf/__init__.py with its 248
// importers), at sizes that draw files and sizes that draw folders, both
// themes, health colours on and off, and every animation step the pane plays.
import type { Lit } from "../src/views/overlay";
import type { ThemeName } from "../src/views/theme";
import { fixture } from "./fake-host";
import { importers as queryImporters } from "./django";

const CONF = "django/conf/__init__.py";
const QUERY = "django/db/models/query.py";
const confImporters = (JSON.parse(fixture("django-blast-radius-conf-depth1.json")) as { transitive_affected: { path: string }[] }).transitive_affected.map((t) => t.path);

export const TURNS: Record<string, Lit> = {
  query: {
    hits: ["tests/queries/tests.py", "django/db/models/sql/query.py"],
    reads: ["django/db/models/manager.py", QUERY],
    named: [QUERY, "django/db/models/base.py"],
    importers: queryImporters,
    edits: [QUERY],
    edit: QUERY,
    current: QUERY,
  },
  conf: {
    hits: ["django/conf/global_settings.py"],
    reads: [CONF, "django/core/management/__init__.py"],
    named: [CONF],
    importers: confImporters,
    edits: [CONF],
    edit: CONF,
    current: CONF,
  },
};

export const SIZES: [number, number][] = [
  [110, 24],
  [110, 40],
  [180, 48],
  [180, 60],
];

export const STYLES: { theme: ThemeName; health: boolean }[] = [
  { theme: "dark", health: false },
  { theme: "light", health: false },
  { theme: "dark", health: true },
];

type Step = { kind: "flash" | "ripple"; t: number } | undefined;
export const STEPS: Step[] = [undefined, ...[0, 0.1, 0.25, 0.5, 0.75, 0.9, 1].map((t) => ({ kind: "ripple" as const, t })), ...[0, 0.5, 1].map((t) => ({ kind: "flash" as const, t }))];

export const caseName = (turn: string, size: [number, number], style: { theme: string; health: boolean }, step: Step): string =>
  `${turn} ${size[0]}x${size[1]} ${style.theme}${style.health ? "+health" : ""} ${step === undefined ? "rest" : `${step.kind}@${step.t}`}`;
