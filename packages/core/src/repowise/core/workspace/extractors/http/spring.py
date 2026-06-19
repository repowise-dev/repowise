"""Spring (Java) HTTP provider dialect.

Handles method-level ``@GetMapping("/users")`` annotations and stitches on the
nearest preceding class-level ``@RequestMapping("/api/v1")`` prefix.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..langs import JAVA
from .dialect import build_provider_contract, nearest_prefix

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# @GetMapping("/path"), @PostMapping(value="/path"), etc.
_SPRING_METHOD_RE = re.compile(
    r"""@(Get|Post|Put|Delete|Patch)Mapping\s*\(\s*(?:value\s*=\s*)?['"]([^'"]+)['"]""",
    re.IGNORECASE,
)

# Class-level prefix: @RequestMapping("/api/v1")
_SPRING_CLASS_RE = re.compile(
    r"""@RequestMapping\s*\(\s*(?:value\s*=\s*)?['"]([^'"]+)['"]""",
)


class SpringDialect:
    name = "spring"
    extensions = JAVA

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        # Collect every class-level @RequestMapping so each method annotation
        # gets the prefix from its nearest preceding class declaration rather
        # than always the first one in the file.
        class_mappings: list[tuple[int, str]] = [
            (cm.start(), cm.group(1).rstrip("/")) for cm in _SPRING_CLASS_RE.finditer(content)
        ]

        out: list[Contract] = []
        for m in _SPRING_METHOD_RE.finditer(content):
            method = m.group(1).upper()
            path_raw = m.group(2)
            prefix = nearest_prefix(class_mappings, m.start())
            if prefix:
                path_raw = prefix + "/" + path_raw.lstrip("/")
            c = build_provider_contract(ctx, method=method, path_raw=path_raw, framework="spring")
            if c is not None:
                out.append(c)
        return out
