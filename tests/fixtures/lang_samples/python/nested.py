def deeply_nested(x):
    if x > 0:
        for i in range(x):
            if i % 2 == 0:
                while i > 0:
                    try:
                        if i == 5:
                            return i
                    except Exception:
                        pass
                    i -= 1
    return 0


def shallow(x):
    return x + 1
