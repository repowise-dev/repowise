# Telemetry

Repowise collects **completely anonymous** usage telemetry to help us decide
what to build and fix. It is **impossible to tie any event to you, your
machine, or your code**. There are no usernames, no IP addresses, no file
paths, and no source content anywhere in what we send.

This page documents exactly what is collected, what is never collected, how to
verify it yourself, and how to turn it off.

## Why we collect it

A tiny amount of anonymous data answers questions we otherwise have to guess at:

- Which commands do people actually run? (where to invest)
- Which CLI versions and Python versions are in use? (what we can drop support for)
- What share of runs fail, and on which command? (where the bugs are)
- Which OSes matter? (what to test on)

That is the whole purpose. We do not sell it, and we do not use it to identify
anyone.

## What is collected

One event, `command_run`, is sent once per CLI invocation. Every event carries:

| Field | Example | Purpose |
|---|---|---|
| `event` | `command_run` | Event type |
| `anon_id` | `194639…d48a` | Random per-install id (see below) |
| `session_id` | `e2ecb2…1158` | Random per-process id, groups one run |
| `cli_version` | `0.21.0` | Version adoption |
| `os` | `darwin` / `linux` / `windows` | Platform priorities |
| `arch` | `arm64` / `x86_64` | Architecture priorities |
| `python_version` | `3.12` | **major.minor only** |
| `is_ci` | `true` / `false` | Separate automation from humans |
| `properties.command` | `init`, `update`, `health` | Which command ran |
| `properties.subcommand` | `decision.add`, `telemetry.status` | A known subcommand name only |
| `properties.flags` | `["--resume", "--provider"]` | Option **names only**, never values |
| `properties.status` | `ok` / `error` | Success rate |
| `properties.error_type` | `LLMProviderError` | Exception **class name only** |
| `properties.duration_ms` | `71840` | Performance in the wild |

Example payload:

```json
{
  "event": "command_run",
  "anon_id": "194639251e854669ae2f56081620d48a",
  "session_id": "e2ecb2aeb4df422883c5af451f2b1158",
  "cli_version": "0.21.0",
  "os": "darwin",
  "arch": "arm64",
  "python_version": "3.12",
  "is_ci": false,
  "properties": {
    "command": "init",
    "subcommand": null,
    "flags": ["--resume"],
    "status": "ok",
    "error_type": null,
    "duration_ms": 71840
  }
}
```

## What is **never** collected

- Source code or file contents
- File paths or directory names
- Repository names, package names, symbol/function names
- Generated documentation text
- Flag **values** (only the flag name is recorded)
- Environment variable values
- API keys or credentials
- Error messages or stack traces (only the exception class name)
- IP addresses (the server does not record the request source)
- Usernames, hostnames, email, or anything personally identifiable

## How the anonymous id works

`anon_id` is a random UUID generated once and stored in
`~/.repowise/platform.json`. It is **not** derived from your hostname,
username, or any machine identifier, so it cannot be reversed to a person. It
only lets us count distinct installs. Delete the file and a new, unrelated id
is generated; nothing links the two.

## Verify it yourself

Run any command with `REPOWISE_TELEMETRY_DEBUG=1` to print the exact payload to
stderr **without sending anything**:

```bash
REPOWISE_TELEMETRY_DEBUG=1 repowise status
```

## How to opt out

Any one of these disables all telemetry:

```bash
repowise telemetry disable        # persisted to ~/.repowise/platform.json
export REPOWISE_TELEMETRY_DISABLED=1
export DO_NOT_TRACK=1             # the cross-tool standard (https://consoledonottrack.com)
```

Check the current state anytime:

```bash
repowise telemetry status
```

Re-enable with `repowise telemetry enable`.

## Data retention

Events are retained for 90 days and then deleted. Only aggregate figures (e.g.
"X% of runs are on Windows") are ever used or shared.
