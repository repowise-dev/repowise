"""A fake tracker for the ``issue-source`` tests, loaded as a plugin.

It reads ``ENG-123`` keys from each commit's message with the exported helper
and looks them up in an in-memory tracker, as a Jira or Linear plugin would
look them up over the network.
"""

from __future__ import annotations

from repowise.core.issues import IssueLink, IssueRequest, SourceRefused, issue_sources, tracker_keys
from repowise.core.settings.plugins import plugin_setting, register_settings

PROJECTS = plugin_setting("faketracker", "projects", "str", "ENG", "Project keys to read.")
TOKEN = plugin_setting("faketracker", "token", "str", None, "Tracker API token.", secret=True)

SECRET = "trk-secret-token"

#: key -> (type, status, title); tests edit it between syncs.
TRACKER: dict[str, tuple[str, str, str]] = {}


class FakeTracker:
    name = "fake-tracker"
    settings = (PROJECTS, TOKEN)

    def fetch(self, request: IssueRequest):
        if (request.credentials or {}).get(TOKEN.key) != SECRET:
            raise SourceRefused("the tracker refused the token; set FAKE_TRACKER_TOKEN")
        projects = str(request.settings.get(PROJECTS.key, "ENG")).split(",")
        for commit in request.commits:
            for key in tracker_keys(f"{commit.subject}\n{commit.body}", projects=projects):
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
    register_settings(PROJECTS, TOKEN)
    issue_sources.register(FakeTracker.name, FakeTracker())
