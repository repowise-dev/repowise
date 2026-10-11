<?php
namespace Sample;

use Sample\Models\Operation;
use Sample\Models\CalculationRecord;

/** Calculator for basic arithmetic. */
class Calculator extends BaseCalc implements Computable
{
    public function add(float $x, float $y): float
    {
        return $x + $y;
    }

    public function subtract(float $x, float $y): float
    {
        return $x - $y;
    }

    private function helper(): void {}
}
