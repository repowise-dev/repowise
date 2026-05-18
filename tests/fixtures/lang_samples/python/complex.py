def many_branches(a, b, c, d, e):
    if a:
        return 1
    elif b:
        return 2
    elif c:
        return 3
    elif d:
        return 4
    elif e:
        return 5
    if a and b:
        return 6
    if c or d:
        return 7
    if a and c and e:
        return 8
    for i in range(10):
        if i:
            continue
    return 0
