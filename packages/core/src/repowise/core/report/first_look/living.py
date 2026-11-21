"""The living report: the first look as the index has it now, with each item's status.

:func:`build_living` collects the report at the index now (the one collect
:func:`.write.build_report` runs), compares it with the stored report (the
baseline, by item id as :func:`.staleness.compare` does) and reads each item's
status from the stores a mark writes to (:func:`.marks.item_statuses`). An
item the index no longer lists stays in place while a person's answer holds it
(done, acknowledged, a false positive, snoozed), and moves to the baseline's
``resolved`` list once nothing does. An item the baseline never had is marked
``new``. Prose a model wrote for the baseline is kept for every item, section
and summary whose facts have not changed since.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .marks import ItemStatus, item_statuses
from .model import FirstLookReport, Item, SectionBlock
from .rank import order_items, section_key, top_items
from .staleness import compare
from .text import WRITER, item_text, template_prose


@dataclass(frozen=True)
class LivingReport:
    #: The report now: current items plus answered items the index dropped.
    report: FirstLookReport
    statuses: Mapping[str, ItemStatus]
    #: Ids the baseline did not have.
    new: frozenset[str] = frozenset()
    #: Ids the baseline had with other facts (a moved fingerprint).
    changed: frozenset[str] = frozenset()
    #: Ids the index no longer finds that stay listed for a person's answer.
    carried: frozenset[str] = frozenset()
    #: The stored report this one is compared with; ``None`` when there is none.
    baseline: FirstLookReport | None = None
    #: Baseline items the index no longer finds and nobody answered.
    resolved: tuple[Item, ...] = ()
    #: Whether this call stored the baseline (there was none).
    wrote_baseline: bool = field(default=False, compare=False)

    def status(self, item_id: str) -> ItemStatus:
        return self.statuses.get(item_id) or ItemStatus()

    def change(self, item_id: str) -> str | None:
        """``new``, ``changed`` or ``resolved`` against the baseline; ``None`` when the same."""
        if item_id in self.new:
            return "new"
        if item_id in self.carried:
            return "resolved"
        return "changed" if item_id in self.changed else None

    def to_json(self) -> dict[str, Any]:
        data = self.report.to_json()
        for section in data["sections"]:
            for item in section["items"]:
                item.update(self.status(item["id"]).to_json())
                item["change"] = self.change(item["id"])
        if _model_written(self.baseline):
            # The model's wording dates from the baseline, not from this read.
            data["prose_source"]["written_at"] = self.baseline.stamp.created_at
        texts = _texts(self.baseline) if self.baseline is not None else {}
        data["baseline"] = {
            "stamp": self.baseline.stamp.to_json() if self.baseline is not None else None,
            "resolved": [
                {
                    **i.to_json(),
                    **ItemStatus("resolved").to_json(),
                    "change": "resolved",
                    "text": texts.get(i.id) or item_text(i),
                }
                for i in self.resolved
            ],
        }
        return data


def _texts(report: FirstLookReport) -> dict[str, str]:
    return dict((report.prose or {}).get("items") or {})


async def load_baseline(
    session: Any, repository_id: str, repo_root: Path | None
) -> FirstLookReport | None:
    """The stored report: the checkout's file, else the index row."""
    from .store import load_report, read_report_file

    stored = read_report_file(repo_root) if repo_root is not None else None
    return stored or await load_report(session, repository_id)


def _with_items(current: FirstLookReport, carried: list[Item]) -> FirstLookReport:
    """*current* with *carried* back in their sections, ranked."""
    if not carried:
        return current
    blocks = {b.key: b for b in current.sections}
    for item in carried:
        block = blocks.get(item.section) or SectionBlock(item.section, ())
        blocks[item.section] = dataclasses.replace(block, items=(*block.items, item))
    return dataclasses.replace(
        current,
        sections=tuple(
            dataclasses.replace(b, items=order_items(b.items))
            for b in sorted(blocks.values(), key=lambda b: section_key(b.key))
        ),
    )


def _open_top(report: FirstLookReport, statuses: Mapping[str, ItemStatus]) -> tuple[str, ...]:
    """"Do these first" over the open items only."""
    open_blocks = [
        dataclasses.replace(
            b, items=tuple(i for i in b.items if statuses.get(i.id, ItemStatus()).status == "open")
        )
        for b in report.sections
    ]
    return top_items(open_blocks)


