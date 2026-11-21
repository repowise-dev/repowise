"""The ``report.first_look.*`` settings as the report's callers read them."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class FirstLookSettings:
    #: Write the report at all.
    enabled: bool = True
    #: Allow the one model call when a provider is configured and prose is on.
    llm: bool = True
    #: Model override; ``""`` means the provider's cheap model, else its default.
    model: str = ""


def first_look_settings(repo_root: Path | str, config: dict[str, Any] | None = None) -> FirstLookSettings:
    """The effective settings for *repo_root*: its config file, then the defaults.

    *config* is the parsed ``.repowise/config.yaml`` when the caller holds it.
    """
    from repowise.core.settings import registry
    from repowise.core.settings.writers import current, read_config

    if config is None:
        config = read_config(repo_root, registry.get("report.first_look.enabled"))

    def value(key: str) -> Any:
        return current(repo_root, f"report.first_look.{key}", config=config).value

    return FirstLookSettings(
        enabled=bool(value("enabled")), llm=bool(value("llm")), model=str(value("model") or "")
    )


__all__ = ["FirstLookSettings", "first_look_settings"]
