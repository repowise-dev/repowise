"""Health metrics for Dart, pinned on functions from the pub client.

Each number below was counted by hand from the source. A Dart function's
measured span is its body (the grammar keeps the signature beside it), so
NLOC counts from the line holding ``{``. Parameters are plain positional ones:
named and optional parameter counting is still moving.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file

# pub lib/src/io.dart writeTextFileIfDifferent (dependencies made positional),
# ascii_tree.dart _drawLine, validator/strict_dependencies.dart's directive loop.
_PUB = r"""void writeTextFileIfDifferent(
  String path,
  String newContent,
  Iterable<String> dependencies,
) {
  final originalText = tryReadTextFile(path);
  if (originalText != newContent) {
    writeTextFile(path, newContent);
  } else {
    final modified = tryStatFile(path)?.modified;
    if (modified != null) {
      for (final dependency in dependencies) {
        final dependencyModified = tryStatFile(dependency)?.modified;
        if (dependencyModified != null &&
            dependencyModified.isAfter(modified)) {
          log.fine('`$path` is unchanged. Touching because `$dependency` is newer.');
          touch(path);
          return;
        }
      }
    }
    log.fine('`$path` is unchanged. Not rewriting.');
  }
}

void _drawLine(
  StringBuffer buffer,
  String prefix,
  bool isLastChild,
  String? name,
  bool isRoot,
) {
  // Print lines.
  buffer.write(prefix);
  if (!isRoot) {
    if (isLastChild) {
      buffer.write(log.gray(emoji('-- ', "'-- ")));
    } else {
      buffer.write(log.gray(emoji('|-- ', '|-- ')));
    }
  }

  // Print name.
  buffer.writeln(name);
}

Iterable<Usage> findPackages(Iterable<UriBasedDirective> directives, String file) sync* {
  for (var directive in directives) {
    Uri? url;
    final uriString = directive.uri.stringValue;
    if (uriString != null) {
      url = Uri.tryParse(uriString);
    }

    // If the URL could not be parsed or it is a `package:` URL AND there
    // are no segments OR any segment are empty, it's invalid.
    if (url == null ||
        (url.scheme == 'package' &&
            (url.pathSegments.length < 2 ||
                url.pathSegments.any((s) => s.isEmpty)))) {
      errors.add(Usage.errorMessage('Invalid URL.', file, directive));
    } else if (url.scheme == 'package') {
      yield Usage(file, directive, url);
    }
  }
}
"""

# pub lib/src/command/version.dart.
_COMMAND = r"""class VersionCommand extends PubCommand {
  @override
  String get name => 'version';
  @override
  String get description => 'Print pub version.';
  @override
  String get argumentsDescription => '';

  @override
  Future<void> runProtected() async {
    log.message('Pub ${sdk.version}');
  }
}
"""


def _walk(source: str):
    fcx = walk_file("lib/sample.dart", "dart", source.encode())
    if not fcx.functions:
        pytest.skip("tree-sitter grammar for Dart unavailable")
    return fcx


def test_pub_functions() -> None:
    metrics = {f.name: (f.ccn, f.max_nesting, f.nloc, f.param_count) for f in _walk(_PUB).functions}
    assert metrics == {
        # if, if, for, if + ``&&``; the else arm holds the if/for/if ladder.
        "writeTextFileIfDifferent": (6, 4, 20, 3),
        "_drawLine": (3, 2, 11, 5),
        # for, if, if + ``|| && ||``, else if; the closure adds no decision.
        "findPackages": (8, 2, 17, 2),
    }


def test_compound_condition() -> None:
    conditions = [
        (f.name, c.line, c.operator_count, c.flat_chain)
        for f in _walk(_PUB).functions
        for c in f.complex_conditions
        if c.operator_count >= 3
    ]
    assert conditions == [("findPackages", 57, 3, False)]


def test_class_members() -> None:
    [cls] = _walk(_COMMAND).classes
    # Three getters and one method; the blank line is not code.
    assert (cls.name, cls.method_count, cls.total_nloc) == ("VersionCommand", 4, 12)
