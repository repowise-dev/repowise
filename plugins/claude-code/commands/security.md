---
description: Scan for security signals — working-tree scanning already runs during init/update; use --history to walk full git history for leaked secrets.
allowed-tools: Bash, Read
---

# Repowise Security

Scan for security signals with the same local pattern registry used during
`repowise init` / `repowise update`. Working-tree scanning already happens on
those commands. This slash command is for walking **full git history** to find
leaked secrets (and optionally risky patterns) that were later removed.

No LLM — pure local scan. Findings persist to `security_findings` and show up
in the local server security API / UI. Re-runs are idempotent.

## Steps

1. If `.repowise/` doesn't exist: "This repo isn't indexed yet. Run `/repowise:init` first." Stop.
2. Decide the mode from `$ARGUMENTS` (see below), run the command, and present
   a short summary (files scanned, findings stored, by severity / kind). Do not
   print raw secret values if any appear in output — summarize kinds and paths.

## Modes

Default / no useful args — remind the user that working-tree scanning already
ran during init/update, then offer history mode:
```
repowise security scan
```
(Without `--history` this prints a hint and exits — it does **not** re-run the
working-tree scan.)

Handle `$ARGUMENTS`:
- "history" / "full" / "scan history" → `repowise security scan --history`
- "json" with history → `repowise security scan --history --output json`
- "all" / "all-patterns" with history → `repowise security scan --history --all-patterns`
- A path → `repowise security scan --history --path <dir>`
- "since <rev>" / "to <rev>" → pass `--since` / `--to` accordingly

```
repowise security scan --history
repowise security scan --history --since v1.0.0 --to HEAD
repowise security scan --history --all-patterns --output json
```

## Notes

- Default history mode reports **leaked-secret** patterns only
  (`hardcoded_password` / `hardcoded_secret`) to avoid noise. Pass
  `--all-patterns` for code-smell patterns (`eval`, `os.system`, weak hashes, …).
- Never invent findings. If the scan stores zero findings, say so plainly.
- For live-change review priority (not secret scanning), use `/repowise:risk`.
