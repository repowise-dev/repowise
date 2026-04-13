<?php
namespace Sample\Models;

/** Supported operations. */
enum Operation
{
    case Add;
    case Subtract;
}

/** A calculation record. */
class CalculationRecord
{
    public function __construct(
        public Operation $operation,
        public float $x,
        public float $y,
        public float $result,
    ) {}
}

interface Computable
{
    public function compute(): float;
}
