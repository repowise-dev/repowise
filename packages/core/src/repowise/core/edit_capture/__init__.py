"""Edit-time AI attribution: capture agent edits as hashes, bind them to commits.

Opt-in (``attribution.capture``, see :mod:`repowise.core.attribution`). A hook
records which lines an agent wrote as content hashes in a local sidecar under
``.repowise/attribution/``; ``repowise update`` binds them to the commits that
added those lines and, at ``capture: notes``, writes git-ai v3 notes. Prompts,
code text and person identities are never stored.

* :mod:`.hashing` - the one line-normalisation and hash function.
* :mod:`.edits` - one edit extractor per agent, shared by the live hook and the
  transcript adapters, plus the hook-payload capture adapters.
* :mod:`.store` - the consent gate and the sidecar files.
* :mod:`.binder` - commit binding (precision first: a miss beats a false hit).
* :mod:`.notes` - git-ai v3 note writing and removal of only our own notes.
* :mod:`.backfill` - past commits bound from local agent transcripts, on request.

Every module here is stdlib-only at import: the hook path imports it.
"""
