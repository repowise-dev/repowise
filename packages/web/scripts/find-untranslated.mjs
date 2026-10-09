#!/usr/bin/env node
/**
 * Untranslated-string detector for the Repowise Web UI.
 *
 * Reports user-visible copy that is still hard-coded in the tsx files under
 * src/ instead of coming from the message catalogs. It is a linter, not a
 * translator: it never edits files, it prints a list.
 *
 *   npm run i18n:todo                      # full baseline, grouped by file
 *   npm run i18n:todo -- src/components    # scope to a directory
 *   npm run i18n:todo -- --json            # machine-readable, for follow-up cards
 *   npm run i18n:todo -- --strict          # exit 1 when anything is untranslated
 *
 * What counts as untranslated (see I18N.md, "Finding untranslated copy"):
 *   - JSX text nodes            <span>Save changes</span>
 *   - JSX attribute strings     placeholder="Search", aria-label="Close",
 *                               title="...", alt="...", label="..."
 *   - user-facing string exits  toast.*("..."), confirm("..."), alert("..."),
 *                               window.prompt("..."), new Error("...")
 *
 * Deliberately not reported: className / data-* / key / id, imports and types,
 * comments, test files, the contents of <code>, <pre>, <kbd> and <samp>, URLs,
 * paths, identifiers, and every string listed in
 * i18n-untranslated-allowlist.json.
 *
 * Exit: 0 = list produced, 1 = --strict and something is untranslated,
 *       2 = usage or configuration error.
 */

import { createRequire } from "node:module";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const require = createRequire(import.meta.url);
let ts;
try {
  ts = require("typescript");
} catch {
  console.error(
    "cannot resolve 'typescript' - run `npm install` at the repo root first.",
  );
  process.exit(2);
}

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PACKAGE_ROOT = path.resolve(HERE, "..");
const SRC_ROOT = path.join(PACKAGE_ROOT, "src");
const DEFAULT_ALLOWLIST = path.join(
  PACKAGE_ROOT,
  "i18n-untranslated-allowlist.json",
);

/** JSX attributes whose string value is user-visible copy. */
const JSX_TEXT_ATTRIBUTES = new Set([
  "placeholder",
  "aria-label",
  "aria-placeholder",
  "aria-description",
  "title",
  "alt",
  "label",
  "hint",
  "description",
]);

/** Element contents that are code, not prose. */
const NON_PROSE_TAGS = new Set(["code", "pre", "kbd", "samp", "script", "style"]);

/** Object keys of a toast options bag that carry copy. */
const TOAST_OPTION_KEYS = new Set(["description", "title", "label"]);

const ERROR_CONSTRUCTORS = new Set(["Error"]);
const WINDOW_DIALOGS = new Set(["confirm", "alert", "prompt"]);

// ---------------------------------------------------------------------------
// text helpers
// ---------------------------------------------------------------------------

const ENTITIES = {
  amp: "&",
  lt: "<",
  gt: ">",
  quot: '"',
  apos: "'",
  nbsp: " ",
  hellip: "\u2026",
  mdash: "\u2014",
  ndash: "\u2013",
  rarr: "\u2192",
  larr: "\u2190",
  times: "\u00d7",
  check: "\u2713",
  middot: "\u00b7",
};

function decodeEntities(raw) {
  return raw.replace(/&(#x?[0-9a-fA-F]+|[a-zA-Z]+);/g, (whole, body) => {
    if (body.startsWith("#")) {
      const hex = body[1] === "x" || body[1] === "X";
      const code = Number.parseInt(hex ? body.slice(2) : body.slice(1), hex ? 16 : 10);
      return Number.isFinite(code) ? String.fromCodePoint(code) : whole;
    }
    const named = ENTITIES[body];
    return named === undefined ? whole : named;
  });
}

/** JSX text nodes carry their own indentation and newlines - flatten them. */
function normalizeText(raw) {
  return decodeEntities(raw).replace(/\s+/g, " ").trim();
}

