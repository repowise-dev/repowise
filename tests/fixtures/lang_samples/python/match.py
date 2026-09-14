# Python match/case is pattern matching — each case is a distinct branch
# point, structurally the same decision as if/elif. Unlike a flat Rust
# match (or C-style switch), the arms must count toward CCN.
#
# python_match 3 arms -> base 1 + 3 cases = 4
def python_match(x):
    match x:
        case 1:
            return "one"
        case 2:
            return "two"
        case 3:
            return "three"
        case _:
            return "other"


# Equivalent if/elif — must report the same CCN as python_match above.
def python_if_elif(x):
    if x == 1:
        return "one"
    elif x == 2:
        return "two"
    elif x == 3:
        return "three"
    else:
        return "other"
