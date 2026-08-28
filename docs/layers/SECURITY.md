# Security Signals

Repowise records a small set of security signals while it indexes: pattern
matches over source text, and symbol names that look security-relevant. Pure
regex and SQL, no LLM calls, no network, no dependency resolution.

This is a floor, not a scanner. The registry holds eleven patterns. It has no
model of your framework, no notion of which inputs are attacker-controlled, and
no dataflow: it cannot tell a parameterised query from a concatenated one beyond
what the surrounding characters give away, and it cannot tell whether a
dangerous call is reachable. Everything below is written so you can see exactly
where that floor sits, because a security surface that overstates itself is
worse than one that reports nothing.

Run a real SAST tool alongside it. Nothing here replaces one.

## Quick start

```bash
repowise init                       # working-tree signals populate during indexing
repowise security scan --history    # walk every tracked revision for leaked secrets
repowise security scan --history --since v1.0.0 --to HEAD
repowise security scan --history --all-patterns --format json
```

```
repowise security scan --history

  8 findings across 412 commits

  high   hardcoded_secret     config/settings.py:14      a3f19c2  2025-11-04
  high   hardcoded_password   deploy/bootstrap.sh:31     91b7ee0  2025-08-22
  high   hardcoded_secret     tests/fixtures/auth.py:7   4c02da8  2025-06-13
```

From an agent, through the risk surface:

```python
get_risk(target="src/api/")     # includes a security_signals block for the target
```

Findings also appear on the Security tab of the code-health page, and at
`GET /api/repos/{repo_id}/security`.

## What the registry catches

Eleven patterns plus a symbol-name scan, giving twelve kinds across three
severities. Severity is a fixed property of the pattern; nothing is scored,
ranked, or aggregated.

| Kind | Severity | Matches |
|------|----------|---------|
| `eval_call` | high | `eval(...)`, including a receiver chain (`vm.eval(`, `foo.bar.eval(`) |
| `exec_call` | high | `exec(...)`, same receiver handling. Outside Python only in a file that names `child_process`, and there also `execFile` / `execSync` |
| `pickle_loads` | high | `pickle.loads` |
| `subprocess_shell_true` | high | `subprocess.*` with `shell=True`, including across physical lines |
| `os_system` | high | `os.system` |
| `hardcoded_password` | high | an assignment of a quoted literal to a name containing `password`, any case |
| `hardcoded_secret` | high | the same for `api_key`, `apikey` or `secret` |
| `fstring_sql` | med | an f-string containing `SELECT` and an interpolation |
| `concat_sql` | med | `.execute("SELECT ... +` |
| `tls_verify_false` | med | `verify = False` |
| `weak_hash` | low | the words `md5` or `sha1` |
| `security_sensitive_symbol` | low | a symbol whose name contains `auth`, `token`, `password`, `jwt`, `session` or `crypto` |

The last one is informational. It flags nothing wrong; it marks where the
security-relevant code lives.

Two details worth knowing:

**`eval` and `exec` are resolved properly, the rest are not.** In Python files
they are found by walking the AST, so a match is a real call rather than a
substring, with a bounded lexical fallback when the file does not parse. Other
languages get the lexical path, over source with comments and string literals
masked out, so an `eval(` inside a comment does not fire. Outside Python
`exec_call` carries one more condition: the file has to name `child_process`,
searched in raw source because the module usually arrives as a string literal
that masking would blank. Every other pattern in the table is a plain regex over
one line of raw source, comments included.

**One pattern sees across lines.** `subprocess.run(` opening on one line with
`shell=True` several lines down is invisible to a per-line scan, so
`subprocess_shell_true` gets a second pass over the whole source. Continuation
is restricted to lines that begin with indentation and capped at roughly 200
characters, so a closed call cannot reach forward into an unrelated one.

## What the registry does not catch

Stated plainly, because the table above reads like more coverage than it is.

**Most languages.** The registry is shaped by Python. `pickle.loads`,
`os.system`, `subprocess(shell=True)` and `verify=False` are Python idioms;
`fstring_sql` is a Python f-string. On a JavaScript, TypeScript, Go, Rust or
Java codebase, `eval` / `exec` and the two secret patterns are most of what can
fire. A repo in one of those languages reporting few findings is reporting the
registry's shape, not its own health.

**Anything needing framework semantics.** Missing authorization on a route
handler, permissive CORS on a mutating endpoint, an unvalidated redirect, an
object reference with no ownership check. These require knowing what a route
handler is, which ones mutate, and what counts as a gate. A regex cannot do it
and this layer does not try.

**Anything needing dataflow.** Whether attacker-controlled input actually
reaches a dangerous call is not computed. `eval_call` fires the same on a
constant and on a request parameter.

**Real secret detection.** The two secret patterns match a literal assignment to
a variable named like a credential, in any case, so the constant spelling a
pinned credential usually carries is covered. That is the whole of it: they do
not know entropy, key formats, or provider prefixes, and a credential held in a
name they do not list is invisible. For secret scanning proper, run gitleaks or
trufflehog; history mode below is complementary to those, not a replacement.