const CJK = /[\u3000-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/;

/** Technical tokens: never translated, and never worth an allowlist entry. */
const TECHNICAL = [
  /^(https?:\/\/|mailto:|www\.)/i, // URLs
  /^[^\s@]+@[^\s@]+\.[^\s@]+$/, // emails
  /^[a-z][a-z0-9+.-]*\/[a-z0-9+.-]+$/i, // mime types
  /^[./~]/, // absolute / relative paths
  /^[A-Za-z0-9_@*-]+(\/[A-Za-z0-9_@*-]+)+$/, // a/b/c, src/**/*
  /^[A-Za-z0-9]+([_.:-][A-Za-z0-9]+)+$/, // dotted, snake, kebab, scoped
  /^\.[a-z0-9]+$/i, // .tsx
  /^v?\d+(\.\d+)+$/i, // 1.2.3
  /^[A-Z][A-Z0-9_]{2,}=/, // ENV_VAR=value snippets
  /^\d+(\.\d+)?\s*(px|rem|em|ms|s|%|kb|mb|gb|tb|d|h|m)$/i, // 24px, 3s
  /^[#@][A-Za-z0-9_-]+$/, // #123, @user
];

/**
 * Returns the copy to report, or null when the string is not translatable
 * prose. `minLength` is the configurable floor for JSX text nodes.
 */
function translatable(raw, minLength) {
  const text = normalizeText(raw);
  if (!text) return null;
  if (text.length < minLength) return null;
  if (!/[A-Za-z]/.test(text)) return null; // no latin letters: symbols, digits
  if (CJK.test(text)) return null; // already Chinese/Japanese copy
  for (const pattern of TECHNICAL) {
    if (pattern.test(text)) return null;
  }
  return text;
}

// ---------------------------------------------------------------------------
// allowlist
// ---------------------------------------------------------------------------

const ALLOWLIST_HELP =
  "i18n-untranslated-allowlist.json: {\"entries\": [{\"value\": \"Repowise\", \"reason\": \"product name\"}]}. Every entry needs a value and a short reason; optional \"match\": \"regex\".";

function loadAllowlist(file) {
  if (!existsSync(file)) {
    console.warn(`! no allowlist at ${path.relative(PACKAGE_ROOT, file)} - every hit is reported.`);
    return { exact: new Map(), regex: [], entries: 0 };
  }

  let parsed;
  try {
    parsed = JSON.parse(readFileSync(file, "utf8"));
  } catch (error) {
    console.error(`✖ allowlist is not valid JSON: ${error.message}`);
    process.exit(2);
  }

  const entries = Array.isArray(parsed) ? parsed : parsed?.entries;
  if (!Array.isArray(entries)) {
    console.error(`✖ allowlist has no "entries" array.\n  ${ALLOWLIST_HELP}`);
    process.exit(2);
  }

  const exact = new Map();
  const regex = [];
  entries.forEach((entry, index) => {
    const where = `entries[${index}]`;
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
      console.error(`✖ ${where} is not an object.\n  ${ALLOWLIST_HELP}`);
      process.exit(2);
    }
    const { value, reason, match = "exact" } = entry;
    if (typeof value !== "string" || value.trim() === "") {
      console.error(`✖ ${where} has no "value".\n  ${ALLOWLIST_HELP}`);
      process.exit(2);
    }
    if (typeof reason !== "string" || reason.trim().length < 8) {
      console.error(
        `✖ ${where} ("${value}") needs a real "reason" - a short audit note, not a shrug.\n  ${ALLOWLIST_HELP}`,
      );
      process.exit(2);
    }
    if (match === "exact") {
      if (exact.has(value)) {
        console.warn(`! allowlist entry "${value}" is duplicated; keeping the first reason.`);
      } else {
        exact.set(value, reason);
      }
    } else if (match === "regex") {
      try {
        regex.push({ pattern: new RegExp(value, "u"), source: value, reason, hits: 0 });
      } catch (error) {
        console.error(`✖ ${where} ("${value}") is not a valid regex: ${error.message}`);
        process.exit(2);
      }
    } else {
      console.error(`✖ ${where} has match="${match}" - use "exact" or "regex".`);
      process.exit(2);
    }
  });

  return { exact, regex, entries: entries.length };
}

/** Returns the audit reason when the string is allowlisted, else null. */
function allowlistReason(allowlist, text) {
  const direct = allowlist.exact.get(text);
  if (direct) return direct;
  for (const rule of allowlist.regex) {
    if (rule.pattern.test(text)) {
      rule.hits += 1;
      return rule.reason;
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// JSX / call-site extraction
// ---------------------------------------------------------------------------

function tagNameText(name) {
  if (ts.isIdentifier(name)) return name.text;
  return name.getText();
}

function rootIdentifier(node) {
  let current = node;
  while (ts.isPropertyAccessExpression(current) || ts.isElementAccessExpression(current)) {
    current = current.expression;
  }
  return ts.isIdentifier(current) ? current.text : null;
}

/**
 * String literals reachable from a JSX attribute value or call argument:
 * "copy", {"copy"}, {cond ? "a" : "b"}, `copy`, `copy ${x}`.
 */
function literalParts(expression) {
  if (!expression) return [];
  if (ts.isParenthesizedExpression(expression)) return literalParts(expression.expression);
  if (ts.isStringLiteral(expression) || ts.isNoSubstitutionTemplateLiteral(expression)) {
    return [{ copy: expression.text, node: expression }];
  }
  if (ts.isTemplateExpression(expression)) {
    const copy = expression.templateSpans.reduce(
      (acc, span) => `${acc}{…}${span.literal.text}`,
      expression.head.text,
    );
    return [{ copy, node: expression }];
  }
  if (ts.isConditionalExpression(expression)) {
    return [...literalParts(expression.whenTrue), ...literalParts(expression.whenFalse)];
  }
  if (ts.isJsxExpression(expression)) return literalParts(expression.expression);
  return [];
}

function stringExitDetail(node) {
  const callee = node.expression;
  if (ts.isIdentifier(callee)) {
    if (callee.text === "toast" || WINDOW_DIALOGS.has(callee.text)) {
      return `call:${callee.text}`;
    }
    return null;
  }
  if (!ts.isPropertyAccessExpression(callee)) return null;
  const root = rootIdentifier(callee);
  if (root === "toast") return "call:toast";
  if ((root === "window" || root === "globalThis") && WINDOW_DIALOGS.has(callee.name.text)) {
    return `call:window.${callee.name.text}`;
  }
  return null;
}
// ---------------------------------------------------------------------------
// scanning
// ---------------------------------------------------------------------------

const TEST_FILE = /\.(test|spec|stories)\.tsx$/;

function isTestPath(relative) {
  return TEST_FILE.test(relative) || /(^|\/)(__tests__|__mocks__)\//.test(relative);
}

function collectTsx(dir, out = []) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === "node_modules" || entry.name === ".next") continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      collectTsx(full, out);
      continue;
    }
    if (!entry.name.endsWith(".tsx")) continue;
    if (isTestPath(path.relative(PACKAGE_ROOT, full))) continue;
    out.push(full);
  }
  return out;
}

