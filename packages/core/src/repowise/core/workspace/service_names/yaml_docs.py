"""Every YAML document in a file, or none when it does not parse."""

from __future__ import annotations

from typing import Any


def load_yaml_docs(text: str) -> list[Any]:
    """The file's documents; ``[]`` for a template or anything else that does not parse."""
    import yaml

    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    try:
        return [d for d in yaml.load_all(text, Loader=loader) if d is not None]
    except yaml.YAMLError:
        return []
