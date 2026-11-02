"""Importable fixture plugin used to exercise real spawn-pool extraction."""

from __future__ import annotations

import os
import time
from pathlib import Path

from repowise.core.ingestion.document_extractors import document_extractors


class SpawnExtractor:
    extensions = frozenset({".xyz"})

    def __init__(self) -> None:
        self.version = os.environ.get("REPOWISE_TEST_EXTRACTOR_VERSION", "1")
        self.unpicklable = lambda: None

    def extract(self, path: Path, data: bytes) -> str:
        calls_file = os.environ.get("REPOWISE_TEST_EXTRACTOR_CALLS")
        if calls_file:
            with Path(calls_file).open("a", encoding="utf-8") as stream:
                stream.write(f"{path.name}\n")
        if os.environ.get("REPOWISE_TEST_EXTRACTOR_FAIL") == "1":
            raise ValueError("fixture extraction failure")
        if os.environ.get("REPOWISE_TEST_EXTRACTOR_CRASH") == "1":
            os._exit(17)
        if os.environ.get("REPOWISE_TEST_EXTRACTOR_HANG") == "1":
            while True:
                time.sleep(1)
        text = data.decode("utf-8", errors="replace")
        padding = int(os.environ.get("REPOWISE_TEST_EXTRACTOR_PADDING", "0"))
        return f"# Extracted document\n\n{text}\n{'x' * padding}"


def register() -> None:
    document_extractors.register("spawn-test-extractor", SpawnExtractor())