**Dependencies.** No CVE lookup, no advisory feed, no SBOM. Nothing here looks
outside your source.

**False positives are expected.** `weak_hash` fires on the word `md5` anywhere,
including in a comment explaining why md5 was removed. `hardcoded_password`
fires on test fixtures and on empty placeholder credentials. The layer reports
signals for a human to read, and it is tuned to say too much rather than too
little.

**Two of them are worth knowing before you read a report.**

`exec` is not a global in JavaScript; the name belongs to `RegExp.prototype.exec`,
so the receiver-chain prefix in the pattern would otherwise match
`re.exec(expr)`, `/x/.exec(s)` and `cellPattern.exec(xml)` — ordinary parsing
code — at `high`. Outside Python the kind is gated on the file naming
`child_process`, which is where the dangerous call comes from. The gate is per
file rather than per call, so a file that both spawns a process and parses text
with regexes still reports every `exec(` in it. That residual is deliberate,
and covered by a test rather than left implicit.

The per-line patterns run on raw source, comments included. A comment that
spells out a credential assignment reports itself as a `hardcoded_secret`, and
`weak_hash` fires on a comment explaining why md5 was removed. Only the
`eval`/`exec` path masks comments and string literals; nothing else does.

## Working tree versus history

Two scan surfaces share the registry and the storage.

**Working tree** runs during `repowise init` and `repowise update`, over the
files as they exist now. Rows are stored with an empty `commit_sha`. There is no
separate command for it: `repowise security scan` without `--history` is a stub
that prints a hint, because re-scanning the working tree outside indexing would
duplicate what indexing just did.

**History** runs only on `repowise security scan --history`, walking every
tracked revision of every source file. Rows carry the SHA and author date of the
commit that introduced the match. This finds what the working tree cannot: a
credential committed in March and removed in April is absent from HEAD and
present in the clone forever.

History mode reports **only `hardcoded_password` and `hardcoded_secret`** by
default. The reasoning is asymmetry of decay. A commit that once called `eval()`
is history doing what history does — the code changed, that is the point, and
reporting every such moment across every revision buries the surface in noise. A
committed secret does not decay. It stays valid until someone rotates it, and
whoever cloned the repo still has it. Pass `--all-patterns` to get the code-smell
kinds across history too, and expect volume.

Both paths land in the same `security_findings` table, with a unique constraint
on `(repository_id, file_path, kind, line_number, commit_sha)`. Re-running either
scan is idempotent.

## Line verification

A finding's `line_number` is written at scan time, and the file moves on. A wrong
line on a security finding is worse than none: it sends the reader to innocent
code while looking authoritative. So the line is re-checked against the live file
every time a finding is served, using the stored snippet — the first 120
characters of the matched line, which is always a substring of the line it came
from.

Three outcomes, on every finding the API returns:

| `line_verified` | `line_number` | Meaning |
|---|---|---|
| `true` | a line | The snippet is on that line. Either it never moved, or it moved and the line was corrected. |
| `false` | a line | The snippet occurs more than once in the file. The line is a guess and the surface should mark it as one. |
| `false` | `null` | The snippet is gone from the file. The finding is stale; showing a line would point at unrelated code. |

`security_sensitive_symbol` is the exception. Its snippet is a bare identifier
rather than a line of code, and an identifier recurs throughout a file, so
relocating on it would land somewhere arbitrary. Those findings are checked in
place and never relocated or withdrawn: if the name is not on the stored line
they are returned unverified rather than treated as gone.

When the file cannot be read at all — the repo is not checked out where the
server expects it — the stored line is passed through unverified rather than
claimed as correct.

## Storage

One table, `security_findings`, written by both scan paths:

| Column | Notes |
|---|---|
| `file_path` | Repo-relative |
| `kind` | One of the kinds in the table above |
| `severity` | `high`, `med`, `low` — fixed per pattern |
| `snippet` | Matched line, trimmed to 120 characters; a symbol name for `security_sensitive_symbol` |
| `line_number` | As of scan time; verified at serve time |
| `commit_sha` | Empty for working-tree rows, the introducing commit for history rows |
| `commit_at` | Author date, history rows only |

Indexing against a database that has not yet migrated the table skips
persistence silently; every other write failure is a real error.

## Reading the surface honestly

A useful way to read a repo's findings, in order:

1. **Any `hardcoded_secret` or `hardcoded_password` from history mode.** These
   are the findings most likely to be both true and actionable. Rotate first,
   remove from history second.
2. **The high-severity working-tree kinds**, as a list of places to read rather
   than a list of bugs. The layer found a call; you decide whether it is
   reachable.
3. **`security_sensitive_symbol` as a map.** Where the auth and crypto code
   lives is useful context for review, and for `get_risk` when you are about to
   change one of those files.
4. **Low findings last, and sceptically.** `weak_hash` in particular is a word
   match.

And once more, because it is the whole point of this page: a low finding count
means the registry did not match. On most repos, in most languages, that is what
it means and nothing more.
