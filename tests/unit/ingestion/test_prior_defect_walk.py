"""What the prior-defect walk carries, on real temporary repositories.

The walk parses a ``git log --name-only`` whose format now ends with a
multi-line commit body. A path line and a line of prose are indistinguishable
by shape, so the boundary between them is a byte git will not emit inside a
message — and getting that wrong would not raise, it would silently stop
yielding paths and zero a shipped biomarker. That is what most of this file is
about.
"""

from __future__ import annotations

from dataclasses import replace

from repowise.core.ingestion.git_indexer._constants import _MAX_COMMIT_BODY_BYTES
from repowise.core.ingestion.git_indexer.fix_events import build_fix_events
from repowise.core.ingestion.git_indexer.prior_defects import (
    FixWalk,
    collect_fix_commits,
    compute_prior_defects,
)


def _repo(tmp_path):
    import git as gitpython

    repo = gitpython.Repo.init(tmp_path)
    repo.config_writer().set_value("user", "email", "t@example.com").release()
    repo.config_writer().set_value("user", "name", "T").release()
    return repo


def _write(repo, tmp_path, name, content, message):
    (tmp_path / name).write_text(content, encoding="utf-8")
    repo.index.add([name])
    return repo.index.commit(message).hexsha


def _walk(tmp_path, paths) -> FixWalk:
    import git as gitpython

    return collect_fix_commits(gitpython.Repo(tmp_path), set(paths), as_of_ts=None)


_PROSE_BODY = """The rebuild refused whenever the index held more rows than
wiki_pages could account for.

- ensure_index prunes orphans on every open
- doctor --repair steps over a failed schema upgrade

Closes #1309
"""


class TestCommitProse:
    def test_subject_and_body_ride_the_walk(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        _write(repo, tmp_path, "a.py", "x = 1\n", "feat: add a")
        _write(repo, tmp_path, "a.py", "x = 2\n", f"fix: repair the index\n\n{_PROSE_BODY}")

        (fix,) = _walk(tmp_path, ["a.py"]).fixes

        assert fix.subject == "fix: repair the index"
        assert "ensure_index prunes orphans" in fix.body
        assert "Closes #1309" in fix.body

    def test_a_body_that_looks_like_a_path_does_not_eat_the_path_block(self, tmp_path) -> None:
        """The regression the terminator exists for.

        A body line spelled like a repo-relative path is exactly what a squash
        description contains, and the ``--name-only`` block that follows has no
        shape of its own to be recognised by.
        """
        repo = _repo(tmp_path)
        _write(repo, tmp_path, "a.py", "x = 1\n", "feat: add a")
        _write(
            repo,
            tmp_path,
            "a.py",
            "x = 2\n",
            "fix: repair the index\n\nTouched a.py and also b.py\n\nnot/a/real/path.py\n",
        )

        (fix,) = _walk(tmp_path, ["a.py"]).fixes

        assert fix.paths == ["a.py"]
        assert "not/a/real/path.py" in fix.body

    def test_a_subject_only_commit_has_an_empty_body(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        _write(repo, tmp_path, "a.py", "x = 1\n", "feat: add a")
        _write(repo, tmp_path, "a.py", "x = 2\n", "fix: terse")

        (fix,) = _walk(tmp_path, ["a.py"]).fixes

        assert fix.subject == "fix: terse"
        assert fix.body == ""

    def test_the_body_is_byte_capped(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        _write(repo, tmp_path, "a.py", "x = 1\n", "feat: add a")
        long_body = "why " * _MAX_COMMIT_BODY_BYTES
        _write(repo, tmp_path, "a.py", "x = 2\n", f"fix: verbose\n\n{long_body}")

        (fix,) = _walk(tmp_path, ["a.py"]).fixes

        assert len(fix.body.encode("utf-8")) <= _MAX_COMMIT_BODY_BYTES

    def test_the_subject_is_byte_capped_too(self, tmp_path) -> None:
        """Git bounds a body by convention and a subject by nothing at all.

        Both are persisted, so an unbounded one is the one that matters.
        """
        repo = _repo(tmp_path)
        _write(repo, tmp_path, "a.py", "x = 1\n", "feat: add a")
        _write(repo, tmp_path, "a.py", "x = 2\n", "fix: " + "S" * (_MAX_COMMIT_BODY_BYTES * 4))

        (fix,) = _walk(tmp_path, ["a.py"]).fixes

        assert len(fix.subject.encode("utf-8")) <= _MAX_COMMIT_BODY_BYTES

    def test_a_separator_inside_a_subject_does_not_change_what_counts_as_a_fix(
        self, tmp_path
    ) -> None:
        """The field separator is a byte, and a commit message may contain it.

        Cutting the subject at the first one would drop the keyword that
        classifies the commit, so the file would silently lose a defect count.
        Both boundaries are therefore taken from the right.
        """
        repo = _repo(tmp_path)
        _write(repo, tmp_path, "a.py", "x = 1\n", "feat: add a")
        _write(repo, tmp_path, "a.py", "x = 2\n", "cleanup\x1f resolve the crash\n\nbody")

        walk = _walk(tmp_path, ["a.py"])

        assert [f.paths for f in walk.fixes] == [["a.py"]]
        assert walk.fixes[0].subject == "cleanup\x1f resolve the crash"
        assert walk.fixes[0].body == "body"


class TestExistingConsumersAreUnchanged:
    """Both shipped consumers must produce the same output as before.

    Asserted by construction rather than by eye: run each consumer over the
    walk and over a copy with the new fields blanked, and require the results
    to be equal. If either ever starts reading commit prose, this fails.
    """

    def _both(self, tmp_path, files):
        import git as gitpython

        walk = _walk(tmp_path, files)
        blanked = FixWalk(
            fixes=[replace(f, subject="", body="") for f in walk.fixes],
            oldest_fix_ts=walk.oldest_fix_ts,
        )
        repo = gitpython.Repo(tmp_path)
        return (
            (build_fix_events(walk), build_fix_events(blanked)),
            (
                compute_prior_defects(repo, set(files), as_of_ts=None, walk=walk),
                compute_prior_defects(repo, set(files), as_of_ts=None, walk=blanked),
            ),
        )

    def test_fix_events_and_prior_defects_ignore_the_prose(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        _write(repo, tmp_path, "a.py", "x = 1\n", "feat: add a")
        (tmp_path / "b.py").write_text("y = 1\n", encoding="utf-8")
        (tmp_path / "a.py").write_text("x = 2\n", encoding="utf-8")
        repo.index.add(["a.py", "b.py"])
        repo.index.commit(f"fix: correct both\n\n{_PROSE_BODY}")

        (events, blanked_events), (counts, blanked_counts) = self._both(
            tmp_path, ["a.py", "b.py"]
        )

        assert events == blanked_events
        assert counts == blanked_counts
        # And the pass still counts what it counted before the prose arrived.
        assert counts.counts == {"a.py": 1, "b.py": 1}
        assert [r["file_path"] for r in events] == ["a.py", "b.py"]
