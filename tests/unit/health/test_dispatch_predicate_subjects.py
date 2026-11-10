"""An ``if`` chain whose every arm is a predicate on one value is a dispatch.

The predicate names its subject inside a call or a type test: ``!strcasecmp(u,
"kb")``, ``x is A``, ``\\is_object($val)``, ``[value isKindOfClass:...]``.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file
from repowise.core.analysis.health.complexity.dispatch import DISPATCH_SHARE

_EXT = {"c": "c", "dart": "dart", "php": "php", "objectivec": "m"}


def _share(language: str, source: str, name: str) -> float:
    fcx = walk_file(f"/tmp/sample.{_EXT[language]}", language, source.encode())
    if not fcx.functions:
        pytest.skip(f"{language} grammar unavailable")
    return next(fc for fc in fcx.functions if fc.name == name).dispatch_share


_C = """
long unit(const char *u) {
    long mul;
    if (*u == '\\0' || !strcasecmp(u, "b")) {
        mul = 1;
    } else if (!strcasecmp(u, "k")) {
        mul = 1000;
    } else if (strcmp(u, "kb") == 0) {
        mul = 1024;
    } else {
        mul = 0;
    }
    return mul;
}

long mixed(const char *u, const char *v) {
    long mul;
    if (!strcasecmp(u, "b")) {
        mul = 1;
    } else if (!strcasecmp(v, "k")) {
        mul = 1000;
    } else if (!strcasecmp(u, "kb")) {
        mul = 1024;
    } else {
        mul = 0;
    }
    return mul;
}
"""

_DART = """
Object bind(Object node) {
  if (node is DynamicMap) {
    return node;
  }
  if (node is DynamicList) {
    return node;
  }
  if (node is Loop) {
    return node;
  }
  return node;
}

Object chain(Object node) {
  if (node is A) {
    return 1;
  } else if (node is! B) {
    return 2;
  } else if (node == 3) {
    return 3;
  }
  return 0;
}
"""

_PHP = """<?php
function describe($val) {
    if (null === $val || \\is_scalar($val)) {
        return 'scalar';
    } elseif ($val instanceof \\DateTimeInterface) {
        return 'date';
    } elseif (\\is_object($val)) {
        return 'object';
    } elseif (is_array($val)) {
        return 'array';
    }
    return gettype($val);
}
"""

_OBJC = """
NSArray * pairs(NSString *key, id value) {
    NSMutableArray *out = [NSMutableArray array];
    if ([value isKindOfClass:[NSDictionary class]]) {
        [out addObject:key];
    } else if ([value isKindOfClass:[NSArray class]]) {
        [out addObject:value];
    } else if ([value isMemberOfClass:[NSSet class]]) {
        [out addObject:key];
    } else {
        [out addObject:value];
    }
    return out;
}
"""


def test_c_string_compare_chain_is_one_dispatch() -> None:
    assert _share("c", _C, "unit") >= DISPATCH_SHARE


def test_c_compare_chain_over_two_strings_is_not() -> None:
    assert _share("c", _C, "mixed") == 0.0


@pytest.mark.parametrize("name", ["bind", "chain"])
def test_dart_type_tests_are_one_dispatch(name: str) -> None:
    assert _share("dart", _DART, name) >= DISPATCH_SHARE


def test_php_type_predicate_chain_is_one_dispatch() -> None:
    assert _share("php", _PHP, "describe") >= DISPATCH_SHARE


def test_objc_kind_of_class_chain_is_one_dispatch() -> None:
    assert _share("objectivec", _OBJC, "pairs") >= DISPATCH_SHARE
