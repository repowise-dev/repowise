"""Health metrics for PHP, pinned on methods from monolog.

Each number below was counted by hand from the source. A PHP method's span
starts at its signature and its NLOC counts the body from the ``{`` line.
``??`` is not a decision here, as in TypeScript. Only the parameter count is
pinned, not how many are typed or scalar: PHP type reading is still moving.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file

# monolog Handler/PHPConsoleHandler.php getRecordTags, Processor/
# PsrLogMessageProcessor.php __invoke (three type arms dropped),
# Handler/NativeMailerHandler.php mail, Handler/DoctrineCouchDBHandler.php.
_MONOLOG = r"""<?php

class PHPConsoleHandler
{
    private function getRecordTags(LogRecord $record): array
    {
        $tags = null;
        $filteredContext = [];
        if ($record->context !== []) {
            $filteredContext = $record->context;
            foreach ($this->options['debugTagsKeysInContext'] as $key) {
                if (isset($filteredContext[$key])) {
                    $tags = $filteredContext[$key];
                    if ($key === 0) {
                        array_shift($filteredContext);
                    } else {
                        unset($filteredContext[$key]);
                    }
                    break;
                }
            }
        }

        return [$tags ?? $record->level->toPsrLogLevel(), $filteredContext];
    }
}

class PsrLogMessageProcessor
{
    public function __invoke(LogRecord $record): LogRecord
    {
        if (false === strpos($record->message, '{')) {
            return $record;
        }

        $replacements = [];
        foreach ($record->context as $key => $val) {
            $placeholder = '{' . $key . '}';
            if (strpos($record->message, $placeholder) === false) {
                continue;
            }

            if (null === $val || \is_scalar($val) || (\is_object($val) && method_exists($val, "__toString"))) {
                $replacements[$placeholder] = $val;
            } elseif (\is_array($val)) {
                $replacements[$placeholder] = 'array'.Utils::jsonEncode($val, null, true);
            } else {
                $replacements[$placeholder] = '['.\gettype($val).']';
            }
        }

        return $record->with(message: strtr($record->message, $replacements));
    }
}

class NativeMailerHandler
{
    protected function mail(string $to, string $subject, string $content, string $headers, string $parameters): void
    {
        mail($to, $subject, $content, $headers, $parameters);
    }
}

class DoctrineCouchDBHandler extends AbstractProcessingHandler
{
    private CouchDBClient $client;

    public function __construct(CouchDBClient $client, int|string|Level $level = Level::Debug, bool $bubble = true)
    {
        $this->client = $client;
        parent::__construct($level, $bubble);
    }

    /**
     * @inheritDoc
     */
    protected function write(LogRecord $record): void
    {
        $this->client->postDocument($record->formatted);
    }

    protected function getDefaultFormatter(): FormatterInterface
    {
        return new NormalizerFormatter;
    }
}
"""


def _walk(source: str):
    fcx = walk_file("src/Sample.php", "php", source.encode())
    if not fcx.functions:
        pytest.skip("tree-sitter grammar for PHP unavailable")
    return fcx


def test_monolog_methods() -> None:
    metrics = {f.name: (f.ccn, f.max_nesting, f.nloc, f.param_count) for f in _walk(_MONOLOG).functions}
    assert metrics == {
        # if, foreach, if, if: if > foreach > if > if.
        "getRecordTags": (5, 4, 19, 1),
        # if, foreach, if, if + ``|| || &&``, elseif.
        "__invoke": (9, 2, 20, 1),
        "mail": (1, 0, 3, 5),
        "__construct": (1, 0, 4, 3),
        "write": (1, 0, 3, 1),
        "getDefaultFormatter": (1, 0, 3, 0),
    }


def test_compound_condition() -> None:
    conditions = [
        (f.name, c.line, c.operator_count, c.flat_chain)
        for f in _walk(_MONOLOG).functions
        for c in f.complex_conditions
        if c.operator_count >= 3
    ]
    assert conditions == [("__invoke", 43, 3, False)]


def test_class_members() -> None:
    classes = {c.name: (c.method_count, c.total_nloc) for c in _walk(_MONOLOG).classes}
    assert classes == {
        "PHPConsoleHandler": (1, 23),
        "PsrLogMessageProcessor": (1, 24),
        "NativeMailerHandler": (1, 7),
        # The docblock and the blank lines are not code.
        "DoctrineCouchDBHandler": (3, 17),
    }