function collectHits(file, source, allowlist, options) {
  const sourceFile = ts.createSourceFile(
    file,
    source,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TSX,
  );
  const found = [];
  const allowed = [];
  const seen = new Set();

  const push = (node, kind, detail, raw) => {
    const text = translatable(raw, options.minLength);
    if (!text) return;
    const pos = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile));
    const entry = {
      file: path.relative(PACKAGE_ROOT, file),
      line: pos.line + 1,
      column: pos.character + 1,
      kind,
      detail,
      text,
    };
    const key = `${entry.line}:${entry.column}:${text}`;
    if (seen.has(key)) return;
    seen.add(key);
    const reason = allowlistReason(allowlist, text);
    if (reason) {
      entry.reason = reason;
      allowed.push(entry);
    } else {
      found.push(entry);
    }
  };

  const checkToastBag = (node) => {
    const bag = node.arguments[1];
    if (!bag || !ts.isObjectLiteralExpression(bag)) return;
    for (const prop of bag.properties) {
      if (!ts.isPropertyAssignment(prop) || !ts.isIdentifier(prop.name)) continue;
      if (!TOAST_OPTION_KEYS.has(prop.name.text)) continue;
      for (const part of literalParts(prop.initializer)) {
        push(part.node, "string-exit", `toast.${prop.name.text}`, part.copy);
      }
    }
  };

  const visit = (node, inNonProse) => {
    if (ts.isJsxElement(node) || ts.isJsxSelfClosingElement(node)) {
      const tag = ts.isJsxElement(node) ? node.openingElement.tagName : node.tagName;
      const nonProse = inNonProse || NON_PROSE_TAGS.has(tagNameText(tag));
      ts.forEachChild(node, (child) => visit(child, nonProse));
      return;
    }
    if (ts.isJsxText(node)) {
      if (!inNonProse) push(node, "jsx-text", null, node.text);
      return;
    }
    if (ts.isJsxAttribute(node)) {
      const name = tagNameText(node.name);
      if (!inNonProse && JSX_TEXT_ATTRIBUTES.has(name)) {
        for (const part of literalParts(node.initializer)) {
          push(part.node, "jsx-attribute", name, part.copy);
        }
      }
      ts.forEachChild(node, (child) => visit(child, inNonProse));
      return;
    }
    if (ts.isCallExpression(node)) {
      const detail = stringExitDetail(node);
      if (detail) {
        for (const part of literalParts(node.arguments[0])) {
          push(part.node, "string-exit", detail, part.copy);
        }
        if (detail === "call:toast") checkToastBag(node);
      }
    } else if (
      ts.isNewExpression(node) &&
      ts.isIdentifier(node.expression) &&
      ERROR_CONSTRUCTORS.has(node.expression.text)
    ) {
      for (const part of literalParts(node.arguments?.[0])) {
        push(part.node, "string-exit", "new:Error", part.copy);
      }
    }
    ts.forEachChild(node, (child) => visit(child, inNonProse));
  };

  visit(sourceFile, false);
  return { found, allowed };
}
// ---------------------------------------------------------------------------
// CLI + report
// ---------------------------------------------------------------------------

