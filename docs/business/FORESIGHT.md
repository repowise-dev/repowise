<div align="center">

# Repowise Foresight

### Know what a change will need before you open a file.

<p>Every fix your team has shipped, every file someone forgot, every test someone had to<br />
go back and update is already written down in git. Foresight learned from years of that<br />
history across many open-source projects, and now reads yours.</p>

<a href="https://www.repowise.dev/foresight?src=oss_docs_foresight#waitlist"><img src="https://img.shields.io/badge/FORESIGHT-join_the_waitlist-0A0A0A?style=for-the-badge&labelColor=B9A3E3" alt="Join the Foresight waitlist" /></a>
<a href="https://cal.com/repowise-demo/30min"><img src="https://img.shields.io/badge/BOOK_A_DEMO-30_min-0A0A0A?style=for-the-badge&labelColor=F59520" alt="Book a Foresight demo" /></a>

<sub>Coming to Repowise hosted and enterprise · no LLM · milliseconds on a CPU · air-gap ready</sub>

</div>

---

## The bug report that names no file

A customer writes in:

> **Checkout sometimes charges twice after a timeout.**
> On a slow connection the payment page times out, the customer tries again, and the
> card is charged twice.

Keyword search, the way most agents and people find code today, returns whatever says
"checkout":

```text
web/checkout_page.tsx         matches "checkout" 14 times
tests/test_checkout_ui.py     matches "checkout" 9 times
README.md                     matches "charges" 3 times
web/checkout.css              matches "checkout" 6 times
```

Foresight answers with where the fix will actually land, and why:

```text
payments/retry.py      likely   fixed for a similar timeout bug 3 weeks ago
billing/ledger.py      possible changes with retry.py 9 of 11 times
payments/gateway.py    possible raises the Timeout that retry.py catches
```

The issue never mentions `retry.py`. The history does. Your agent starts on the right
file instead of reading the checkout page first.

<sub>An illustrative example. Measured results are below.</sub>

---

## What it answers

The open-source index knows what your code *is*. Foresight predicts what a piece of
work will *need*.

| | Foresight tells you | For example |
|---|---|---|
| **Where the fix goes** | Paste a bug report or a stack trace and get the files, and the functions inside them, the fix will touch | `payments/retry.py`: fixed for a similar timeout bug |
| **What the change forgot** | Mid-edit, before any commit message, the file that usually changes with yours and is missing from the diff | `billing/ledger.py`: changes with `retry.py` 9 of 11 times |
| **What still holds the old name** | Rename or remove an identifier or a literal, and see the files that still reference it | `config/settings.py`: still references `SESSION_TTL` |
| **Tests to update** | The test files that will need edits too, ranked, so the pull request arrives complete | `test_retry.py`: moves with `retry.py` |
| **Code health that learns** | A health signal whose weights are learned from how real projects needed fixes | `payments/processor.py`: first in line for review |
| **Confidence you can act on** | A calibrated band and the evidence behind every answer, so an agent or reviewer knows when to trust it | `likely`: 3 signals agree |

---

## Measured on real bugs

Every result was run on the code as it stood before the fix, on projects the models
never trained on.

<table>
<tr>
<td align="center" width="25%"><h2>87%</h2><sub><b>right file in the top five</b><br />SWE-bench Verified, 493 real bugs, no LLM</sub></td>
<td align="center" width="25%"><h2>2.6x</h2><sub><b>better where search fails</b><br />when the report names no file: right file first 42% of the time, against 16% for keyword search</sub></td>
<td align="center" width="25%"><h2>3 in 4</h2><sub><b>right file in the top five on real issues</b><br />114 real openclaw bugs: 75%, against 48% for keyword search</sub></td>
<td align="center" width="25%"><h2>6 of 7</h2><sub><b>unseen projects won</b><br />ahead of keyword search on 6 projects it never saw, tied on the seventh</sub></td>
</tr>
</table>

That last number matters most. Foresight is trained once, on public history, and then
works on code it has never seen. It does not need months of your data before it is
useful, and it never trains on your code.

---

## How it runs

| | |
|---|---|
| **No LLM** | Small learned models. No prompt and no tokens, and the same input gets the same answer every time. |
| **CPU only** | Runs on the machine that already runs Repowise. No GPU. |
| **Milliseconds** | Under half a second on a laptop, so it can run on every edit, commit and pull request. |
| **Air-gap ready** | Inference is local. Self-hosted and air-gapped deployments keep code inside your network. |
| **Reads your history** | Each answer draws on your repository's own co-change, fix and ownership record from the Repowise index. |
| **Explains itself** | Every ranked file carries the reason it ranked, in plain words. |
| **Keeps learning** | We retrain on new public history, so each release has learned from more real changes than the last. |

---

## Where you see it

Foresight sits on top of the Repowise index and reaches you through the surfaces you
already use:

- **Your coding agent**, through the same Repowise MCP tools, so it opens the right
  file first and reads less to get there.
- **Pull requests**, where the files and tests a change forgot show up before review.
- **The dashboard**, next to change risk, code health and each Change Record.

The open-source engine stays complete without it. Foresight is the learned layer on
top, for teams that want the next step predicted as well as the current state
explained.

---

## Questions

**When can we use it?** Foresight is coming to Repowise hosted and enterprise. Teams on
the waitlist get access first, in the order it opens.

**Does our code leave our network?** No. Foresight makes no model-provider call. The
models are trained ahead of time on public open-source history and run on a CPU next
to the index, so enterprise deployments run it self-hosted or fully air-gapped.

**Is it open source?** No. The Repowise engine is AGPL-3.0 and fully usable without
Foresight. Foresight is part of the hosted and commercial products. See
[COMMERCIAL.md](COMMERCIAL.md).

**Does it replace our coding agent or our reviewers?** No. It gives both a better
starting point: the right files, the forgotten ones, and how sure it is.

---

<div align="center">

### Be first to run Foresight on your code.

<a href="https://www.repowise.dev/foresight?src=oss_docs_foresight#waitlist"><img src="https://img.shields.io/badge/FORESIGHT-join_the_waitlist-0A0A0A?style=for-the-badge&labelColor=B9A3E3" alt="Join the Foresight waitlist" /></a>

<sub>Questions first? <a href="https://cal.com/repowise-demo/30min">Book a 30-minute demo</a> or write to <a href="mailto:hello@repowise.dev">hello@repowise.dev</a>.</sub>

</div>
