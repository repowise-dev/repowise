"""Sample Python package for repowise integration tests.

This package provides a simple calculator with data models and utilities.
It is intentionally small but realistic — covering classes, functions,
imports, type annotations, and docstrings.
"""

from python_pkg.calculator import Calculator, add, divide, multiply, subtract
from python_pkg.models import CalculationResult, Operation

__all__ = [
    "CalculationResult",
    "Calculator",
    "Operation",
    "add",
    "divide",
    "multiply",
    "subtract",
]

__version__ = "1.0.0"