const USAGE = [
  "Usage: node scripts/find-untranslated.mjs [paths...] [options]",
  "",
  "  paths                directories or .tsx files to scan (default: src/)",
  "  --json               print the machine-readable report instead",
  "  --strict             exit 1 when anything is untranslated (CI gate)",
  "  --show-allowed       include allowlisted hits in the report",
  "  --min-length=<n>     ignore copy shorter than n chars (default: 2)",
  "  --group-depth=<n>    directory grouping depth under src/ (default: 2)",
  "  --allowlist=<file>   allowlist to use (default: i18n-untranslated-allowlist.json)",
  "  -h, --help           this text",
].join("\n");

function parseArgs(argv) {
  const options = {
    json: false,
    strict: false,
    showAllowed: false,
    minLength: 2,
    groupDepth: 2,
    allowlist: DEFAULT_ALLOWLIST,
    targets: [],
  };
  const number = (flag, raw, min, max) => {
    const value = Number.parseInt(raw, 10);
    if (!Number.isInteger(value) || value < min || value > max) {
      console.error(`✖ ${flag} expects an integer between ${min} and ${max}, got "${raw}".`);
      process.exit(2);
    }
    return value;
  };
  for (const arg of argv) {
    if (arg === "--json") options.json = true;
    else if (arg === "--strict") options.strict = true;
    else if (arg === "--show-allowed") options.showAllowed = true;
    else if (arg === "-h" || arg === "--help") {
      console.log(USAGE);
      process.exit(0);
    } else if (arg.startsWith("--min-length=")) {
      options.minLength = number("--min-length", arg.slice(13), 1, 64);
    } else if (arg.startsWith("--group-depth=")) {
      options.groupDepth = number("--group-depth", arg.slice(14), 1, 6);
    } else if (arg.startsWith("--allowlist=")) {
      options.allowlist = path.resolve(process.cwd(), arg.slice(12));
    } else if (arg.startsWith("-")) {
      console.error(`✖ unknown option "${arg}".\n\n${USAGE}`);
      process.exit(2);
    } else {
      options.targets.push(arg);
    }
  }
  return options;
}

function resolveTargets(targets) {
  if (targets.length === 0) return [SRC_ROOT];
  return targets.map((target) => {
    const cleaned = target.replace(/\/\*\*$/, "").replace(/\/+$/, "");
    const candidates = [
      path.resolve(PACKAGE_ROOT, cleaned),
      path.resolve(SRC_ROOT, cleaned),
    ];
    const found = candidates.find((candidate) => existsSync(candidate));
    if (!found) {
      console.error(
        `✖ no such path "${target}" (tried ${candidates
          .map((c) => path.relative(PACKAGE_ROOT, c))
          .join(", ")}).`,
      );
      process.exit(2);
    }
    return found;
  });
}

function groupDirectory(relative, depth) {
  const parts = path.dirname(relative).split(path.sep);
  const underSrc = parts[0] === "src" ? parts.slice(1) : parts;
  return underSrc.slice(0, depth).join("/") || ".";
}

function byLocation(a, b) {
  return a.file.localeCompare(b.file) || a.line - b.line || a.column - b.column;
}

