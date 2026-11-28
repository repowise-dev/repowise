"""A fake tracker for the ``issue-source`` contract tests, loaded as a plugin.

It reads ``ENG-123`` keys from each commit's message with the exported helper
and looks them up in an in-memory tracker, as a Jira or Linear plugin would
look them up over the network.
"""

from __future__ import annotations

from repowise.core.issues import IssueLink, IssueRequest, SourceRefused, issue_sources, tracker_keys

SECRET = "trk-secret-token"

#: key -> (type, status, title); tests edit it between syncs.
TRACKER: dict[str, tuple[str, str, str]] = {}


class FakeTracker:
    name = "fake-tracker"

    def fetch(self, request: IssueRequest):
        if request.credentials != SECRET:
            raise SourceRefused("the tracker refused the token; set FAKE_TRACKER_TOKEN")
        for commit in request.commits:
            for key in tracker_keys(f"{commit.subject}\n{commit.body}", projects=["ENG"]):
                if key in TRACKER:
                    kind, status, title = TRACKER[key]
                    yield IssueLink(
                        key=key,
                        type=kind,
                        sha=commit.sha,
                        status=status,
                        title=title,
                        url=f"https://tracker.example/{key}",
                        basis=f"message names {key}",
                    )


def register() -> None:
    issue_sources.register(FakeTracker.name, FakeTracker())
