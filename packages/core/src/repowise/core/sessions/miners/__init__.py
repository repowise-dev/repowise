"""Feature miners over the normalized session-event stream.

Each miner is a consumer of :class:`repowise.core.sessions.Event`; none of
them parse transcripts themselves. Import miners directly (they pull in
their feature's dependencies, which the base ``core.sessions`` package
deliberately does not).
"""
