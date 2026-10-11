"""Fixtures for class-level (LCOM4 / god-class) walker tests."""


class Cohesive:
    """All methods collaborate around shared state → LCOM4 == 1."""

    def __init__(self):
        self.total = 0
        self.count = 0

    def add(self, n):
        self.total += n
        self.count += 1

    def average(self):
        return self.total / self.count if self.count else 0

    def reset(self):
        self.total = 0
        self.count = 0

    def describe(self):
        return f"{self.count} items, avg {self.average()}"


class Splintered:
    """Two disjoint method clusters plus a loner → LCOM4 == 3.

    {set_a, get_a} share self.a; {set_b, get_b} share self.b; loner
    touches neither. Fields are class-level so no constructor bridges the
    clusters.
    """

    a = 0
    b = 0

    def set_a(self, v):
        self.a = v

    def get_a(self):
        return self.a

    def set_b(self, v):
        self.b = v

    def get_b(self):
        return self.b

    def loner(self):
        return 42