function summarize(hits, depth) {
  const buckets = new Map();
  for (const hit of hits) {
    const directory = groupDirectory(hit.file, depth);
    const bucket = buckets.get(directory) ?? { directory, files: new Set(), strings: 0 };
    bucket.files.add(hit.file);
    bucket.strings += 1;
    buckets.set(directory, bucket);
  }
  return [...buckets.values()]
    .map((b) => ({ directory: b.directory, files: b.files.size, strings: b.strings }))
    .sort((a, b) => b.strings - a.strings || a.directory.localeCompare(b.directory));
}

function printHits(hits, stream, options) {
  let current = null;
  for (const hit of hits) {
    if (hit.file !== current) {
      current = hit.file;
      stream.write(`\n${hit.file}\n`);
    }
    const where = `${hit.line}:${hit.column}`.padEnd(7);
    const kind = (hit.detail ? `${hit.kind}:${hit.detail}` : hit.kind).padEnd(24);
    const note = hit.reason ? `  [allowed: ${hit.reason}]` : "";
    stream.write(`  ${where}${kind}${hit.text}${note}\n`);
  }
  if (hits.length === 0) stream.write("\n  (none)\n");
}

function reportText(result, options, extras) {
  const scope = options.targets.length ? options.targets.join(" ") : "src/";
  process.stdout.write(
    `untranslated copy in ${scope} (min length ${options.minLength})\n`,
  );
  printHits(result.hits, process.stdout, options);
  if (options.showAllowed) {
    process.stdout.write(`\nallowlisted (${result.allowlistedHits})\n`);
    printHits(result.allowlisted ?? [], process.stdout, options);
  }
  const line = "─".repeat(58);
  process.stdout.write(`\n${line}\n`);
  const rows = [
    ["files scanned", extras.scanned],
    ["files with untranslated copy", result.hitFiles],
    ["untranslated strings", result.totalHits],
    ["allowlisted strings", result.allowlistedHits],
  ];
  for (const [label, value] of rows) {
    process.stdout.write(`${label.padEnd(32)}${String(value).padStart(6)}\n`);
  }
  if (result.byDirectory.length > 0) {
    process.stdout.write(`\nby directory (depth ${options.groupDepth} under src/)\n`);
    process.stdout.write(`  ${"strings".padStart(8)}${"files".padStart(7)}  directory\n`);
    for (const row of result.byDirectory) {
      process.stdout.write(
        `  ${String(row.strings).padStart(8)}${String(row.files).padStart(7)}  ${row.directory}\n`,
      );
    }
  }
  for (const warning of extras.stale) process.stderr.write(`! ${warning}\n`);
}
function main() {
  const options = parseArgs(process.argv.slice(2));
  const allowlist = loadAllowlist(options.allowlist);
  const targets = resolveTargets(options.targets);

  const files = [
    ...new Set(
      targets.flatMap((target) => {
        if (statSync(target).isDirectory()) return collectTsx(target);
        const relative = path.relative(PACKAGE_ROOT, target);
        return target.endsWith(".tsx") && !isTestPath(relative) ? [target] : [];
      }),
    ),
  ].sort();

  const hits = [];
  const allowed = [];
  for (const file of files) {
    const scanned = collectHits(file, readFileSync(file, "utf8"), allowlist, options);
    hits.push(...scanned.found);
    allowed.push(...scanned.allowed);
  }
  hits.sort(byLocation);
  allowed.sort(byLocation);

  const hitFiles = new Set(hits.map((hit) => hit.file)).size;
  const result = {
    scope: options.targets.length ? options.targets : ["src"],
    minLength: options.minLength,
    scannedFiles: files.length,
    hitFiles,
    totalHits: hits.length,
    allowlistedHits: allowed.length,
    byDirectory: summarize(hits, options.groupDepth),
    hits,
    allowlisted: allowed,
  };

  const used = new Set(allowed.map((hit) => hit.text));
  const stale = [
    ...[...allowlist.exact.keys()]
      .filter((value) => !used.has(value))
      .map((value) => `allowlist entry "${value}" matched nothing in this scan.`),
    ...allowlist.regex
      .filter((rule) => rule.hits === 0)
      .map((rule) => `allowlist regex /${rule.source}/ matched nothing in this scan.`),
  ];

  if (options.json) {
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  } else {
    reportText(result, options, { scanned: files.length, stale });
  }

  if (options.strict && hits.length > 0) {
    if (!options.json) {
      process.stderr.write(
        `\n✖ ${hits.length} untranslated string(s) in ${hitFiles} file(s).\n`,
      );
    }
    process.exit(1);
  }
}

main();
