"""Utility functions shared across the sample calculator package.

These helpers are used by calculator.py and are intentionally kept
separate to demonstrate inter-module dependency detection.
"""

from __future__ import annotations

import math


def is_close_to_zero(value: float, tolerance: float = 1e-10) -> bool:
    """Check whether a float is close enough to zero to be treated as zero.

    Used internally to prevent division-by-zero errors.

    Args:
        value:     The number to check.
        tolerance: Maximum absolute value considered "close to zero".
                   Defaults to 1e-10 (suitable for most floating-point arithmetic).

    Returns:
        True if abs(value) <= tolerance, False otherwise.
    """
    return abs(value) <= tolerance


def round_result(value: float, decimal_places: int = 10) -> float:
    """Round a float to avoid floating-point representation artifacts.

    Args:
        value:          The float to round.
        decimal_places: Number of decimal places to keep. Defaults to 10.

    Returns:
        The rounded float value.
    """
    return round(value, decimal_places)


def format_number(value: float, max_decimals: int = 6) -> str:
    """Format a float as a clean string, stripping unnecessary trailing zeros.

    Args:
        value:        The number to format.
        max_decimals: Maximum decimal places shown. Defaults to 6.

    Returns:
        A clean string representation. For example:
        - 3.0 → "3"
        - 3.14159265358979 → "3.141593" (with max_decimals=6)
    """
    formatted = f"{value:.{max_decimals}f}"
    # Strip trailing zeros after decimal point
    if "." in formatted:
        formatted = formatted.rstrip("0").rstrip(".")
    return formatted


def clamp(value: float, min_val: float, max_val: float) -> float:
    """Clamp a value to [min_val, max_val].

    Args:
        value:   The value to clamp.
        min_val: Lower bound (inclusive).
        max_val: Upper bound (inclusive).

    Returns:
        value if within range, otherwise the nearest bound.

    Raises:
        ValueError: If min_val > max_val.
    """
    if min_val > max_val:
        raise ValueError(f"min_val ({min_val}) must be <= max_val ({max_val})")
    return max(min_val, min(value, max_val))


def safe_sqrt(value: float) -> float:
    """Compute the square root, raising on negative input.

    Args:
        value: A non-negative number.

    Returns:
        The square root of value.

    Raises:
        ValueError: If value is negative.
    """
    if value < 0:
        raise ValueError(f"Cannot take square root of negative number: {value}")
    return math.sqrt(value)
