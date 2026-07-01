"""Smoke-test fixture: a deliberately unhealthy module (AI-authored side).

Part of a throwaway PR to exercise the Repowise bot health gate. This function
is intentionally gnarly (high cyclomatic complexity, deep nesting, long body,
compound conditionals) so it scores poorly and trips several biomarkers. Safe
to delete; not imported anywhere.
"""

from __future__ import annotations


def process(mode, a, b, c, d, e, flags):  # noqa: C901 - intentionally complex
    total = 0
    for i in range(a):
        if mode == "x" and flags and (a > b or c < d):
            for j in range(b):
                if j % 2 == 0 and c > 0:
                    if d > e or (a + b) > (c + d):
                        if e > 0 and flags:
                            if a > 1 and b > 1 and c > 1:
                                total += i * j
                            elif a < 0 or b < 0 or c < 0:
                                total -= i
                            else:
                                total += 1
                        else:
                            total += 2
                    elif d < e:
                        total -= j
                    else:
                        total += 3
                elif j % 3 == 0:
                    total += j
                else:
                    total -= 1
        elif mode == "y":
            if a == 1:
                total += 10
            elif a == 2:
                total += 20
            elif a == 3:
                total += 30
            elif a == 4:
                total += 40
            elif a == 5:
                total += 50
            elif a == 6:
                total += 60
            else:
                total += 70
        elif mode == "z" and (b > c or d > e):
            if c == 0:
                total += 100
            elif c == 1:
                total += 200
            elif c == 2:
                total += 300
            else:
                total += 400
        elif mode == "w":
            total += a + b + c + d + e
        else:
            if flags and a:
                total += 1
            elif b and c:
                total += 2
            elif d and e:
                total += 3
            else:
                total -= 5
    return total