def _model_written(baseline: FirstLookReport | None) -> bool:
    return baseline is not None and (baseline.prose or {}).get("writer") not in (None, WRITER)


def _kept_sections(
    report: FirstLookReport, baseline: FirstLookReport, same: set[str]
) -> dict[str, str]:
    """The baseline's section intros whose sections hold the same unchanged items."""
    was = (baseline.prose or {}).get("sections") or {}
    old = {b.key: {i.id for i in b.items} for b in baseline.sections}
    out = {}
    for block in report.sections:
        ids = {i.id for i in block.items}
        if was.get(block.key) and ids <= same and ids == old.get(block.key):
            out[block.key] = was[block.key]
    return out


def _prose(report: FirstLookReport, baseline: FirstLookReport | None) -> dict[str, Any]:
    """Template text, with a model's baseline text wherever its facts still hold."""
    prose = template_prose(report)
    if not _model_written(baseline):
        return prose
    was = baseline.prose
    before = {i.id: i.fingerprint for i in baseline.items()}
    same = {i.id for i in report.items() if before.get(i.id) == i.fingerprint}
    items = _texts(baseline)
    prose["items"] = {
        k: (items[k] if k in same and items.get(k) else v) for k, v in prose["items"].items()
    }
    prose["sections"].update(_kept_sections(report, baseline, same))
    if set(before) == {i.id for i in report.items()} == same:
        prose.update({k: was[k] for k in ("summary", "headline") if was.get(k)})
    prose["writer"] = was["writer"]
    return prose


async def _current(
    session: Any, repository_id: str, root: Path | None, store_missing: bool, app_url: str | None
) -> tuple[FirstLookReport, FirstLookReport | None, bool]:
    """The report at the index now, the stored one, and whether this call stored it."""
    from .write import build_report, write_first_look

    baseline = await load_baseline(session, repository_id, root)
    if baseline is None and store_missing:
        current = await write_first_look(session, repository_id, root, app_url=app_url)
        return current, current, True
    return await build_report(session, repository_id, root), baseline, False


def _finish(
    current: FirstLookReport,
    baseline: FirstLookReport | None,
    carried: list[Item],
    statuses: Mapping[str, ItemStatus],
) -> FirstLookReport:
    """*current* with the answered items it dropped, open-only "Do these first",
    and the prose (a model's where it still holds, stamped as the model's)."""
    report = _with_items(current, carried)
    report = dataclasses.replace(report, top=_open_top(report, statuses))
    stamp = report.stamp
    if _model_written(baseline):
        stamp = dataclasses.replace(stamp, generator=baseline.stamp.generator, cost=baseline.stamp.cost)
    return dataclasses.replace(report, stamp=stamp, prose=_prose(report, baseline))


async def build_living(
    session: Any,
    repository_id: str,
    repo_root: Path | str | None,
    *,
    store_missing: bool = False,
    app_url: str | None = None,
) -> LivingReport:
    """The report at the index now, against the stored one, with every item's status.

    With *store_missing* and no stored report, the report collected now is
    stored first (as ``init`` writes it, *app_url* in its rendered files) and
    is its own baseline.
    """
    root = Path(repo_root) if repo_root is not None else None
    current, baseline, wrote = await _current(session, repository_id, root, store_missing, app_url)
    if baseline is None:
        statuses = await item_statuses(session, repository_id, current.items())
        report = _finish(current, None, [], statuses)
        return LivingReport(report=report, statuses=statuses)
    found = compare(baseline, current.items(), unknown=current.unavailable)
    now_ids = {i.id for i in current.items()}
    dropped = [i for i in baseline.items() if i.id not in now_ids]
    # A section the index cannot read now keeps the baseline's items as they were.
    statuses = await item_statuses(
        session,
        repository_id,
        [*current.items(), *dropped],
        gone=[i.id for i in dropped if i.section not in found.unknown],
    )
    carried = [i for i in dropped if statuses[i.id].status != "resolved"]
    return LivingReport(
        report=_finish(current, baseline, carried, statuses),
        statuses=statuses,
        new=frozenset(i.id for i in found.new),
        changed=frozenset(found.changed),
        carried=frozenset(i.id for i in carried),
        baseline=baseline,
        resolved=tuple(i for i in dropped if statuses[i.id].status == "resolved"),
        wrote_baseline=wrote,
    )


__all__ = ["LivingReport", "build_living", "load_baseline"]
