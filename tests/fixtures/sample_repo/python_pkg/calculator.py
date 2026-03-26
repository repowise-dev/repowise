"""Core arithmetic operations for the sample calculator package.

Provides both module-level functions and a stateful Calculator class.
The Calculator class maintains a history of all calculations performed.

This file is intentionally written to exercise the AST parser:
    - Module-level functions (add, subtract, multiply, divide)
    - A class with methods and properties (Calculator)
    - Type annotations on all signatures
    - Docstrings on all public symbols
    - Imports from sibling modules (models, utils)
    - Custom exception classes
"""

from __future__ import annotations

from python_pkg.models import CalculationHistory, CalculationResult, Operation
from python_pkg.utils import is_close_to_zero, round_result


class DivisionByZeroError(ArithmeticError):
    """Raised when a division by zero is attempted.

    Separate from Python's built-in ZeroDivisionError to allow callers
    to catch repowise-domain errors independently of system errors.
    """


def add(x: float, y: float) -> float:
    """Return the sum of x and y.

    Args:
        x: First operand.
        y: Second operand.

    Returns:
        x + y
    """
    return round_result(x + y)


def subtract(x: float, y: float) -> float:
    """Return the difference of x and y (x - y).

    Args:
        x: Minuend.
        y: Subtrahend.

    Returns:
        x - y
    """
    return round_result(x - y)


def multiply(x: float, y: float) -> float:
    """Return the product of x and y.

    Args:
        x: First factor.
        y: Second factor.

    Returns:
        x * y
    """
    return round_result(x * y)


def divide(x: float, y: float) -> float:
    """Return the quotient of x divided by y.

    Args:
        x: Dividend.
        y: Divisor. Must not be zero.

    Returns:
        x / y

    Raises:
        DivisionByZeroError: If y is zero or very close to zero.
    """
    if is_close_to_zero(y):
        raise DivisionByZeroError(f"Cannot divide {x} by zero (got y={y})")
    return round_result(x / y)


class Calculator:
    """Stateful calculator that records the history of all calculations.

    The Calculator wraps the module-level arithmetic functions and
    records each operation as a CalculationResult in its history.

    Example:
        calc = Calculator()
        result = calc.add(10, 5)   # 15.0
        result = calc.divide(10, 4) # 2.5
        print(calc.history.total_calculations)  # 2
        print(calc.last_result.summary)         # "10 divide 4 = 2.5"
    """

    def __init__(self) -> None:
        self._history = CalculationHistory()

    @property
    def history(self) -> CalculationHistory:
        """The full history of calculations performed by this instance."""
        return self._history

    @property
    def last_result(self) -> CalculationResult | None:
        """The most recent CalculationResult, or None if no calculations yet."""
        return self._history.last()

    def add(self, x: float, y: float) -> float:
        """Add x and y, record the result in history.

        Args:
            x: First operand.
            y: Second operand.

        Returns:
            The sum x + y.
        """
        value = add(x, y)
        self._history.append(
            CalculationResult(operation=Operation.ADD, operands=[x, y], result=value)
        )
        return value

    def subtract(self, x: float, y: float) -> float:
        """Subtract y from x, record the result in history.

        Args:
            x: Minuend.
            y: Subtrahend.

        Returns:
            The difference x - y.
        """
        value = subtract(x, y)
        self._history.append(
            CalculationResult(
                operation=Operation.SUBTRACT, operands=[x, y], result=value
            )
        )
        return value

    def multiply(self, x: float, y: float) -> float:
        """Multiply x by y, record the result in history.

        Args:
            x: First factor.
            y: Second factor.

        Returns:
            The product x * y.
        """
        value = multiply(x, y)
        self._history.append(
            CalculationResult(
                operation=Operation.MULTIPLY, operands=[x, y], result=value
            )
        )
        return value

    def divide(self, x: float, y: float) -> float:
        """Divide x by y, record the result in history.

        Args:
            x: Dividend.
            y: Divisor. Must not be zero.

        Returns:
            The quotient x / y.

        Raises:
            DivisionByZeroError: If y is zero.
        """
        value = divide(x, y)
        self._history.append(
            CalculationResult(
                operation=Operation.DIVIDE, operands=[x, y], result=value
            )
        )
        return value

    def clear_history(self) -> None:
        """Remove all entries from the calculation history."""
        self._history.clear()
