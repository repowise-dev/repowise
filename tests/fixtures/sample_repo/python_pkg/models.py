"""Data models for the sample calculator package.

Defines the core domain objects used across the package.
All models use Python dataclasses for simplicity and immutability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Operation(Enum):
    """Supported arithmetic operations."""

    ADD = "add"
    SUBTRACT = "subtract"
    MULTIPLY = "multiply"
    DIVIDE = "divide"


@dataclass(frozen=True)
class CalculationResult:
    """The result of a single arithmetic calculation.

    Attributes:
        operation:  The operation that was performed.
        operands:   The input values, in order.
        result:     The computed result.
        timestamp:  When the calculation was performed.
    """

    operation: Operation
    operands: list[float]
    result: float
    timestamp: datetime = field(default_factory=datetime.utcnow)

    @property
    def summary(self) -> str:
        """Human-readable one-line summary of the calculation."""
        ops_str = f" {self.operation.value} ".join(str(x) for x in self.operands)
        return f"{ops_str} = {self.result}"


@dataclass
class CalculationHistory:
    """Mutable log of all calculations performed by a Calculator instance.

    Attributes:
        entries: Ordered list of CalculationResult objects.
    """

    entries: list[CalculationResult] = field(default_factory=list)

    def append(self, result: CalculationResult) -> None:
        """Add a result to the history."""
        self.entries.append(result)

    def clear(self) -> None:
        """Remove all entries from the history."""
        self.entries.clear()

    @property
    def total_calculations(self) -> int:
        """Total number of calculations recorded."""
        return len(self.entries)

    def last(self) -> CalculationResult | None:
        """Return the most recent calculation, or None if history is empty."""
        return self.entries[-1] if self.entries else None
